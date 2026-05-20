"""Shared helpers, constants and utility functions used across route modules."""

import os
import re
from datetime import datetime, timedelta, timezone

import database
from database import Producto

AR_TZ = timezone(timedelta(hours=-3))

def now_ar():
    """Hora actual en Argentina (UTC-3), sin tzinfo para compatibilidad con SQLAlchemy DateTime."""
    return datetime.now(AR_TZ).replace(tzinfo=None)


def ventas_periodo_filter(modelo, desde, hasta, fecha_attr='fecha_estadistica'):
    """Filtro estándar para sumar VENTAS NETAS de ObsVentaDetalle en un período.

    Devuelve un `and_(...)` listo para usar en `.filter(...)`. Reemplaza el
    patrón duplicado de:

        ObsVentaDetalle.fecha_estadistica >= desde,
        ObsVentaDetalle.fecha_estadistica <= hasta,
        or_(ObsVentaDetalle.tipo_operacion == 'V', ObsVentaDetalle.tipo_operacion.is_(None))

    ════════════════════════════════════════════════════════════════════════
    IMPORTANTE — POR QUÉ NO FILTRA tipo_operacion:
    ════════════════════════════════════════════════════════════════════════
    Las devoluciones (`tipo_operacion = 'D'`) vienen de Observer con
    `cantidad` e `importe` NEGATIVOS. El `SUM()` neto descuenta solas las
    devoluciones — NO hay que filtrarlas. Filtrar `tipo == 'V'` excluiría
    las devoluciones y daría ventas BRUTAS en vez de NETAS (era el bug
    que tenían 13 lugares de la app hasta el fix de hoy).

    Sólo filtrá `tipo_operacion` cuando explícitamente querés ANALIZAR
    devoluciones como casos aparte (ej. `tipo_operacion.in_(('D','NC'))`
    para listar las devoluciones), nunca para "ventas".

    Args:
        modelo: ObsVentaDetalle (se pasa por param para no acoplar el helper
            al import directo del modelo).
        desde, hasta: date — extremos inclusivos.
        fecha_attr: 'fecha_estadistica' (default) o 'fecha_operacion' según el caller.

    Uso:
        from helpers import ventas_periodo_filter
        q = session.query(ObsVentaDetalle).filter(
            ventas_periodo_filter(ObsVentaDetalle, desde, hasta),
            ObsVentaDetalle.medico_observer == med_id,  # filtros propios
        )
    """
    from sqlalchemy import and_
    fecha_col = getattr(modelo, fecha_attr)
    return and_(fecha_col >= desde, fecha_col <= hasta)


def multi_token_filter(query_text, *columns):
    """Devuelve una cláusula SQLAlchemy multi-token AND para búsquedas.

    Splittea el query por espacios o '+' y arma `AND(OR(col.ilike(t) por col)
    por token)`. El usuario fue explícito: TODA búsqueda en el sistema debe
    soportar multi-token AND ("400 susp" → tiene 400 Y susp).

    Args:
        query_text: string del input del usuario.
        columns: una o más Column de SQLAlchemy donde buscar el token.

    Returns:
        - None si query_text está vacío o sin tokens (caller debe omitir filter).
        - Cláusula and_(...) lista para pasar a .filter().

    Ejemplo:
        q = request.args.get('q')
        clausula = multi_token_filter(q, Producto.descripcion, Producto.codigo_barra)
        if clausula is not None:
            base = base.filter(clausula)
    """
    from sqlalchemy import and_, or_
    if not query_text or not columns:
        return None
    tokens = [t for t in query_text.replace('+', ' ').split() if t]
    if not tokens:
        return None
    return and_(*[
        or_(*[col.ilike(f'%{t}%') for col in columns])
        for t in tokens
    ])


def detectar_entorno():
    """Detecta dónde está corriendo la app. Retorna dict con:
      - codigo: 'render' | 'local' | 'local_render_db'
      - label:  'PROD' | 'LOCAL' | 'LOCAL→PROD'
      - color:  color hex para el badge/topbar
    """
    # Render setea estas env vars automáticamente en sus servicios
    if os.environ.get('RENDER') or os.environ.get('RENDER_INSTANCE_ID'):
        return {'codigo': 'render', 'label': 'PROD', 'color': '#16A34A',
                'descripcion': 'Producción (Render)'}
    # Docker local apuntando a la DB de Render (escenario dev remoto)
    db_url = os.environ.get('DATABASE_URL', '')
    if 'render.com' in db_url or 'oregon-postgres' in db_url:
        return {'codigo': 'local_render_db', 'label': 'LOCAL→PROD', 'color': '#EF4444',
                'descripcion': 'Local apuntando a DB de Render (cuidado)'}
    return {'codigo': 'local', 'label': 'LOCAL', 'color': '#F59E0B',
            'descripcion': 'Docker local'}

# ── Constants ────────────────────────────────────────────────────────────────

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
PARSERS_FOLDER = os.path.join(os.path.dirname(__file__), 'parsers')
ALLOWED_EXTENSIONS = {'pdf', 'xlsx', 'xls'}
CONVERTER_DIR = os.path.join(UPLOAD_FOLDER, 'converter')
PURCHASE_FOLDER = os.path.join(UPLOAD_FOLDER, 'purchase')

# Las 3 entidades unificadas (ver project_entidades.md)
PARTNER_TIPOS = ('laboratorio', 'drogueria', 'proveedor')
PLANTILLA_FORMATOS = ('xlsx', 'txt_fijo', 'csv')
PLANTILLA_TIPOS_DOC = ('pedido', 'recepcion', 'descuento')

# Patrones de descripcion de items que NO son medicamentos pero aparecen como
# productos en Observer (servicios de farmacia: sellado de recetas, cupones, etc.).
# Se excluyen del análisis de stock/pedido/forecast.
NO_MEDICAMENTO_PATTERNS = (
    '%sellado%receta%',     # "SELLADO DE RECETAS"
    '%costo%receta%',       # "Costo Receta/Cupón"
    'sellado',              # entry genérica id=1 de Observer (descripcion exacta)
)


def filtro_solo_medicamentos(query, ObsProducto):
    """Aplica NOT LIKE para excluir items no-medicamento de una query SQLAlchemy.

    Uso (cuando la query YA tiene join con ObsProducto):
        from helpers import filtro_solo_medicamentos
        base_q = filtro_solo_medicamentos(base_q, ObsProducto)
    """
    from sqlalchemy import not_
    for pat in NO_MEDICAMENTO_PATTERNS:
        query = query.filter(not_(ObsProducto.descripcion.ilike(pat)))
    return query


def top_productos_por_medico(session, medico_observer_ids, desde, hasta,
                              limit=10, resolver_codigo_barra=False,
                              excluir_no_medicamentos=True):
    """Top productos vendidos por uno o varios médicos en un rango.

    Devuelve lista de dicts con: observer_id, nombre, unidades, importe.
    Si `resolver_codigo_barra=True`, agrega `codigo_barra` (busca primero en
    Producto local por bridge, fallback a obs_codigos_barras orden=1).

    Aplica `ventas_periodo_filter` (suma neta = descuenta devoluciones).
    Si `excluir_no_medicamentos=True` (default) aplica el filtro de
    sellado/costo cupón.

    Args:
        session: SQLAlchemy session.
        medico_observer_ids: list[int] — IDs del médico. Usar
            `helpers.medicos_observer_ids_compartidos()` para agrupar
            variantes por matrícula (mismo médico, múltiples observer_id).
        desde, hasta: fechas inclusivas.
        limit: cantidad máxima de productos.
        resolver_codigo_barra: si True, resuelve el EAN para cada producto.
        excluir_no_medicamentos: si True (default), excluye sellado/cupón.

    Usado por: routes/consulta_medico.py (top 10 con CB). Disponible para
    otras pantallas que quieran "qué receta este médico" sin re-implementar.
    """
    from sqlalchemy import desc as _desc
    from sqlalchemy import func as _func

    import database as _db

    base = (session.query(_db.ObsVentaDetalle)
            .filter(_db.ObsVentaDetalle.medico_observer.in_(medico_observer_ids),
                    ventas_periodo_filter(_db.ObsVentaDetalle, desde, hasta)))
    if excluir_no_medicamentos:
        base = base.filter(excluir_no_medicamentos_ovd(
            _db.ObsVentaDetalle, _db.ObsProducto, session))

    top_rows = (base.with_entities(
                    _db.ObsVentaDetalle.producto_observer,
                    _func.coalesce(_func.sum(_db.ObsVentaDetalle.cantidad), 0).label('uds'),
                    _func.coalesce(_func.sum(_db.ObsVentaDetalle.importe), 0).label('imp'),
                )
                .group_by(_db.ObsVentaDetalle.producto_observer)
                .order_by(_desc('uds'))
                .limit(limit).all())

    prod_ids = [r[0] for r in top_rows if r[0]]
    nombre_por_id = {}
    cb_por_id = {}
    if prod_ids:
        for op in (session.query(_db.ObsProducto)
                   .filter(_db.ObsProducto.observer_id.in_(prod_ids)).all()):
            nombre_por_id[op.observer_id] = op.descripcion or ''
        if resolver_codigo_barra:
            for p in (session.query(_db.Producto)
                      .filter(_db.Producto.observer_id.in_(prod_ids)).all()):
                if p.codigo_barra:
                    cb_por_id[p.observer_id] = p.codigo_barra
            sin_cb = [pid for pid in prod_ids if pid not in cb_por_id]
            if sin_cb:
                for row in (session.query(_db.ObsCodigoBarras.producto_observer,
                                          _db.ObsCodigoBarras.codigo_barras)
                            .filter(_db.ObsCodigoBarras.producto_observer.in_(sin_cb),
                                    _db.ObsCodigoBarras.fecha_baja.is_(None),
                                    _db.ObsCodigoBarras.orden == 1).all()):
                    if row[1]:
                        cb_por_id[row[0]] = row[1].strip()

    out = []
    for r in top_rows:
        if not r[0]:
            continue
        item = {
            'observer_id': r[0],
            'nombre':      nombre_por_id.get(r[0], f'#{r[0]}'),
            'unidades':    float(r[1] or 0),
            'importe':     float(r[2] or 0),
        }
        if resolver_codigo_barra:
            item['codigo_barra'] = cb_por_id.get(r[0])
        out.append(item)
    return out


def excluir_no_medicamentos_ovd(ObsVentaDetalle, ObsProducto, session):
    """Devuelve un filtro SQLAlchemy para queries sobre ObsVentaDetalle que
    excluye filas cuyo producto es 'no-medicamento' (sellado de recetas,
    costo receta/cupón, etc.) — items que NO son ventas de medicamentos
    sino servicios administrativos de la farmacia.

    Usa una subquery — NO requiere joinear ObsProducto en la query principal.
    Aplicable en CUALQUIER estadística de ventas que sume cantidad/importe.

    Uso típico:
        from helpers import excluir_no_medicamentos_ovd
        base = (session.query(ObsVentaDetalle)
                .filter(
                    ventas_periodo_filter(ObsVentaDetalle, desde, hasta),
                    excluir_no_medicamentos_ovd(ObsVentaDetalle, ObsProducto, session),
                    # ... otros filtros propios
                ))

    Convención del proyecto: SIEMPRE excluir estos items al calcular
    "ventas". Solo se incluyen en informes de servicios o auditoría
    contable (donde el dato del cobro del cupón sí cuenta).
    """
    from sqlalchemy import not_, or_
    no_med_ids = session.query(ObsProducto.observer_id).filter(
        or_(*[ObsProducto.descripcion.ilike(pat) for pat in NO_MEDICAMENTO_PATTERNS])
    )
    return not_(ObsVentaDetalle.producto_observer.in_(no_med_ids))

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(CONVERTER_DIR, exist_ok=True)
os.makedirs(PURCHASE_FOLDER, exist_ok=True)


# ── Utility functions ────────────────────────────────────────────────────────

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_providers():
    with database.get_db() as session:
        providers = session.query(database.Provider).order_by(database.Provider.razon_social).all()
        return [{'id': p.id, 'razon_social': p.razon_social, 'cuit': p.cuit or '',
                 'parser_file': p.parser_file or '',
                 'ruta_facturas': p.ruta_facturas or '',
                 'grabar_productos': p.grabar_productos if p.grabar_productos is not None else 1} for p in providers]


def _make_parser_slug(name):
    """'DROGUERÍA EJEMPLO S.A.' → 'droguer_a_ejemplo_s_a'"""
    return re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')


def _ensure_parser_file(parser_name, razon_social, cuit=''):
    """Crea el archivo parser desde la plantilla si no existe."""
    parser_path = os.path.join(PARSERS_FOLDER, f'{parser_name}.py')
    if not os.path.exists(parser_path):
        with open(os.path.join(PARSERS_FOLDER, '_template.py'), encoding='utf-8') as f:
            template = f.read()
        content = (template
                   .replace('{{RAZON_SOCIAL}}', razon_social)
                   .replace('{{CUIT}}', cuit))
        with open(parser_path, 'w', encoding='utf-8') as f:
            f.write(content)


def _normalizar_nombre_entidad(nombre):
    """Normaliza nombre de laboratorio/proveedor para comparación deduplicada.

    Pipeline:
      1. lowercase
      2. quitar acentos
      3. quitar sufijos societarios (S.A., S.R.L., S.A.S., LTDA, S.C., S.H.)
      4. quitar prefijos genéricos (DROGUERÍA, LABORATORIO, LAB., DROG.)
      5. colapsar espacios y signos repetidos
      6. quitar puntos y comas extras

    Casos cubiertos:
      'Droguería Suizo Argentina S.A.' → 'suizo argentina'
      'DROGUERIA SUIZO ARGENTINA SA'   → 'suizo argentina'
      'Roemmers'                       → 'roemmers'
      'Roemmers S.A.I.C.F.'            → 'roemmers'
    """
    if not nombre:
        return ''
    import unicodedata
    s = str(nombre).strip().lower()
    # Quitar acentos
    s = ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))
    # Quitar sufijos societarios al final (en orden, los más largos primero)
    sufijos = [
        r'\bs\.?a\.?i\.?c\.?(?:f\.?)?\b',  # S.A.I.C., S.A.I.C.F.
        r'\bs\.?a\.?s\.?\b',
        r'\bs\.?r\.?l\.?\b',
        r'\bs\.?a\.?\b',
        r'\bs\.?c\.?\b',
        r'\bs\.?h\.?\b',
        r'\bltda\.?\b',
    ]
    for sufijo in sufijos:
        s = re.sub(sufijo + r'\s*$', '', s).strip()
    # Quitar prefijos genéricos
    prefijos = [r'^drogueria\s+', r'^drog\.?\s+', r'^laboratorio\s+', r'^lab\.?\s+']
    for pref in prefijos:
        s = re.sub(pref, '', s).strip()
    # Colapsar puntos, comas y espacios múltiples
    s = re.sub(r'[.,]+', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def get_or_create_laboratorio(session, nombre, observer_id=None, activo=True):
    """Devuelve un Laboratorio existente o crea uno nuevo.

    Deduplicación robusta por nombre normalizado (case-insensitive, sin acentos,
    sin sufijos societarios). Si encuentra match, devuelve el existente —
    nunca crea duplicado aunque el usuario tipee variantes.

    Args:
        session: sesión SQLAlchemy abierta
        nombre: nombre tal cual lo tipeó el usuario
        observer_id: si viene, prioriza match por observer_id (puente a Observer)
        activo: solo aplica si crea uno nuevo

    Returns:
        instancia Laboratorio (puede ser nueva — no llama commit, eso es responsabilidad del caller)
    """
    nombre = (nombre or '').strip()
    if not nombre:
        return None
    # 1. Match por observer_id (si viene): el bridge a Observer es 1-a-1.
    if observer_id is not None:
        existente = session.query(database.Laboratorio).filter_by(observer_id=observer_id).first()
        if existente:
            return existente
    # 2. Match por nombre normalizado (defensa contra variantes "Roemmers" / "Roemmers S.A." / "ROEMMERS")
    norm_buscado = _normalizar_nombre_entidad(nombre)
    if norm_buscado:
        candidatos = session.query(database.Laboratorio).all()
        for c in candidatos:
            if _normalizar_nombre_entidad(c.nombre) == norm_buscado:
                # Si el existente no tiene observer_id pero el nuevo sí, asignárselo
                if observer_id is not None and not c.observer_id:
                    c.observer_id = observer_id
                return c
    # 3. No existe → crear
    nuevo = database.Laboratorio(nombre=nombre, observer_id=observer_id, activo=activo)
    session.add(nuevo)
    session.flush()
    return nuevo


def get_or_create_proveedor(session, razon_social, cuit=None, **extras):
    """Devuelve un Provider existente o crea uno nuevo.

    Match por orden:
      1. CUIT exacto (si viene y existe)
      2. Razón social normalizada (sin acentos, sin sufijos societarios)

    Si encuentra match, devuelve el existente. Solo crea si genuinamente no hay.
    NO llama commit — el caller decide cuándo persistir.

    Args:
        session, razon_social, cuit, **extras: campos extra del Provider
            (domicilio, parser_file, tipo, etc.)
    """
    razon_social = (razon_social or '').strip()
    cuit = (cuit or '').strip() or None
    # 1. Match por CUIT
    if cuit:
        existente = session.query(database.Provider).filter_by(cuit=cuit).first()
        if existente:
            # Completar campos vacíos del existente con los nuevos (no pisa lo que ya está).
            for k, v in extras.items():
                if v and not getattr(existente, k, None):
                    setattr(existente, k, v)
            return existente
    # 2. Match por razón social normalizada
    if razon_social:
        norm = _normalizar_nombre_entidad(razon_social)
        if norm:
            for c in session.query(database.Provider).all():
                if _normalizar_nombre_entidad(c.razon_social) == norm:
                    if cuit and not c.cuit:
                        c.cuit = cuit
                    for k, v in extras.items():
                        if v and not getattr(c, k, None):
                            setattr(c, k, v)
                    return c
    # 3. No existe → crear
    if not razon_social:
        return None
    nuevo = database.Provider(razon_social=razon_social, cuit=cuit, **extras)
    session.add(nuevo)
    session.flush()
    return nuevo


def _get_or_create_provider_by_name(razon_social, cuit='', parser_name=''):
    """Wrapper con sesión propia sobre `get_or_create_proveedor`. Usado desde
    rutas Flask que no tienen sesión abierta. Devuelve (id, parser_file)."""
    with database.get_db() as session:
        provider = get_or_create_proveedor(session, razon_social, cuit,
                                           parser_file=parser_name)
        session.commit()
        return provider.id, provider.parser_file


def get_config():
    with database.get_db() as session:
        cfg = session.get(database.Config, 1)
        if not cfg:
            cfg = database.Config(id=1, farmacia_nombre='Farmacia', ruta_facturas='')
            session.add(cfg)
            session.commit()
        return {
            'farmacia_nombre': cfg.farmacia_nombre,
            'ruta_facturas': cfg.ruta_facturas or '',
            'ruta_excels': cfg.ruta_excels or '',
            'ruta_descargas': cfg.ruta_descargas or '',
            'ruta_backups': cfg.ruta_backups or '',
            'ruta_plantillas_lab': cfg.ruta_plantillas_lab or '',
            'umbral_pico': float(cfg.umbral_pico or 1.30),
            'umbral_baja': float(cfg.umbral_baja or 0.70),
            'umbral_tendencia': float(cfg.umbral_tendencia or 0.20),
            'rot_alta_min': float(cfg.rot_alta_min or 20.0),
            'rot_alta_tol': float(cfg.rot_alta_tol or 0.0),
            'rot_media_min': float(cfg.rot_media_min or 5.0),
            'rot_media_tol': float(cfg.rot_media_tol or 0.0),
            'rot_baja_tol': float(cfg.rot_baja_tol or 0.0),
        }


# ── Product helpers ──────────────────────────────────────────────────────────

def _get_all_barcodes(session, producto):
    """Devuelve TODOS los EANs asociados a un producto, consultando:
      1. `productos.codigo_barra` (campo principal, todavía existe).
      2. La tabla 1-a-N `producto_codigos_barra` (fuente de verdad).
      3. La tabla `obs_codigos_barras` si el producto tiene observer_id.

    Las columnas legacy `alt1/2/3` ya NO se leen — están vacías en producción
    y el código se prepara para el DROP COLUMN.

    Args:
        session: SQLAlchemy session.
        producto: instancia de Producto.

    Returns:
        list[str] sin duplicados.
    """
    if not producto:
        return []
    out = []
    seen = set()
    # 1. Principal en `productos.codigo_barra`
    if producto.codigo_barra and producto.codigo_barra not in seen:
        seen.add(producto.codigo_barra)
        out.append(producto.codigo_barra)
    # 2. Tabla 1-a-N local — fuente de verdad
    try:
        from database import ProductoCodigoBarra
        for cb, in (session.query(ProductoCodigoBarra.codigo_barra)
                    .filter_by(producto_id=producto.id).all()):
            if cb and cb not in seen:
                seen.add(cb)
                out.append(cb)
    except Exception:
        pass
    # 3. Observer
    if getattr(producto, 'observer_id', None):
        try:
            from database import ObsCodigoBarras
            for cb, in (session.query(ObsCodigoBarras.codigo_barras)
                        .filter_by(producto_observer=producto.observer_id)
                        .filter(ObsCodigoBarras.fecha_baja.is_(None)).all()):
                if cb and cb not in seen:
                    seen.add(cb)
                    out.append(cb)
        except Exception:
            pass
    return out


def _find_productos_bulk(session, eans):
    """Versión bulk de `_find_producto`. Para una lista de EANs devuelve
    `{ean: Producto}` consultando la cascada:

      1. `productos.codigo_barra` IN (eans) — match al principal
      2. `producto_codigos_barra` (1-a-N local) IN (eans) — match a alts/extras
      3. `obs_codigos_barras` IN (eans) → resuelve vía observer_id

    Las columnas `alt1/2/3` ya no se consultan — están vacías y migran a
    DROP COLUMN. La 1-a-N (`producto_codigos_barra`) cubre todos los EANs
    que antes vivían en alt1/2/3.

    Args:
        session: SQLAlchemy session.
        eans: iterable de strings.

    Returns:
        dict {ean: Producto}. Solo incluye los EANs que matchearon.
    """
    eans_clean = list({str(e).strip() for e in eans if e and str(e).strip()})
    if not eans_clean:
        return {}
    out = {}
    # 1. Match al principal en productos.codigo_barra
    prods = session.query(Producto).filter(
        Producto.codigo_barra.in_(eans_clean)
    ).all()
    for p in prods:
        if p.codigo_barra and p.codigo_barra in eans_clean and p.codigo_barra not in out:
            out[p.codigo_barra] = p
    pendientes = [e for e in eans_clean if e not in out]
    # 2. Match en producto_codigos_barra (1-a-N local)
    if pendientes:
        try:
            from database import ProductoCodigoBarra
            rows = (session.query(ProductoCodigoBarra.codigo_barra,
                                  ProductoCodigoBarra.producto_id)
                    .filter(ProductoCodigoBarra.codigo_barra.in_(pendientes))
                    .all())
            if rows:
                ids = {pid for _, pid in rows}
                prod_map = {p.id: p for p in
                            session.query(Producto).filter(Producto.id.in_(ids)).all()}
                for ean, pid in rows:
                    if pid in prod_map and ean not in out:
                        out[ean] = prod_map[pid]
                pendientes = [e for e in pendientes if e not in out]
        except Exception:
            pass
    # 3. Match en obs_codigos_barras (resuelve por observer_id)
    if pendientes:
        try:
            from database import ObsCodigoBarras
            rows = (session.query(ObsCodigoBarras.codigo_barras,
                                  ObsCodigoBarras.producto_observer)
                    .filter(ObsCodigoBarras.codigo_barras.in_(pendientes),
                            ObsCodigoBarras.fecha_baja.is_(None))
                    .all())
            if rows:
                obs_ids = {oid for _, oid in rows}
                prod_map = {p.observer_id: p for p in
                            session.query(Producto)
                            .filter(Producto.observer_id.in_(obs_ids)).all()}
                for ean, oid in rows:
                    if oid in prod_map and ean not in out:
                        out[ean] = prod_map[oid]
        except Exception:
            pass
    return out


def _find_producto(session, codigo_barra):
    """Busca un producto por EAN. Consulta en orden:
      1. `productos.codigo_barra` o `alt1/2/3` (legacy local, rápido).
      2. `producto_codigos_barra` (1-a-N local, reemplazo gradual de alts).
      3. `obs_codigos_barras` → resuelve vía `observer_id` (1-a-N de Observer).

    Cada query solo corre si la anterior falla. El campo `productos.codigo_barra`
    cubre el principal; los alts/extras viven en la 1-a-N y se buscan en (2).
    Las columnas legacy `alt1/2/3` ya NO se consultan (vacías + DROP COLUMN
    pendiente).
    """
    bc = str(codigo_barra).strip()
    if not bc:
        return None
    # 1. Match al principal en productos.codigo_barra
    prod = (session.query(Producto)
            .filter(Producto.codigo_barra == bc).first())
    if prod is not None:
        return prod
    # 2. Match en producto_codigos_barra (1-a-N local)
    try:
        from database import ProductoCodigoBarra
        m = (session.query(ProductoCodigoBarra.producto_id)
             .filter(ProductoCodigoBarra.codigo_barra == bc)
             .first())
        if m:
            return session.get(Producto, m[0])
    except Exception:
        pass
    # 3. Match en obs_codigos_barras → resuelve por observer_id
    try:
        from database import ObsCodigoBarras
        m = (session.query(ObsCodigoBarras.producto_observer)
             .filter(ObsCodigoBarras.codigo_barras == bc,
                     ObsCodigoBarras.fecha_baja.is_(None))
             .first())
        if m:
            return (session.query(Producto)
                    .filter(Producto.observer_id == m[0])
                    .first())
    except Exception:
        pass
    return None


def _upsert_producto(session, codigo_barra, descripcion, precio_pvp=None, laboratorio_id=None, fecha_compra=None, codigo_alfabeta=None):
    """Crea o actualiza un producto en la tabla productos."""
    if not codigo_barra:
        return
    codigo_barra = str(codigo_barra).strip()
    codigo_alfabeta = str(codigo_alfabeta).strip() if codigo_alfabeta else None
    prod = _find_producto(session, codigo_barra)
    if prod:
        if descripcion and not prod.descripcion:
            prod.descripcion = str(descripcion).strip()
        if precio_pvp and float(precio_pvp) > 0:
            prod.precio_pvp = precio_pvp
        if laboratorio_id and not prod.laboratorio_id:
            prod.laboratorio_id = laboratorio_id
        if fecha_compra and (not prod.ultima_compra or fecha_compra > prod.ultima_compra):
            prod.ultima_compra = fecha_compra
        if codigo_alfabeta and not prod.codigo_alfabeta:
            prod.codigo_alfabeta = codigo_alfabeta
        from datetime import datetime as _dt
        prod.actualizado_en = _dt.utcnow()
    else:
        session.add(Producto(
            codigo_barra=codigo_barra,
            descripcion=str(descripcion).strip() if descripcion else '',
            precio_pvp=precio_pvp,
            laboratorio_id=laboratorio_id,
            ultima_compra=fecha_compra,
            codigo_alfabeta=codigo_alfabeta,
        ))


def _upsert_pedido_items(session, items, observer_bridge=False):
    """Itera los PedidoItem de un pedido recién creado, asegura que cada producto
    esté en el catálogo master y opcionalmente liga el bridge a obs_productos
    cuando el código viene como pseudo-EAN ``OBS:<id>``.

    Centraliza el loop que antes se repetía en purchase.py:711, purchase.py:887
    e informes.py:808 (este último incluso omitía el upsert).

    Args:
        session: sesión SQLAlchemy abierta.
        items: iterable de PedidoItem ya agregados al pedido (con codigo_barra,
               nombre, precio_pvp).
        observer_bridge: si True, los códigos ``OBS:<id>`` que correspondan a
                         productos ya creados se atan a obs_productos.
    """
    for it in items:
        _upsert_producto(session, it.codigo_barra, it.nombre, float(it.precio_pvp or 0))
        if observer_bridge and it.codigo_barra and it.codigo_barra.startswith('OBS:'):
            try:
                obs_id = int(it.codigo_barra[4:])
            except (ValueError, TypeError):
                continue
            prod = session.query(Producto).filter_by(codigo_barra=it.codigo_barra).first()
            if prod and not prod.observer_id:
                ya_tomado = session.query(Producto.id).filter(
                    Producto.observer_id == obs_id,
                    Producto.id != prod.id,
                ).first()
                if not ya_tomado:
                    prod.observer_id = obs_id


def _add_alt_barcode(session, codigo_barra_erp, codigo_barra_alt, fuente='manual', factura_id=None):
    """Agrega un código alternativo al producto ERP si no está ya registrado.

    Escribe en ambos lados durante la migración:
      - Por default: llena slots libres en alt1/2/3 + persiste en
        producto_codigos_barra (1-a-N).
      - Si la env var `EAN_LEGACY_ALTS_DISABLED=1` está set: solo escribe
        en producto_codigos_barra. Útil para validar Fase 4 antes de
        dropear las columnas legacy.

    Siempre inserta en producto_codigos_barra (1-a-N, sin límite, con
    trazabilidad de fuente y factura).
    """
    if not codigo_barra_erp or not codigo_barra_alt:
        return
    codigo_barra_erp = str(codigo_barra_erp).strip()
    codigo_barra_alt = str(codigo_barra_alt).strip()
    if codigo_barra_erp == codigo_barra_alt:
        return
    prod = session.query(Producto).filter_by(codigo_barra=codigo_barra_erp).first()
    if not prod:
        return
    # Tabla 1-a-N: única fuente de verdad para alts. Insert idempotente
    # (UNIQUE constraint en (producto_id, codigo_barra)). Las columnas
    # legacy `alt1/2/3` ya no se escriben (DROP COLUMN pendiente).
    try:
        from database import ProductoCodigoBarra
        ya = (session.query(ProductoCodigoBarra.id)
              .filter_by(producto_id=prod.id, codigo_barra=codigo_barra_alt).first())
        if not ya:
            session.add(ProductoCodigoBarra(
                producto_id=prod.id,
                codigo_barra=codigo_barra_alt,
                es_principal=False,
                fuente=fuente,
                factura_id=factura_id,
            ))
    except Exception:
        pass


# ── Bulk product upsert ──────────────────────────────────────────────────────

def _bulk_upsert_productos(session, items):
    """Upsert masivo: 1 SELECT en vez de N. items: list of (codigo_barra, descripcion, precio_pvp, fecha_compra).

    Lookup en cascada: primero por `productos.codigo_barra` (principal),
    fallback por `producto_codigos_barra` (1-a-N) para EANs que viven solo
    como alternativos. Las columnas legacy `alt1/2/3` ya NO se consultan.
    """
    from datetime import datetime as _dt

    barcodes = list({str(i[0]).strip() for i in items if i[0]})
    if not barcodes:
        return

    # 1. Match al principal
    existing = session.query(Producto).filter(
        Producto.codigo_barra.in_(barcodes)
    ).all()

    prod_map = {}
    for p in existing:
        if p.codigo_barra:
            prod_map[p.codigo_barra] = p

    # 2. Match a EANs alternativos en producto_codigos_barra
    pendientes = [b for b in barcodes if b not in prod_map]
    if pendientes:
        try:
            from database import ProductoCodigoBarra
            rows = (session.query(ProductoCodigoBarra.codigo_barra,
                                  ProductoCodigoBarra.producto_id)
                    .filter(ProductoCodigoBarra.codigo_barra.in_(pendientes))
                    .all())
            if rows:
                ids = {pid for _, pid in rows}
                extras = {p.id: p for p in
                          session.query(Producto).filter(Producto.id.in_(ids)).all()}
                for ean, pid in rows:
                    if pid in extras and ean not in prod_map:
                        prod_map[ean] = extras[pid]
        except Exception:
            pass

    new_prods = []
    for codigo_barra, descripcion, precio_pvp, fecha_compra in items:
        if not codigo_barra:
            continue
        bc = str(codigo_barra).strip()
        prod = prod_map.get(bc)
        if prod:
            if descripcion and not prod.descripcion:
                prod.descripcion = str(descripcion).strip()
            if precio_pvp and float(precio_pvp) > 0:
                prod.precio_pvp = precio_pvp
            if fecha_compra and (not prod.ultima_compra or fecha_compra > prod.ultima_compra):
                prod.ultima_compra = fecha_compra
            prod.actualizado_en = _dt.utcnow()
        else:
            new_prod = Producto(
                codigo_barra=bc,
                descripcion=str(descripcion).strip() if descripcion else '',
                precio_pvp=precio_pvp,
                ultima_compra=fecha_compra,
            )
            new_prods.append(new_prod)
            prod_map[bc] = new_prod
    if new_prods:
        session.add_all(new_prods)
        # Flush para que una llamada subsiguiente vea estos productos en el SELECT
        # y no intente insertarlos de nuevo (evita UNIQUE violation en codigo_barra).
        session.flush()


# ── Normalizador de texto PDF con caracteres cuadruplicados ───────────────
# pdfplumber en ciertas fuentes/layouts (ej. 20 de Junio) cuadruplica cada
# carácter de texto en negrita: "TOTAL" → "TTTTOOOOTTTTAAAALLLL". Detecta
# líneas donde al menos un token es multi-char cuadruplicado y reduce esa
# línea entera con `(.)\1{3} → \1`. No toca líneas normales.
# ── OCR fallback para PDFs escaneados (sin capa de texto) ──────────────────
# Usa pytesseract (con tesseract-ocr + tesseract-ocr-spa instalados en Docker).
# Cachea el resultado en "<pdf_path>.ocr.txt" para no re-procesar.
def _clean_ocr_text(txt):
    """Post-procesa texto OCR: quita espacios y líneas extras."""
    import re as _re
    # 1) Strip por línea
    lines = [l.rstrip() for l in (txt or '').split('\n')]
    out = []
    for l in lines:
        # 2) Colapsar espacios/tabs múltiples dentro de la línea a uno
        l = _re.sub(r'[ \t]+', ' ', l).strip()
        out.append(l)
    # 3) Colapsar 3+ líneas vacías a 1 sola
    result = []
    empty = 0
    for l in out:
        if not l:
            empty += 1
            if empty >= 2:
                continue  # saltar línea vacía extra
        else:
            empty = 0
        result.append(l)
    return '\n'.join(result)


def _preprocess_image_for_ocr(pil_img):
    """Convierte a grayscale + auto-contraste + binarización para mejorar OCR.
    Reduce errores tipo '$→2/3/5' que ocurren cuando Tesseract ve tonos grises
    intermedios en vez de blanco puro y negro puro."""
    try:
        from PIL import ImageOps
        img = pil_img.convert('L')                     # grayscale
        img = ImageOps.autocontrast(img, cutoff=2)     # estirar contraste
        img = img.point(lambda p: 255 if p > 170 else 0, mode='1')  # threshold → B/W
        return img
    except Exception:
        return pil_img  # si falla cualquier cosa, devolvemos la original


def extract_text_with_ocr_fallback(pdf_path, min_chars=50, lang='spa', dpi=400):
    """Intenta extraer texto con pdfplumber. Si el resultado es muy chico
    (PDF escaneado), corre OCR sobre cada página y cachea el resultado.

    Retorna el texto completo (todas las páginas unidas con \\n).
    """
    import os as _os

    import pdfplumber as _pdfplumber
    # 1) Intento rápido con pdfplumber
    try:
        with _pdfplumber.open(pdf_path) as pdf:
            texto_plano = '\n'.join((p.extract_text() or '') for p in pdf.pages)
    except Exception:
        texto_plano = ''
    if len(texto_plano.strip()) >= min_chars:
        return texto_plano

    # 2) Necesitamos OCR. Chequear cache.
    cache_path = pdf_path + '.ocr.txt'
    if _os.path.isfile(cache_path):
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                cached = f.read()
            if cached.strip():
                return cached
        except OSError:
            pass

    # 3) Correr OCR página por página
    try:
        import pytesseract
    except ImportError:
        return texto_plano  # sin OCR disponible, devolvemos lo que haya

    try:
        paginas = []
        with _pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                img = page.to_image(resolution=dpi).original
                img = _preprocess_image_for_ocr(img)  # grayscale + binarización
                # PSM 6: bloque uniforme de texto (mejor para facturas)
                txt = pytesseract.image_to_string(img, lang=lang, config='--psm 6') or ''
                paginas.append(txt)
        texto_ocr = _clean_ocr_text('\n'.join(paginas))
    except Exception:
        return texto_plano

    # 4) Guardar cache
    try:
        with open(cache_path, 'w', encoding='utf-8') as f:
            f.write(texto_ocr)
    except OSError:
        pass
    return texto_ocr


def _normalize_quadrupled(text):
    import re as _re
    out_lines = []
    for line in text.split('\n'):
        hit = False
        # Detectar si la línea tiene tokens cuadruplicados (TTTTOOOO...) o un
        # run largo de tokens cortos (letter-spacing "( G ) G r a v a d o...").
        short_run = 0
        max_short_run = 0
        for tok in line.split():
            if len(tok) < 8 or len(tok) % 4 != 0:
                pass
            else:
                chunks = [tok[i:i+4] for i in range(0, len(tok), 4)]
                if all(len(set(c)) == 1 for c in chunks) and len({c[0] for c in chunks}) >= 2:
                    hit = True
            if len(tok) <= 2:
                short_run += 1
                if short_run > max_short_run:
                    max_short_run = short_run
            else:
                short_run = 0
        if max_short_run >= 10:
            hit = True  # letter-spacing significativo
        if hit:
            # 1) Reducir cuadruplicados: TTTTOOOO → TO
            line = _re.sub(r'(.)\1{3}', r'\1', line)
            # 2) Colapsar runs de ≥5 tokens cortos (letter-spacing):
            #    `G r a v a d o` → `Gravado`
            tokens = line.split(' ')
            result, run = [], []
            def _flush(res, r):
                if len(r) >= 5:
                    res.append(''.join(r))
                else:
                    res.extend(r)
            for t in tokens:
                if len(t) <= 2:
                    run.append(t)
                else:
                    _flush(result, run); run = []
                    result.append(t)
            _flush(result, run)
            line = ' '.join(result)
        # 3) Colapsar rellenos de puntos (≥4 puntos seguidos) → espacio simple
        #    `Subtotal Bruto................................$ 1.409.334,86` → `Subtotal Bruto $ 1.409.334,86`
        #    Aplica siempre (no solo en líneas hit), es un artefacto común.
        line = _re.sub(r'\.{4,}', ' ', line)
        out_lines.append(line)
    return '\n'.join(out_lines)


# ── Pattern builder (used by invoices + converter) ──────────────────────────

def _build_item_pattern(example_line, selections):
    """Build regex pattern from example_line + selections.
    Content after the first newline in example_line is wrapped in an optional group,
    so rows that fit on a single line still match."""
    import re as _re
    sel = sorted(selections, key=lambda s: s.get('start', 0))

    def _kind(txt):
        t = (txt or '').strip()
        if _re.fullmatch(r'-?\d[\d.,]*', t):
            return r'[\d.,]+'
        if _re.fullmatch(r'\d+', t):
            return r'\d+'
        if _re.fullmatch(r'\S+', t):
            return r'\S+'
        return r'.+?'

    def _norm_literal(s):
        result = ''
        for tok in _re.findall(r'\s+|\S+', s):
            if tok.isspace():
                result += r'\s+'
            else:
                result += _re.escape(tok)
        return result

    first_nl = example_line.find('\n')
    pattern_main = '^'
    pattern_opt = ''
    cursor = 0
    fields = []
    prev_cap = None
    opened_opt = False

    for s in sel:
        start = int(s.get('start', 0))
        end   = int(s.get('end', start))
        literal = example_line[cursor:start]
        cap = _kind(example_line[start:end])

        in_opt_zone = first_nl >= 0 and start >= first_nl

        if in_opt_zone and not opened_opt:
            if first_nl > cursor:
                pre = example_line[cursor:first_nl]
                if pre:
                    pattern_main += _norm_literal(pre)
            pattern_opt = r'(?:'
            rest = example_line[max(cursor, first_nl):start]
            if rest:
                pattern_opt += _norm_literal(rest)
            opened_opt = True
        else:
            target = pattern_opt if opened_opt else pattern_main
            if literal:
                norm = _norm_literal(literal)
                if literal.strip() == '' and prev_cap == r'.+?' and cap in (r'[\d.,]+', r'\d+', r'\S+'):
                    norm = r'\s*'
                if opened_opt:
                    pattern_opt += norm
                else:
                    pattern_main += norm
            elif prev_cap is not None and cap in (r'[\d.,]+', r'\d+', r'\S+') \
                    and prev_cap in (r'[\d.,]+', r'\d+', r'\S+'):
                # Dos capturas del mismo tipo sin literal entre ellas → sin un
                # separador explícito, la primera greedy roba caracteres de la
                # segunda. Forzamos \s+ para evitar el bug.
                if opened_opt:
                    pattern_opt += r'\s+'
                else:
                    pattern_main += r'\s+'

        if opened_opt:
            pattern_opt += '(' + cap + ')'
        else:
            pattern_main += '(' + cap + ')'
        fields.append(s.get('field'))
        prev_cap = cap
        cursor = end

    tail = example_line[cursor:]
    if tail.strip():
        if first_nl >= cursor and not opened_opt:
            pre = example_line[cursor:first_nl]
            if pre:
                pattern_main += _norm_literal(pre)
            rest = example_line[first_nl:]
            if rest.strip():
                pattern_opt = r'(?:' + _norm_literal(rest)
                opened_opt = True
        else:
            if opened_opt:
                pattern_opt += _norm_literal(tail)
            else:
                pattern_main += _norm_literal(tail)

    pattern = pattern_main + (pattern_opt + r')?' if opened_opt else '')
    # Anclar al fin de línea para evitar que el último [\d.,]+ absorba
    # el primer número de la línea siguiente (bug de PHARMAMERICAN:
    # precio_unit capturaba el EAN de la fila de abajo).
    pattern += r'\s*$'

    def _base(name):
        return _re.sub(r'_\d+$', '', name or '')

    base_fields = []
    for f in fields:
        b = _base(f)
        if b not in base_fields:
            base_fields.append(b)

    return pattern, fields, base_fields, _base


def medicos_observer_ids_compartidos(session, medico_id):
    """Devuelve todos los obs_medicos.observer_id que comparten al menos una
    matrícula con el médico dado.

    Caso de uso: en Observer, el POS crea un médico nuevo cada vez que se
    vende un producto promocionado por un laboratorio, etiquetándolo con
    el nombre del lab + nombre del médico. Ej:

        5968 | PALADINO, ANDREA BEATRIZ  | matrícula 16097
       86964 | BERNABO PALADINO          | matrícula 16097
       88185 | BONO BALIARDA PALADINO    | matrícula 16097

    Las tres son la misma médica para fines clínicos pero el sistema las
    contabiliza separadas. Esta función agrupa por matrícula así los
    informes y consultas pueden agregarlas correctamente.

    Args:
        session: SQLAlchemy session.
        medico_id: observer_id del médico base.

    Returns:
        Lista de observer_ids (incluye el medico_id original). Si el médico
        no tiene matrícula, devuelve [medico_id] (no hay forma de agrupar).
    """
    import database

    matriculas = (session.query(database.ObsMedicoMatricula.matricula)
                  .filter(database.ObsMedicoMatricula.medico_observer == medico_id,
                          database.ObsMedicoMatricula.fecha_baja.is_(None))
                  .all())
    matriculas_set = {m[0] for m in matriculas if m[0]}
    if not matriculas_set:
        return [medico_id]
    relacionados = (session.query(database.ObsMedicoMatricula.medico_observer)
                    .filter(database.ObsMedicoMatricula.matricula.in_(matriculas_set),
                            database.ObsMedicoMatricula.fecha_baja.is_(None))
                    .distinct()
                    .all())
    ids = {r[0] for r in relacionados}
    ids.add(medico_id)
    return sorted(ids)


def calcular_metricas_pedido_auto(stock, minimo, maximo, u12m, m12m,
                                  dias_cobertura=None):
    """Métricas de reposición para un producto bajo mínimo.

    Función pura, sin DB. Testeable.

    Args:
        stock: int, stock actual.
        minimo: int, mínimo configurado.
        maximo: int o None, máximo configurado.
        u12m: int, unidades vendidas en los últimos 12 meses.
        m12m: float, monto vendido en los últimos 12 meses.
        dias_cobertura: int o None. Si se especifica, calcula `sugerido` para
            cubrir esa cantidad de días de venta proyectada (en base a u12m),
            ignorando mínimo/máximo configurados.

    Returns:
        dict con: sugerido, base_sugerido, avg_mensual, precio_unit,
                  perdida_mensual, perdida_pesos, min_diag, min_diag_label.

    Reglas:
      - Si u12m=0 → sugerido=0 (no proponer compra de productos sin movimiento).
      - Si dias_cobertura → sugerido = ceil(u12m/365 * dias) - stock, clip ≥ 0.
      - Si hay máximo > stock → sugerido = maximo - stock.
      - Si no → sugerido = max(1, minimo - stock).
      - avg_mensual = u12m / 12.
      - precio_unit = m12m / u12m (0 si no hay ventas).
      - factor_falta = clamp((minimo - stock) / minimo, 0, 1).
      - perdida_mensual = avg_mensual * factor_falta.
      - perdida_pesos = perdida_mensual * precio_unit.
      - Diagnóstico de mínimo:
          - sin_ventas si u12m=0
          - ratio = minimo / avg_mensual
          - <0.5 → bajo (cubre <~2 semanas)
          - >2   → alto (cubre >2 meses)
          - sino → ok
    """
    import math
    stock = int(stock or 0)
    minimo = int(minimo or 0)
    maximo = int(maximo) if maximo is not None else None
    u12m = int(u12m or 0)
    m12m = float(m12m or 0)
    dias_cobertura = int(dias_cobertura) if dias_cobertura else None

    if u12m <= 0:
        sugerido = 0
        base_sugerido = 'sin_ventas'
    elif dias_cobertura and dias_cobertura > 0:
        target = math.ceil(u12m / 365.0 * dias_cobertura)
        sugerido = max(0, target - stock)
        base_sugerido = f'dias-{dias_cobertura}'
    elif maximo and maximo > stock:
        sugerido = maximo - stock
        base_sugerido = 'max-stock'
    else:
        sugerido = max(1, minimo - stock)
        base_sugerido = 'min-stock'

    avg_mensual = u12m / 12.0 if u12m else 0.0
    precio_unit = (m12m / u12m) if u12m else 0.0
    factor_falta = min(1.0, max(0.0, (minimo - stock) / minimo)) if minimo else 0.0
    perdida_mensual = round(avg_mensual * factor_falta, 1)
    perdida_pesos = round(perdida_mensual * precio_unit, 2)

    if avg_mensual <= 0:
        min_diag = 'sin_ventas'
        min_diag_label = 'Sin ventas 12m'
    else:
        ratio = minimo / avg_mensual
        if ratio < 0.5:
            min_diag = 'bajo'
            min_diag_label = f'Bajo — cubre ~{int(ratio * 30)}d, sugerido ≥{int(round(avg_mensual))}'
        elif ratio > 2:
            min_diag = 'alto'
            min_diag_label = f'Alto — cubre ~{int(ratio * 30)}d, sugerido ≈{int(round(avg_mensual * 1.5))}'
        else:
            min_diag = 'ok'
            min_diag_label = f'OK — cubre ~{int(ratio * 30)}d'

    return {
        'sugerido': sugerido,
        'base_sugerido': base_sugerido,
        'avg_mensual': round(avg_mensual, 2),
        'precio_unit': round(precio_unit, 2),
        'perdida_mensual': perdida_mensual,
        'perdida_pesos': perdida_pesos,
        'min_diag': min_diag,
        'min_diag_label': min_diag_label,
    }


def aplicar_overrides_planificador(sugerido, stock, minimo, cant_fija, oferta_min):
    """Aplica overrides operativos sobre un sugerido calculado.

    Función pura, sin DB. Sirve para que /pedido/prueba (planificador) muestre
    los mismos números que después aparecen en /compras/dia/armar (operativo),
    que ya respeta estos overrides via services/calculo_pedido.py.

    Reglas (orden de precedencia):
      1. cant_fija (Producto.cantidad_reposicion_fija): hard override. Si
         stock <= minimo y cant_fija > 0 → sugerido = cant_fija, regardless
         de lo que diga el cálculo. Decisión explícita del operador.
      2. oferta_min (OfertaMinimo.unidades_minima): piso. Si sugerido > 0
         y sugerido < oferta_min → sugerido = oferta_min (subir al mínimo
         para acceder al descuento TRF). Si sugerido = 0 NO sube (no compro
         solo por la oferta).
      3. Sin override → sugerido sin cambios.

    Args:
        sugerido: int, cantidad calculada antes de override.
        stock: int, stock actual (usado para regla cant_fija).
        minimo: int, mínimo configurado (usado para regla cant_fija).
        cant_fija: int|None, valor de Producto.cantidad_reposicion_fija.
        oferta_min: int|None, valor de OfertaMinimo.unidades_minima vigente.

    Returns:
        tuple (sugerido_final, override_slug, override_valor) donde
        override_slug es 'cant_fija' | 'oferta_min' | None.
    """
    sugerido = int(sugerido or 0)
    stock = int(stock or 0)
    minimo = int(minimo or 0)
    cant_fija = int(cant_fija) if cant_fija else 0
    oferta_min = int(oferta_min) if oferta_min else 0

    if cant_fija > 0 and stock <= minimo:
        return (cant_fija, 'cant_fija', cant_fija)
    if oferta_min > 0 and 0 < sugerido < oferta_min:
        return (oferta_min, 'oferta_min', oferta_min)
    return (sugerido, None, None)


# Buckets de rotación de productos (avg_mensual).
# Usado por /informes/cadencias-lab. La idea no es calcular una cadencia exacta
# (eso depende de la política del operador) sino agrupar por *qué tan rápido
# rota* el producto y sugerir cada cuánto conviene reponerlo.
#
# Tupla: (slug, label, icono, avg_mensual_min, cadencia_sugerida, color)
#   avg_mensual_min = umbral inferior (>=) para entrar al bucket
#   cadencia_sugerida = texto descriptivo para la UI
CADENCIA_BUCKETS = [
    ('alta',       'Alta rotación (≥60/mes)',  '🔥', 60.0, '~10 días',     'rojo'),
    ('media_alta', 'Media-alta (30-60/mes)',   '⚡', 30.0, '~15 días',     'amarillo'),
    ('media',      'Media (10-30/mes)',        '🐢', 10.0, '~30 días',     'verde'),
    ('baja',       'Baja (5-10/mes)',          '🐌',  5.0, '~45 días',     'azul'),
    ('muy_baja',   'Muy baja (<5/mes)',        '💤',  0.0, '~60-90 días',  'mute'),
]


def _bucket_cadencia(avg_mensual):
    """Devuelve el slug del bucket según el avg mensual de ventas.

    >>> _bucket_cadencia(75)
    'alta'
    >>> _bucket_cadencia(40)
    'media_alta'
    >>> _bucket_cadencia(15)
    'media'
    >>> _bucket_cadencia(7)
    'baja'
    >>> _bucket_cadencia(1)
    'muy_baja'
    """
    for slug, _label, _icono, avg_min, _cad, _color in CADENCIA_BUCKETS:
        if avg_mensual >= avg_min:
            return slug
    return 'muy_baja'


def analizar_cadencias_lab(session, lab_observer_id, meses_rotacion=3,
                            cobertura_default=30):
    """Agrupa productos de un lab según su cadencia natural de compra.

    Para cada producto del lab con ventas:
      cadencia_natural = cant_reposicion / avg_diario
    donde cant_reposicion = cant_fija si está seteada, sino cobertura_default.

    Args:
        session: SQLAlchemy session.
        lab_observer_id: int, observer_id del laboratorio.
        meses_rotacion: int, meses para calcular avg_diario (default 3).
        cobertura_default: int, días de cobertura asumida para productos
            sin cant_fija seteada (default 30 = mensual).

    Returns:
        dict {
            'lab_id': int,
            'meses_rotacion': int,
            'cobertura_default': int,
            'buckets': [
                {slug, label, icono, color, dias_max,
                 n_productos, monto_mensual, items: [...]}
            ],
            'sin_ventas': [...],
            'totales': {
                'productos_con_ventas': int,
                'productos_sin_ventas': int,
                'monto_mensual_total': float,
            }
        }
    """
    import database

    _DIAS_PROM_MES = 30.42
    dias_rotacion = int(meses_rotacion * _DIAS_PROM_MES)

    # 1. Productos del lab (vía ObsProducto.laboratorio_observer).
    rows_prod = (session.query(database.ObsProducto.observer_id,
                               database.ObsProducto.descripcion,
                               database.ObsProducto.codigo_alfabeta)
                 .filter(database.ObsProducto.laboratorio_observer == lab_observer_id,
                         database.ObsProducto.fecha_baja.is_(None))
                 .all())
    if not rows_prod:
        return {'lab_id': lab_observer_id, 'meses_rotacion': meses_rotacion,
                'cobertura_default': cobertura_default, 'buckets': [],
                'sin_ventas': [], 'totales': {
                    'productos_con_ventas': 0, 'productos_sin_ventas': 0,
                    'monto_mensual_total': 0.0}}
    obs_ids = [r.observer_id for r in rows_prod]

    # 2. Bulk: cant_fija (Producto master local linkeado por observer_id)
    #          + stock (ObsStock) + ventas/monto (ObsVentaMensual u12m).
    cant_fija_map = dict(session.query(database.Producto.observer_id,
                                       database.Producto.cantidad_reposicion_fija)
                          .filter(database.Producto.observer_id.in_(obs_ids),
                                  database.Producto.cantidad_reposicion_fija.isnot(None),
                                  database.Producto.cantidad_reposicion_fija > 0)
                          .all())

    from sqlalchemy import func as _f
    stock_rows = (session.query(database.ObsStock.producto_observer,
                                _f.sum(database.ObsStock.stock_actual))
                  .filter(database.ObsStock.producto_observer.in_(obs_ids))
                  .group_by(database.ObsStock.producto_observer).all())
    stock_map = {r[0]: int(r[1] or 0) for r in stock_rows}

    # u_rot (últimos `meses_rotacion` meses completos) + monto (últimos 12m
    # para tener el precio histórico estable).
    from datetime import date as _d
    hoy = _d.today()
    end_anio = hoy.year if hoy.month > 1 else hoy.year - 1
    end_mes = hoy.month - 1 if hoy.month > 1 else 12
    start_mes = end_mes - (meses_rotacion - 1)
    start_anio = end_anio
    while start_mes <= 0:
        start_mes += 12
        start_anio -= 1
    desde_ym = start_anio * 100 + start_mes
    hasta_ym = end_anio * 100 + end_mes
    vm = database.ObsVentaMensual
    urot_rows = (session.query(vm.producto_observer,
                               _f.sum(vm.unidades),
                               _f.sum(vm.monto))
                 .filter(vm.producto_observer.in_(obs_ids),
                         vm.anio * 100 + vm.mes >= desde_ym,
                         vm.anio * 100 + vm.mes <= hasta_ym)
                 .group_by(vm.producto_observer).all())
    urot_map = {r[0]: (float(r[1] or 0), float(r[2] or 0)) for r in urot_rows}

    # 3. Por producto: calcular cadencia + bucket + monto mensual.
    # Nota: usamos 'productos' (no 'items') porque en Jinja2 `dict.items`
    # resuelve al método del dict, no a la key — sería un bug silencioso.
    buckets_data = {slug: {'slug': slug, 'label': label, 'icono': icono,
                            'color': color, 'avg_min': avg_min,
                            'cadencia_sugerida': cad_sug,
                            'n_productos': 0, 'monto_mensual': 0.0, 'productos': []}
                    for slug, label, icono, avg_min, cad_sug, color in CADENCIA_BUCKETS}
    sin_ventas = []

    for r in rows_prod:
        u_rot, m_rot = urot_map.get(r.observer_id, (0.0, 0.0))
        if u_rot <= 0:
            sin_ventas.append({
                'observer_id': r.observer_id,
                'nombre': r.descripcion,
                'codigo_alfabeta': r.codigo_alfabeta,
            })
            continue
        avg_diario = u_rot / dias_rotacion
        avg_mensual = avg_diario * _DIAS_PROM_MES
        precio_unit = (m_rot / u_rot) if u_rot else 0  # PVP estimado
        monto_mensual = avg_mensual * precio_unit
        cant_fija = cant_fija_map.get(r.observer_id)
        # Cant de reposición: si tiene cant_fija configurada usa eso;
        # sino, asume cobertura_default días.
        if cant_fija:
            cant_repo = cant_fija
            origen_cant = 'cant_fija'
        else:
            cant_repo = max(1, int(round(avg_diario * cobertura_default)))
            origen_cant = 'cobertura_default'
        cadencia_dias = cant_repo / avg_diario if avg_diario else 9999
        # Bucket por avg_mensual (rotación real), NO por cadencia calculada
        # — esa quedaba siempre = cobertura_default para productos sin cant_fija.
        slug = _bucket_cadencia(avg_mensual)
        bucket = buckets_data[slug]
        bucket['n_productos'] += 1
        bucket['monto_mensual'] += monto_mensual
        bucket['productos'].append({
            'observer_id': r.observer_id,
            'nombre': r.descripcion,
            'codigo_alfabeta': r.codigo_alfabeta,
            'stock': stock_map.get(r.observer_id, 0),
            'avg_diario': round(avg_diario, 2),
            'avg_mensual': round(avg_mensual, 1),
            'cant_repo': cant_repo,
            'origen_cant': origen_cant,
            'cadencia_dias': round(cadencia_dias, 1),
            'precio_unit': round(precio_unit, 2),
            'monto_mensual': round(monto_mensual, 2),
        })

    # Ordenar productos dentro de cada bucket por monto mensual desc (lo que más
    # plata mueve, primero).
    for b in buckets_data.values():
        b['productos'].sort(key=lambda x: -x['monto_mensual'])
        b['monto_mensual'] = round(b['monto_mensual'], 2)

    # Devolver buckets en el orden definido en CADENCIA_BUCKETS.
    buckets = [buckets_data[slug] for slug, *_ in CADENCIA_BUCKETS]
    con_ventas = sum(b['n_productos'] for b in buckets)
    monto_total = sum(b['monto_mensual'] for b in buckets)

    return {
        'lab_id': lab_observer_id,
        'meses_rotacion': meses_rotacion,
        'cobertura_default': cobertura_default,
        'buckets': buckets,
        'sin_ventas': sorted(sin_ventas, key=lambda x: x['nombre']),
        'totales': {
            'productos_con_ventas': con_ventas,
            'productos_sin_ventas': len(sin_ventas),
            'monto_mensual_total': round(monto_total, 2),
        }
    }


def calcular_alertas_repo_fija(session, dias_aviso=7, meses_rotacion=3,
                                limit_top=8, lab_observer_id=None,
                                incluir_sin_alerta=False):
    """Calcula alertas de reposición para productos con cantidad_reposicion_fija.

    Sirve al card "Alertas Repo fija" del home. Lisandro carga manualmente el
    campo `Producto.cantidad_reposicion_fija`; este helper detecta cuáles de
    esos productos están a punto de tocar el mínimo (o ya lo tocaron) según
    el ritmo de venta reciente, para avisar con `dias_aviso` de anticipación.

    Args:
        session: SQLAlchemy session abierta.
        dias_aviso: int, ventana de aviso (default 7 = 1 semana antes).
        meses_rotacion: int, meses para calcular avg_diario (default 3, igual
            que /pedidos/dia/armar).

    Returns:
        dict {
            'total': int,                # productos con repo fija configurada
            'rojo': int,                 # ya bajo mínimo (urgente)
            'amarillo': int,             # 1-3 días a mínimo
            'verde': int,                # 4-7 días a mínimo
            'sin_alerta': int,           # >7 días o sin ventas
            'top': [                     # top 8 ordenado por urgencia
                {producto_id, observer_id, nombre, stock, minimo,
                 cant_fija, dias_a_min (float|None), nivel ('rojo'|'amarillo'|'verde')}
            ]
        }
    """
    import database

    _DIAS_PROM_MES = 30.42
    dias_rotacion = int(meses_rotacion * _DIAS_PROM_MES)

    # 1. Productos con repo fija seteada + linkeados a Observer.
    #    Si lab_observer_id viene, filtramos por laboratorio (vía ObsProducto).
    q = (session.query(database.Producto.id,
                       database.Producto.observer_id,
                       database.Producto.descripcion,
                       database.Producto.codigo_barra,
                       database.Producto.cantidad_reposicion_fija)
         .filter(database.Producto.cantidad_reposicion_fija.isnot(None),
                 database.Producto.cantidad_reposicion_fija > 0,
                 database.Producto.observer_id.isnot(None)))
    if lab_observer_id:
        q = (q.join(database.ObsProducto,
                    database.ObsProducto.observer_id == database.Producto.observer_id)
              .filter(database.ObsProducto.laboratorio_observer == lab_observer_id))
    rows_prod = q.all()
    if not rows_prod:
        return {'total': 0, 'rojo': 0, 'amarillo': 0, 'verde': 0,
                'sin_alerta': 0, 'top': []}

    obs_ids = [r.observer_id for r in rows_prod]

    # 2. Stock + mínimo por observer_id (sum si hay multi-farmacia).
    from sqlalchemy import func as _f
    stock_rows = (session.query(database.ObsStock.producto_observer,
                                _f.sum(database.ObsStock.stock_actual),
                                _f.sum(database.ObsStock.minimo))
                  .filter(database.ObsStock.producto_observer.in_(obs_ids))
                  .group_by(database.ObsStock.producto_observer)
                  .all())
    stock_map = {r[0]: (int(r[1] or 0), int(r[2] or 0)) for r in stock_rows}

    # 3. Ventas últimos `meses_rotacion` meses completos (avg_diario).
    #    Mismo cálculo que /pedidos/dia/armar (u_rot / dias_rotacion).
    from datetime import date as _d
    hoy = _d.today()
    # Inclusivo: incluye desde hace `meses_rotacion` meses hasta el mes anterior
    # al actual (excluye mes parcial actual para no subestimar el promedio).
    end_anio = hoy.year if hoy.month > 1 else hoy.year - 1
    end_mes = hoy.month - 1 if hoy.month > 1 else 12
    start_mes = end_mes - (meses_rotacion - 1)
    start_anio = end_anio
    while start_mes <= 0:
        start_mes += 12
        start_anio -= 1
    desde_ym = start_anio * 100 + start_mes
    hasta_ym = end_anio * 100 + end_mes
    vm = database.ObsVentaMensual
    ventas_rows = (session.query(vm.producto_observer, _f.sum(vm.unidades))
                   .filter(vm.producto_observer.in_(obs_ids),
                           vm.anio * 100 + vm.mes >= desde_ym,
                           vm.anio * 100 + vm.mes <= hasta_ym)
                   .group_by(vm.producto_observer)
                   .all())
    u_rot_map = {r[0]: float(r[1] or 0) for r in ventas_rows}

    # 4. Por producto: clasificar nivel + punto de pedido + rotación.
    #    punto_pedido = minimo + (avg_diario × dias_aviso): nivel de stock al
    #    cual hay que disparar la compra para que llegue antes de tocar el
    #    mínimo (Diego pidió "una semana antes" → dias_aviso=7 default).
    import math as _math

    from purchase_engine import rotation_index as _rot_idx
    items = []
    for r in rows_prod:
        stock, minimo = stock_map.get(r.observer_id, (0, 0))
        u_rot = u_rot_map.get(r.observer_id, 0.0)
        avg_diario = (u_rot / dias_rotacion) if dias_rotacion else 0
        avg_mensual = avg_diario * _DIAS_PROM_MES if avg_diario else 0
        rotacion = _rot_idx(avg_mensual) if avg_mensual else None  # 'A'|'M'|'B'

        # Punto de pedido + días al punto de pedido.
        if avg_diario > 0:
            punto_pedido = minimo + int(_math.ceil(avg_diario * dias_aviso))
            dias_al_pedido = (stock - punto_pedido) / avg_diario
        else:
            punto_pedido = minimo  # sin ritmo → punto = mín
            dias_al_pedido = None

        if stock <= minimo:
            nivel = 'rojo'
            dias_a_min = 0
        elif avg_diario <= 0:
            nivel = None
            dias_a_min = None
        else:
            dias_a_min = (stock - minimo) / avg_diario
            if dias_a_min <= 3:
                nivel = 'amarillo'
            elif dias_a_min <= dias_aviso:
                nivel = 'verde'
            else:
                nivel = None
        items.append({
            'producto_id': r.id,
            'observer_id': r.observer_id,
            'nombre': r.descripcion,
            'codigo_barra': r.codigo_barra,
            'stock': stock,
            'minimo': minimo,
            'cant_fija': int(r.cantidad_reposicion_fija),
            'avg_diario': round(avg_diario, 2),
            'avg_mensual': round(avg_mensual, 1),
            'rotacion': rotacion,
            'punto_pedido': punto_pedido,
            'dias_al_pedido': round(dias_al_pedido, 1) if dias_al_pedido is not None else None,
            'dias_a_min': round(dias_a_min, 1) if dias_a_min is not None else None,
            'nivel': nivel,
        })

    # 5. Counters + top ordenado (rojo > amarillo > verde, dentro de cada nivel por dias asc).
    rojo = sum(1 for x in items if x['nivel'] == 'rojo')
    amarillo = sum(1 for x in items if x['nivel'] == 'amarillo')
    verde = sum(1 for x in items if x['nivel'] == 'verde')
    sin_alerta = sum(1 for x in items if x['nivel'] is None)

    _orden_nivel = {'rojo': 0, 'amarillo': 1, 'verde': 2, None: 3}
    if incluir_sin_alerta:
        # Pantalla detalle: todos los items con cant_fija ordenados por
        # urgencia (rojo > amarillo > verde > sin_alerta).
        items.sort(key=lambda x: (_orden_nivel[x['nivel']],
                                  x['dias_a_min'] if x['dias_a_min'] is not None else 99999))
        top_items = items[:limit_top] if limit_top else items
    else:
        en_alerta = [x for x in items if x['nivel']]
        en_alerta.sort(key=lambda x: (_orden_nivel[x['nivel']],
                                       x['dias_a_min'] if x['dias_a_min'] is not None else 999))
        top_items = en_alerta[:limit_top] if limit_top else en_alerta
    return {
        'total': len(items),
        'rojo': rojo,
        'amarillo': amarillo,
        'verde': verde,
        'sin_alerta': sin_alerta,
        'top': top_items,
    }


def _sin_acentos(s):
    """lowercase + sin acentos, para matching insensible a tildes.

    PostgreSQL ILIKE es case-insensitive pero NO accent-insensitive:
    '%LOSARTAN%' no matchea 'Losartán'. Por eso normalizamos en Python.
    """
    import unicodedata
    if not s:
        return ''
    s = str(s).lower()
    return ''.join(c for c in unicodedata.normalize('NFKD', s)
                   if not unicodedata.combining(c))


def _ventana_12m_ym(hoy=None):
    """Devuelve (desde_ym, hasta_ym) como ints YYYYMM para los últimos 12 meses."""
    from datetime import date as _date
    if hoy is None:
        hoy = _date.today()
    hasta = hoy.year * 100 + hoy.month
    desde_y = hoy.year - 1
    desde_m = hoy.month + 1
    if desde_m > 12:
        desde_m -= 12
        desde_y += 1
    return desde_y * 100 + desde_m, hasta


def analizar_gap_marcas(session, lab_observer_id):
    """Informe 1 — Gap de captura por marca estrella.

    Para cada marca del portfolio de referencia del lab, cruza contra las
    ventas propias (u12m + monto) de los productos de ese lab cuya descripción
    matchea el patrón de la marca. Detecta marcas líderes a nivel país que la
    farmacia vende poco o nada → oportunidad de captura.

    Returns dict {nombre_lab, nota, total_u12m, total_monto, marcas: [...]} o None.
    """
    import referencia_mercado
    ref = referencia_mercado.referencia_de_lab(lab_observer_id)
    if not ref:
        return None
    from sqlalchemy import func as _f
    desde, hasta = _ventana_12m_ym()

    marcas_out = []
    total_u, total_m = 0, 0.0
    for marca, molecula, indicacion, top10, match in ref['marcas']:
        prods = (session.query(database.ObsProducto.observer_id)
                 .filter(database.ObsProducto.laboratorio_observer == lab_observer_id,
                         database.ObsProducto.descripcion.ilike(f'%{match}%'),
                         database.ObsProducto.fecha_baja.is_(None))
                 .all())
        pids = [p[0] for p in prods]
        u12m, monto = 0, 0.0
        if pids:
            vm = database.ObsVentaMensual
            row = (session.query(_f.sum(vm.unidades), _f.sum(vm.monto))
                   .filter(vm.producto_observer.in_(pids),
                           vm.anio * 100 + vm.mes >= desde,
                           vm.anio * 100 + vm.mes <= hasta)
                   .first())
            u12m = int(row[0] or 0)
            monto = float(row[1] or 0)
        total_u += u12m
        total_m += monto
        marcas_out.append({
            'marca': marca, 'molecula': molecula, 'indicacion': indicacion,
            'top10_nacional': top10, 'n_productos': len(pids),
            'u12m': u12m, 'u_mensual': round(u12m / 12.0, 1),
            'monto': round(monto, 2), 'vende': u12m > 0,
        })
    # Orden: top10 nacional primero, dentro de cada grupo por u12m desc.
    marcas_out.sort(key=lambda m: (not m['top10_nacional'], -m['u12m']))
    return {
        'nombre_lab': ref['nombre'], 'nota': ref.get('nota', ''),
        'total_u12m': total_u, 'total_monto': round(total_m, 2),
        'marcas': marcas_out,
    }


def analizar_ranking_vs_nacional(session, lab_observer_id, limit=30):
    """Informe 2 — Mi ranking del lab vs marcas estrella nacionales.

    Top `limit` productos del lab por unidades 12m, marcando cuáles
    corresponden a una marca estrella (top 10 nacional). Valida si el mix
    propio sigue al mercado o tiene perfil distinto.

    Returns dict {nombre_lab, productos: [...], n_estrella_en_top} o None.
    """
    import referencia_mercado
    ref = referencia_mercado.referencia_de_lab(lab_observer_id)
    if not ref:
        return None
    from sqlalchemy import func as _f
    desde, hasta = _ventana_12m_ym()

    vm = database.ObsVentaMensual
    op = database.ObsProducto
    rows = (session.query(op.observer_id, op.descripcion,
                          _f.coalesce(_f.sum(vm.unidades), 0).label('u12m'),
                          _f.coalesce(_f.sum(vm.monto), 0).label('m12m'))
            .outerjoin(vm, (vm.producto_observer == op.observer_id) &
                       (vm.anio * 100 + vm.mes >= desde) &
                       (vm.anio * 100 + vm.mes <= hasta))
            .filter(op.laboratorio_observer == lab_observer_id,
                    op.fecha_baja.is_(None))
            .group_by(op.observer_id, op.descripcion)
            .order_by(_f.coalesce(_f.sum(vm.unidades), 0).desc())
            .limit(limit)
            .all())

    # Mapa de marcas estrella (top10) para tag rápido por substring.
    estrellas = [(marca, match) for marca, _mol, _ind, top10, match
                 in ref['marcas'] if top10]
    productos = []
    n_estrella = 0
    for r in rows:
        desc_up = (r.descripcion or '').upper()
        marca_estrella = None
        for marca, match in estrellas:
            if match.upper() in desc_up:
                marca_estrella = marca
                break
        if marca_estrella:
            n_estrella += 1
        productos.append({
            'observer_id': r.observer_id,
            'descripcion': r.descripcion,
            'u12m': int(r.u12m or 0),
            'u_mensual': round(int(r.u12m or 0) / 12.0, 1),
            'monto': round(float(r.m12m or 0), 2),
            'marca_estrella': marca_estrella,
        })
    return {
        'nombre_lab': ref['nombre'],
        'productos': productos,
        'n_estrella_en_top': n_estrella,
        'n_estrella_total': len(estrellas),
    }


def analizar_cobertura_moleculas(session, lab_observer_id):
    """Informe 3 — Cobertura de moléculas líderes nacionales.

    Por cada molécula del ranking nacional: ¿la vende la farmacia? ¿Con la
    marca del lab de referencia o con competencia/genérico? Detecta dónde se
    puede migrar a la marca líder o capturar más demanda.

    El cruce de "molécula" es por ObsNombreDroga.descripcion (ILIKE).
    Ventas separadas: total de la droga vs lo que aporta el lab de referencia.

    Returns dict {nombre_lab, moleculas: [...]} o None.
    """
    import referencia_mercado
    ref = referencia_mercado.referencia_de_lab(lab_observer_id)
    if not ref:
        return None
    from sqlalchemy import func as _f
    desde, hasta = _ventana_12m_ym()
    vm = database.ObsVentaMensual
    op = database.ObsProducto
    nd = database.ObsNombreDroga

    # Traer todas las drogas una vez y normalizar (sin acentos) para matchear
    # los patrones de referencia sin depender de ILIKE accent-sensitive.
    todas_drogas = [(d[0], _sin_acentos(d[1]))
                    for d in session.query(nd.observer_id, nd.descripcion).all()]

    moleculas_out = []
    for molecula, ranking, marca_roe, lider, match_droga in ref['moleculas_lideres']:
        # Match normalizado: el patrón puede tener '%' como separador (ej.
        # 'AMOXICILINA%CLAVUL' = ambas partes presentes en cualquier orden).
        partes = [_sin_acentos(p) for p in match_droga.split('%') if p.strip()]
        droga_ids = [did for did, dnorm in todas_drogas
                     if all(p in dnorm for p in partes)]
        u_total, u_lab = 0, 0
        n_prod_total, n_prod_lab = 0, 0
        if droga_ids:
            # Productos de esa droga (cualquier lab) con ventas.
            prod_rows = (session.query(op.observer_id, op.laboratorio_observer)
                         .filter(op.nombre_droga_observer.in_(droga_ids),
                                 op.fecha_baja.is_(None)).all())
            pids = [p[0] for p in prod_rows]
            pids_lab = [p[0] for p in prod_rows if p[1] == lab_observer_id]
            n_prod_total, n_prod_lab = len(pids), len(pids_lab)
            if pids:
                row = (session.query(_f.sum(vm.unidades))
                       .filter(vm.producto_observer.in_(pids),
                               vm.anio * 100 + vm.mes >= desde,
                               vm.anio * 100 + vm.mes <= hasta).first())
                u_total = int(row[0] or 0)
            if pids_lab:
                row = (session.query(_f.sum(vm.unidades))
                       .filter(vm.producto_observer.in_(pids_lab),
                               vm.anio * 100 + vm.mes >= desde,
                               vm.anio * 100 + vm.mes <= hasta).first())
                u_lab = int(row[0] or 0)
        u_comp = max(0, u_total - u_lab)
        share_lab = round(u_lab / u_total * 100, 1) if u_total else 0.0
        moleculas_out.append({
            'molecula': molecula, 'ranking': ranking,
            'marca_roemmers': marca_roe, 'lider_mercado': lider,
            'vende': u_total > 0,
            'u12m_total': u_total, 'u12m_lab': u_lab, 'u12m_competencia': u_comp,
            'share_lab_pct': share_lab,
            'n_productos_total': n_prod_total, 'n_productos_lab': n_prod_lab,
        })
    return {
        'nombre_lab': ref['nombre'],
        'moleculas': moleculas_out,
    }
