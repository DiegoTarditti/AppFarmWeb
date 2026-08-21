"""Calculo de sugerido con ajuste estacional para /pedido/prueba.

Modelo distinto a /pedido/dia:
- Pedido/dia (reposicion tactica) usa u3m/90 como daily_rate (ritmo
  reciente). Sirve para reponer lo que se vendio en el ultimo trimestre.
- Pedido/prueba (planificacion grande) usa u12m/365 como base "neutra"
  y aplica el indice estacional del MES OBJETIVO como multiplicador.
  Asi no se doble-cuenta la estacionalidad: el numerador es plano y el
  multiplicador hace todo el ajuste.

Formula:
    ritmo_diario = (u12m / 365) * indice_estacional[mes_objetivo]
    demanda      = ritmo_diario * cobertura_dias
    sugerido     = max(0, demanda - stock_actual)

mes_objetivo = hoy + lead_time_dias (por default, override por producto).

Escenario aplicable (en orden de precedencia):
    1. Escenario "Generico" del producto especifico (producto_id NOT NULL)
    2. Escenario "Generico" de la droga (producto_id NULL)
    3. Calculo automatico (sin escenario): indices=1.0/mes, lead=0, cob=30
"""

import json
from datetime import date as _date
from datetime import timedelta

from sqlalchemy import and_, or_
from sqlalchemy import func as _func

from database import (
    EstacionalidadEscenario,
    EstacionalidadProducto,
    ObsCodigoBarras,
    ObsProducto,
    ObsStock,
    ObsVentaMensual,
    ProductoFlag,
    ProductoPrecioHist,
    TipoPedidoConfig,
)

# Default por si no hay escenario ni para producto ni para droga.
_DEFAULT_INDICES = [1.0] * 12
_ESCENARIO_NOMBRE = 'Generico'  # convencion v1: 1 solo escenario por droga/producto

# ────── Source of truth UNICO de los limites lead/cobertura ──────
# Todos los validadores backend, los sliders/inputs del frontend y los JS
# leen de aca. Si querés cambiar algun limite, lo haces en UN solo lugar
# y se propaga al template via Jinja (route lo pasa como `limites=LIMITES`)
# y al JS via <script type="application/json" id="limites">.
LIMITES = {
    # Lead time (dias entre pedido y entrega del proveedor)
    'lead_dias_piso':    2,    # piso operativo: ningun proveedor entrega instantaneo
    'lead_dias_default': 2,    # valor inicial cuando no hay escenario configurado
    'lead_dias_max':     180,  # tope razonable (~6 meses)

    # Cobertura (dias de demanda a cubrir con cada pedido)
    'cob_dias_min':      1,
    'cob_dias_default':  30,   # 1 mes tipico
    'cob_dias_max':      365,  # tope razonable (1 anio)
}

# Aliases retrocompat (no usar en codigo nuevo, usar LIMITES['...']).
LEAD_DIAS_PISO = LIMITES['lead_dias_piso']
_DEFAULT_LEAD_DIAS = LIMITES['lead_dias_default']
_DEFAULT_COBERTURA_DIAS = LIMITES['cob_dias_default']


MESES_ES = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun',
            'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']


def obtener_escenarios_bulk(session, droga_ids, producto_ids):
    """Carga los escenarios relevantes para un conjunto de drogas y
    asignaciones producto→escenario en 2 queries. Devuelve dict:

        {
            ('producto', producto_observer_id): EstacionalidadEscenario,
            ('droga', droga_id): EstacionalidadEscenario,
        }

    Lógica (alineada con el modelo de main, post commit 8f84f5f):
    - Los escenarios siempre se crean a nivel droga (EstacionalidadEscenario
      sin producto_id — solo droga_id + es_default).
    - La asignación a un producto puntual va en la tabla intermedia
      EstacionalidadProducto que apunta a un escenario droga existente.
    """
    out = {}
    droga_ids_list = list(droga_ids or [])
    producto_ids_list = list(producto_ids or [])

    if droga_ids_list:
        # Escenarios de droga (preferimos los es_default; si no, el ultimo).
        rows = (session.query(EstacionalidadEscenario)
                .filter(EstacionalidadEscenario.droga_id.in_(droga_ids_list))
                .order_by(EstacionalidadEscenario.es_default.desc(),
                          EstacionalidadEscenario.actualizado_en.desc())
                .all())
        for e in rows:
            key = ('droga', e.droga_id)
            if key not in out:  # primer match = el preferido
                out[key] = e

    if producto_ids_list:
        # Asignaciones producto → escenario via tabla intermedia.
        rows = (session.query(EstacionalidadProducto, EstacionalidadEscenario)
                .join(EstacionalidadEscenario,
                      EstacionalidadEscenario.id == EstacionalidadProducto.escenario_id)
                .filter(EstacionalidadProducto.producto_observer_id.in_(producto_ids_list))
                .all())
        for asig, esc in rows:
            out[('producto', asig.producto_observer_id)] = esc

    return out


def obtener_flags_bulk(session, eans, lab_id=None):
    """Devuelve dict ean → (ProductoFlag, TipoPedidoConfig).
    Si lab_id se pasa, agrega tambien el flag por lab bajo la key
    ('lab', lab_id).
    """
    out = {}
    if eans:
        flags = (session.query(ProductoFlag)
                 .filter(ProductoFlag.ean.in_(list(eans)))
                 .all())
        slugs = list({f.flag_slug for f in flags})
        cfgs = {c.slug: c for c in (session.query(TipoPedidoConfig)
                                    .filter(TipoPedidoConfig.slug.in_(slugs),
                                            TipoPedidoConfig.categoria == 'flag')
                                    .all())} if slugs else {}
        for f in flags:
            out[f.ean] = (f, cfgs.get(f.flag_slug))
    if lab_id is not None:
        f_lab = (session.query(ProductoFlag)
                 .filter(ProductoFlag.ean.is_(None),
                         ProductoFlag.laboratorio_id == lab_id)
                 .first())
        if f_lab:
            cfg = (session.query(TipoPedidoConfig)
                   .filter_by(slug=f_lab.flag_slug, categoria='flag').first())
            out[('lab', lab_id)] = (f_lab, cfg)
    return out


def obtener_ventas_arr_bulk(session, producto_ids, id_farmacia, hoy=None):
    """Devuelve dict producto_id → ventas_arr[12] con [0]=mas antiguo,
    [11]=mes actual parcial. En 1 query.
    """
    if not producto_ids:
        return {}
    if hoy is None:
        hoy = _date.today()
    # Calculo los 12 meses (anio, mes) tuples
    pares = []
    anio, mes = hoy.year, hoy.month
    for _ in range(12):
        pares.append((anio, mes))
        mes -= 1
        if mes <= 0:
            mes = 12
            anio -= 1
    pares.reverse()
    pares_idx = {p: i for i, p in enumerate(pares)}
    anio_min = pares[0][0]
    mes_min = pares[0][1]
    cutoff = anio_min * 100 + mes_min  # YYYYMM

    rows = (session.query(ObsVentaMensual.producto_observer,
                          ObsVentaMensual.anio,
                          ObsVentaMensual.mes,
                          ObsVentaMensual.unidades)
            .filter(ObsVentaMensual.producto_observer.in_(list(producto_ids)),
                    ObsVentaMensual.id_farmacia == id_farmacia,
                    (ObsVentaMensual.anio * 100 + ObsVentaMensual.mes) >= cutoff)
            .all())
    out = {pid: [0.0] * 12 for pid in producto_ids}
    for r in rows:
        idx = pares_idx.get((r.anio, r.mes))
        if idx is not None and r.producto_observer in out:
            out[r.producto_observer][idx] = float(r.unidades or 0)
    return out


def obtener_escenario_aplicable(session, droga_id, producto_id,
                                escenarios_bulk=None):
    """Devuelve (escenario_obj, origen) donde origen es:
    'producto' | 'droga' | 'auto'.

    Si `escenarios_bulk` (dict de obtener_escenarios_bulk) se pasa, lee
    de ahi en O(1) sin tocar la DB. Esto elimina N+1 cuando se procesan
    muchos productos.

    Si no hay escenario en ningun nivel, devuelve (None, 'auto').
    """
    if escenarios_bulk is not None:
        if producto_id is not None:
            esc = escenarios_bulk.get(('producto', producto_id))
            if esc:
                return esc, 'producto'
        if droga_id is not None:
            esc = escenarios_bulk.get(('droga', droga_id))
            if esc:
                return esc, 'droga'
        return None, 'auto'

    # Path lento (queries individuales) — solo cuando no se pasa bulk.
    # 1. Asignacion explicita producto → escenario via EstacionalidadProducto.
    if producto_id is not None:
        row = (session.query(EstacionalidadProducto, EstacionalidadEscenario)
               .join(EstacionalidadEscenario,
                     EstacionalidadEscenario.id == EstacionalidadProducto.escenario_id)
               .filter(EstacionalidadProducto.producto_observer_id == producto_id)
               .first())
        if row:
            return row[1], 'producto'

    # 2. Escenario default de la droga (es_default=True; si no, ultimo creado).
    if droga_id is not None:
        esc = (session.query(EstacionalidadEscenario)
               .filter(EstacionalidadEscenario.droga_id == droga_id)
               .order_by(EstacionalidadEscenario.es_default.desc(),
                         EstacionalidadEscenario.actualizado_en.desc())
               .first())
        if esc:
            return esc, 'droga'

    return None, 'auto'


def obtener_precios_publicos_bulk(session, eans):
    """Devuelve dict {ean: precio_publico} con el precio MAS RECIENTE
    por EAN (fecha de factura mas alta). Para EANs sin precio, no aparecen
    en el dict.

    Diseñado para llamarse 1 vez por endpoint con TODOS los eans de un lab
    a la vez (en lugar de 1 query por producto = N+1).
    """
    if not eans:
        return {}
    # Subquery: por cada EAN, fecha maxima.
    sq = (session.query(
            ProductoPrecioHist.codigo_barra.label('ean'),
            _func.max(ProductoPrecioHist.fecha).label('max_fecha'))
        .filter(ProductoPrecioHist.codigo_barra.in_(eans),
                ProductoPrecioHist.precio_publico.isnot(None),
                ProductoPrecioHist.precio_publico > 0)
        .group_by(ProductoPrecioHist.codigo_barra)
        .subquery())
    rows = (session.query(
            ProductoPrecioHist.codigo_barra,
            ProductoPrecioHist.precio_publico)
        .join(sq, and_(ProductoPrecioHist.codigo_barra == sq.c.ean,
                       ProductoPrecioHist.fecha == sq.c.max_fecha))
        .filter(ProductoPrecioHist.precio_publico.isnot(None))
        .all())
    out = {}
    for r in rows:
        # Si hay multiple filas con misma fecha y ean, se queda la primera.
        if r.codigo_barra not in out:
            out[r.codigo_barra] = float(r.precio_publico)
    return out


def obtener_eans_producto(session, producto_observer_id):
    """Devuelve la lista de EANs activos del producto (principal +
    alternativos, ordenados por `orden` 1..N, excluyendo dados de baja)."""
    rows = (session.query(ObsCodigoBarras.codigo_barras)
            .filter(ObsCodigoBarras.producto_observer == producto_observer_id,
                    ObsCodigoBarras.fecha_baja.is_(None))
            .order_by(ObsCodigoBarras.orden)
            .all())
    return [r.codigo_barras for r in rows]


def obtener_flag_producto(session, eans, laboratorio_id=None):
    """Devuelve (flag_producto, flag_config) o (None, None).

    Busca PRIMERO por cualquiera de los EANs del producto (principal +
    alternativos); si no hay match y se pasa laboratorio_id, busca por
    lab (flag aplicado a todo un laboratorio). El flag por EAN siempre
    gana al flag por lab si ambos existen.

    Args:
        eans: lista de EANs del producto (puede ser vacia).
        laboratorio_id: int opcional, id del laboratorio del producto.
    """
    if not eans and not laboratorio_id:
        return None, None

    # Buscar por EAN primero (preferencia maxima).
    if eans:
        flag = (session.query(ProductoFlag)
                .filter(ProductoFlag.ean.in_(eans))
                .first())
        if flag:
            cfg_row = (session.query(TipoPedidoConfig)
                       .filter_by(slug=flag.flag_slug, categoria='flag')
                       .first())
            return flag, cfg_row

    # Fallback: por lab.
    if laboratorio_id:
        flag = (session.query(ProductoFlag)
                .filter(ProductoFlag.ean.is_(None),
                        ProductoFlag.laboratorio_id == laboratorio_id)
                .first())
        if flag:
            cfg_row = (session.query(TipoPedidoConfig)
                       .filter_by(slug=flag.flag_slug, categoria='flag')
                       .first())
            return flag, cfg_row

    return None, None


def mes_objetivo_default(lead_dias, hoy=None):
    """Devuelve (mes 1-12, anio, label 'Mes YYYY') sumando lead_dias a hoy."""
    if hoy is None:
        hoy = _date.today()
    objetivo = hoy + timedelta(days=int(lead_dias or 0))
    return objetivo.month, objetivo.year, f'{MESES_ES[objetivo.month - 1]} {objetivo.year}'


def calcular_sugerido_estacional(session, producto, u12m, stock_actual,
                                 minimo=0, override_mes_obj=None, hoy=None,
                                 lead_default=None, cob_default=None,
                                 escenarios_bulk=None, eans_producto=None,
                                 flags_bulk=None, lab_id_hint=None):
    """Calculo principal. Devuelve dict con todo el desglose.

    Args:
        session: SQLAlchemy session abierta.
        producto: instancia de ObsProducto (debe tener observer_id,
            nombre_droga_observer, laboratorio_observer).
        u12m: int|float, unidades vendidas en los ultimos 12 meses.
        stock_actual: int, stock actual (ObsStock).
        minimo: int, minimo de ObServer (puede ser 0).
        override_mes_obj: int|None, mes 1-12. Si se pasa, lo usa en lugar
            del calculado por lead_time.
        hoy: date|None para tests (default = date.today()).

    Returns:
        dict con todas las variables del calculo (ver formato abajo).
    """
    droga_id = producto.nombre_droga_observer
    producto_id = producto.observer_id

    # 1. Escenario aplicable (con bulk_map si se paso).
    esc, origen = obtener_escenario_aplicable(
        session, droga_id, producto_id, escenarios_bulk=escenarios_bulk)
    if esc:
        indices = json.loads(esc.indices_json)
        # Clipear al piso operativo aun si la DB tiene un valor mas bajo
        # (puede pasar con escenarios viejos guardados con lead=0).
        lead_dias = max(LEAD_DIAS_PISO, int(esc.lead_time_dias or 0))
        cob_dias = int(esc.cobertura_dias or 30)
        escenario_nombre = esc.nombre
    else:
        # Sin escenario: usar defaults pasados por el caller (cabecera del
        # /pedido/prueba), o los de modulo si no se pasan.
        indices = list(_DEFAULT_INDICES)
        lead_dias = max(LEAD_DIAS_PISO,
                        int(lead_default) if lead_default is not None
                        else _DEFAULT_LEAD_DIAS)
        cob_dias = max(1, int(cob_default) if cob_default is not None
                          else _DEFAULT_COBERTURA_DIAS)
        escenario_nombre = None

    # 2. Mes objetivo.
    if override_mes_obj is not None:
        # Mantener anio actual; si el mes ya paso este anio, usar el proximo.
        if hoy is None:
            hoy = _date.today()
        anio_obj = hoy.year if override_mes_obj >= hoy.month else hoy.year + 1
        mes_obj = int(override_mes_obj)
        mes_obj_label = f'{MESES_ES[mes_obj - 1]} {anio_obj}'
    else:
        mes_obj, anio_obj, mes_obj_label = mes_objetivo_default(lead_dias, hoy=hoy)

    indice_aplicado = float(indices[mes_obj - 1])

    # 3. Calculo principal.
    u12m_f = float(u12m or 0)
    ritmo_diario_base = u12m_f / 365.0
    ritmo_diario = ritmo_diario_base * indice_aplicado
    demanda_proyectada = ritmo_diario * cob_dias

    # Sugerido base: cubrir la demanda menos lo que ya hay.
    sugerido_base = max(0, int(round(demanda_proyectada - (stock_actual or 0))))

    # Si el sugerido base no llega ni al minimo, subir al minimo (resguardo).
    if minimo and minimo > 0:
        deficit_minimo = max(0, int(minimo) - int(stock_actual or 0))
        sugerido_base = max(sugerido_base, deficit_minimo)

    # 4. Flag de comportamiento excepcional (busca por EAN principal +
    #    alternativos del producto; fallback a lab).
    if eans_producto is not None:
        eans = eans_producto
    else:
        eans = obtener_eans_producto(session, producto_id)
    lab_id_effective = lab_id_hint if lab_id_hint is not None else producto.laboratorio_observer

    if flags_bulk is not None:
        # Lookup en bulk_map.
        flag_obj, flag_cfg = None, None
        for ean in eans:
            if ean in flags_bulk:
                flag_obj, flag_cfg = flags_bulk[ean]
                break
        if flag_obj is None and ('lab', lab_id_effective) in flags_bulk:
            flag_obj, flag_cfg = flags_bulk[('lab', lab_id_effective)]
    else:
        flag_obj, flag_cfg = obtener_flag_producto(
            session, eans=eans, laboratorio_id=lab_id_effective,
        )
    flag_dict = None
    excluido_por_flag = False
    sugerido_final = sugerido_base
    if flag_obj and flag_cfg:
        cfg_json = json.loads(flag_cfg.config_json or '{}')
        flag_dict = {
            'slug': flag_obj.flag_slug,
            'nombre': flag_cfg.nombre,
            'efecto_armado': cfg_json.get('efecto_armado', 'ninguno'),
            'icono': cfg_json.get('icono', ''),
            'color': cfg_json.get('color', 'gray'),
        }
        if flag_dict['efecto_armado'] == 'excluir':
            sugerido_final = 0
            excluido_por_flag = True
        elif flag_dict['efecto_armado'] == 'badge_cero':
            sugerido_final = 0

    # 5. Razon humana.
    if u12m_f <= 0:
        razon = 'Sin ventas 12m → sin sugerido.'
        sugerido_final = 0
    elif excluido_por_flag:
        razon = f'Excluido por flag {flag_dict["slug"]}.'
    else:
        razon = (
            f'u12m {int(u12m_f)}/365 = {ritmo_diario_base:.2f} u/d base. '
            f'× indice {mes_obj_label} ({indice_aplicado:.2f}) '
            f'= {ritmo_diario:.2f} u/d. '
            f'× cobertura {cob_dias}d = {demanda_proyectada:.0f}u demanda. '
            f'- stock {stock_actual or 0}u = {sugerido_base}u sugerido.'
        )

    return {
        # Identidad
        'producto_observer_id': producto_id,
        'producto_nombre': producto.descripcion,
        'droga_id': droga_id,
        # Estado base
        'stock_actual': int(stock_actual or 0),
        'minimo': int(minimo or 0),
        'u12m': int(u12m_f),
        # Escenario
        'origen_escenario': origen,
        'escenario_nombre': escenario_nombre,
        'indices': indices,
        'lead_dias': lead_dias,
        'cobertura_dias': cob_dias,
        # Mes objetivo
        'mes_objetivo': mes_obj,
        'mes_objetivo_anio': anio_obj,
        'mes_objetivo_label': mes_obj_label,
        'indice_aplicado': indice_aplicado,
        # Calculo
        'ritmo_diario_base': round(ritmo_diario_base, 3),
        'ritmo_diario': round(ritmo_diario, 3),
        'demanda_proyectada': int(round(demanda_proyectada)),
        'sugerido_base': sugerido_base,
        'sugerido_final': sugerido_final,
        # Flag
        'flag': flag_dict,
        'excluido_por_flag': excluido_por_flag,
        # Diagnostico
        'razon': razon,
    }


# ──────────────────────────────────────────────────────────────────────
#  Calculo de sugerido para /pedido/dia (REPOSICION tactica)
#
#  Replica fiel de la logica de routes/compras_dia.py:760-820 para 1
#  producto. Sirve para mostrar la columna "Día act." en /pedido/prueba
#  comparando con el calculo estacional.
#
#  Asunciones (vs. compras_dia.py que tiene mas contexto):
#  - factor_h=1.0 (no hay drog asignada → sin ponderación por hora cierre).
#  - cubrir_dias=30 (no hay slider de pantalla).
#  - meses_rotacion=3 (default de compras_dia, configurable hasta 12).
#  - pack_quantity=None (no se lee de Producto local).
#  - cantidad_reposicion_fija=None (no se lee de Producto local).
# ──────────────────────────────────────────────────────────────────────

_DIAS_PROM_MES = 30.42  # mismo valor que compras_dia.py
_MESES_ROTACION_DEFAULT = 3


def _obtener_ventas_arr_12m(session, producto_observer_id, id_farmacia, hoy=None):
    """Devuelve array de 12 floats con ventas mensuales [0]=mas antiguo,
    [11]=mes actual (parcial). Llena con 0 los meses sin data."""
    if hoy is None:
        hoy = _date.today()
    # 12 meses incluyendo el actual.
    pares = []
    anio, mes = hoy.year, hoy.month
    for _ in range(12):
        pares.append((anio, mes))
        mes -= 1
        if mes <= 0:
            mes = 12
            anio -= 1
    pares.reverse()  # ahora [0]=mas antiguo, [11]=actual

    pares_set = {(a, m) for a, m in pares}
    rows = (session.query(ObsVentaMensual.anio, ObsVentaMensual.mes,
                          ObsVentaMensual.unidades)
            .filter(ObsVentaMensual.producto_observer == producto_observer_id,
                    ObsVentaMensual.id_farmacia == id_farmacia)
            .all())
    por_mes = {(r.anio, r.mes): float(r.unidades or 0)
               for r in rows if (r.anio, r.mes) in pares_set}
    return [por_mes.get((a, m), 0.0) for a, m in pares]


def calcular_sugerido_dia_actual(session, producto_observer_id, id_farmacia,
                                 meses_rotacion=_MESES_ROTACION_DEFAULT,
                                 factor_h=1.0, cubrir_dias=30,
                                 stock_actual=None, min_actual=None,
                                 hoy=None, ventas_arr_bulk=None):
    """Replica fiel del calculo de /pedido/dia (REPOSICION) para 1 producto.

    Devuelve int (a_pedir) o None si no se puede calcular.

    Args:
        session: SQLAlchemy session.
        producto_observer_id: int.
        id_farmacia: int.
        meses_rotacion: int 1-12 (default 3, igual que compras_dia).
        factor_h: float (default 1.0).
        cubrir_dias: int (default 30).
        stock_actual: int (opcional; si no se pasa, se busca de ObsStock).
        min_actual: int (opcional; si no se pasa, se busca de ObsStock).
        hoy: date para tests (default = date.today()).
    """
    try:
        from purchase_helpers import calcular_min_sugerido, clasificar_min
        from services.calculo_pedido import calcular_a_pedir, cargar_config
    except ImportError:
        return None

    if hoy is None:
        hoy = _date.today()

    # Si no vienen stock/min cargados, hago 1 query a ObsStock.
    if stock_actual is None or min_actual is None:
        st = (session.query(ObsStock.stock_actual, ObsStock.minimo)
              .filter(ObsStock.producto_observer == producto_observer_id,
                      ObsStock.id_farmacia == id_farmacia)
              .first())
        stock_actual = int((st and st.stock_actual) or 0)
        min_actual = int((st and st.minimo) or 0)

    if ventas_arr_bulk is not None:
        ventas_arr = ventas_arr_bulk.get(producto_observer_id, [0.0] * 12)
    else:
        ventas_arr = _obtener_ventas_arr_12m(
            session, producto_observer_id, id_farmacia, hoy=hoy)
    u12m_int = int(sum(ventas_arr))

    # min_sugerido + sin_mov (mismo helper que compras_dia).
    try:
        start_month = hoy.month  # aprox, compras_dia usa el del slider
        end_month = hoy.month
        min_sugerido, _avg_m, sin_mov, _tipo = calcular_min_sugerido(
            ventas_arr, stock_actual, start_month, end_month)
    except Exception:
        min_sugerido, sin_mov = 0, False

    if u12m_int == 0 or sin_mov:
        min_sugerencia = None
    else:
        min_sugerencia = clasificar_min(min_actual, min_sugerido)

    # u_rot: meses anteriores al actual (excluye parcial).
    _i_end = 11
    _i_start = max(0, _i_end - meses_rotacion)
    u_rot = sum(ventas_arr[_i_start:_i_end])
    dias_rotacion = int(meses_rotacion * _DIAS_PROM_MES)
    daily_rate = (u_rot / dias_rotacion) if dias_rotacion else 0

    # min_efectivo (correccion del minimo si esta desfasado).
    if min_sugerencia in ('up', 'down') and min_sugerido > 0:
        min_efectivo = min_sugerido
    else:
        min_efectivo = min_actual

    # Llamar al motor compartido.
    cfg = cargar_config('REPOSICION') or {}
    result = calcular_a_pedir(cfg, {
        'daily_rate': daily_rate,
        'min_efectivo': min_efectivo,
        'factor_h': factor_h,
        'cubrir_dias': cubrir_dias,
        'stock_actual': stock_actual,
        'cantidad_reposicion_fija': None,
        'pack_quantity': None,
        'u12m': u12m_int,
        'sin_mov': sin_mov,
    })
    return int(result.get('a_pedir', 0))
