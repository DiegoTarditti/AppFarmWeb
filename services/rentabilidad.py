"""Costeo de productos para el análisis de rentabilidad.

Qué costo se usa y por qué: **el de reposición** (la última compra), no el
promedio de lo comprado. La pregunta que toma decisiones es "si vendo uno más
hoy y lo repongo, ¿gano?", y eso lo contesta lo que cuesta reponerlo hoy. Que
se hayan vendido unidades de stock viejo no lo invalida.

Medido sobre la base real: elegir entre último costo y promedio ponderado mueve
el número 0,52% (mediana sobre 1.236 productos con 2+ compras), así que se
eligió el que conceptualmente contesta la pregunta, no el que da distinto. El
indicador de calidad entonces NO es la cobertura sino la **antigüedad** del
costo: un costo de hace 5 días sirve aunque se haya comprado 1 y vendido 19;
uno de hace 90 días es dudoso aunque la cobertura sea del 100%.

Tres trampas que este módulo tiene que respetar:

1. **Notas de crédito sin signo.** Los tres caminos que cargan facturas guardan
   el signo distinto, y en producción los 86 renglones de NCR están TODOS en
   positivo. Hay que firmar por `tipo_comprobante`, nunca por el signo del
   renglón: si no, una devolución cuenta como compra.

2. **Renglones a costo cero.** Psicotrópicos, probadores y un medidor de
   glucemia entran facturados a $0 o con 99,99% de descuento. Son compras
   reales y su margen es legítimo, pero si contaran como "mejor precio" toda
   otra compra del mismo producto figuraría con un sobreprecio de cientos de
   miles por ciento. Se excluyen SÓLO del baseline de mejor precio.

3. **Inflación.** Comparar contra el mejor precio del trimestre sin ajustar
   convierte la inflación en "mala compra": sobre jul-sep 2026 eso inflaba el
   sobreprecio de $2.241.314 a $4.269.436. Ver `services/inflacion.py`.
"""
from __future__ import annotations

from sqlalchemy import case, func

import database
from services.inflacion import DTO_MAXIMO, PRECIO_MINIMO, ajustar

# Operaciones que forman la venta NETA. Las 'D' (devolución) vienen con cantidad
# e importe NEGATIVOS, así que restan solas al sumar. Mismo criterio que
# `sync_ventas_mensuales`, para que los dos números sean comparables.
TIPOS_VENTA = ('V', 'D')


# Una compra que quedó dentro de este % del mejor precio no se marca como
# sobreprecio: es redondeo, una diferencia de día, o el resto de inflación que el
# índice mensual no captura. Sin umbral el total del trimestre pasa de
# $2.241.314 a $3.321.603, casi todo en diferencias de centavos que no son una
# decisión de compra.
UMBRAL_SOBREPRECIO_PCT = 2.0


def _es_precio_de_mercado(item):
    """Si este renglón sirve como referencia de precio (ver trampa 2)."""
    if item.precio_unitario is None or float(item.precio_unitario) < PRECIO_MINIMO:
        return False
    return not (item.dto is not None and float(item.dto) >= DTO_MAXIMO)


def _renglones(session, eans=None):
    """Renglones de compra con su factura, ordenados por fecha."""
    q = (session.query(database.InvoiceItem, database.Invoice)
         .join(database.Invoice, database.Invoice.id == database.InvoiceItem.factura_id)
         .filter(database.InvoiceItem.codigo_barra.isnot(None),
                 database.InvoiceItem.codigo_barra != '',
                 database.Invoice.fecha.isnot(None),
                 database.Invoice.tipo_comprobante.in_(('FAC', 'NCR'))))
    if eans is not None:
        eans = list(eans)
        if not eans:
            return []
        q = q.filter(database.InvoiceItem.codigo_barra.in_(eans))
    return sorted(q.all(), key=lambda par: (par[1].fecha, par[1].id))


def costos_por_ean(session, factores=None, eans=None, hoy=None,
                   umbral_pct=UMBRAL_SOBREPRECIO_PCT):
    """{ean: costeo} — costo de reposición, mejor precio y sobreprecio por compra.

    `factores` viene de `inflacion.factores_a_hoy()`: lleva cada precio a pesos
    de hoy antes de compararlos. Sin factores no ajusta nada (y entonces parte
    del "sobreprecio" va a ser inflación, ver trampa 3).

    El costo de reposición se devuelve NOMINAL, tal como se pagó. Proyectarlo a
    hoy con el índice sería inventar un precio que nadie cotizó; para eso está
    `dias_costo`, que dice qué tan confiable es.
    """
    factores = factores or {}
    hoy = hoy or database.now_ar().date()
    out = {}

    for item, inv in _renglones(session, eans):
        ean = item.codigo_barra
        d = out.setdefault(ean, {
            'ean': ean,
            'costo_reposicion': None, 'fecha_costo': None, 'dias_costo': None,
            'proveedor_costo': None,
            'unidades_compradas': 0, 'unidades_devueltas': 0,
            'mejor_precio': None, 'fecha_mejor': None,
            'compras': [], 'sobreprecio_total': 0.0,
        })
        cantidad = abs(float(item.cantidad or 0))
        # Trampa 1: el signo lo define el comprobante, no el renglón.
        if inv.tipo_comprobante == 'NCR':
            d['unidades_devueltas'] += cantidad
            continue
        d['unidades_compradas'] += cantidad

        precio = float(item.precio_unitario) if item.precio_unitario is not None else None
        # El costo de reposición es la última compra, tenga o no precio de mercado:
        # una compra a $0 es igual de real para reponer.
        if precio is not None:
            d['costo_reposicion'] = precio
            d['fecha_costo'] = inv.fecha
            d['dias_costo'] = (hoy - inv.fecha).days
            d['proveedor_costo'] = inv.proveedor_razon

        if not _es_precio_de_mercado(item):
            continue
        periodo = inv.fecha.strftime('%Y-%m')
        d['compras'].append({
            'fecha': inv.fecha, 'cantidad': cantidad,
            'precio': precio, 'precio_ajustado': ajustar(precio, periodo, factores),
            'proveedor': inv.proveedor_razon, 'factura_id': inv.id,
            'sobreprecio': 0.0, 'sobre_pct': 0.0,
        })

    for d in out.values():
        _marcar_sobreprecio(d, umbral_pct)
    return out


def _marcar_sobreprecio(costeo, umbral_pct):
    """Compara cada compra contra la más barata del período, ya en pesos de hoy."""
    compras = costeo['compras']
    if not compras:
        return
    mejor = min(compras, key=lambda c: c['precio_ajustado'])
    costeo['mejor_precio'] = mejor['precio_ajustado']
    costeo['fecha_mejor'] = mejor['fecha']
    if len(compras) < 2 or not mejor['precio_ajustado']:
        return          # con una sola compra no hay con qué comparar
    for c in compras:
        exceso = c['precio_ajustado'] - mejor['precio_ajustado']
        pct = 100 * exceso / mejor['precio_ajustado']
        if pct <= umbral_pct:
            continue
        c['sobreprecio'] = exceso * c['cantidad']
        c['sobre_pct'] = pct
        costeo['sobreprecio_total'] += c['sobreprecio']


# ── Cruce con ventas ────────────────────────────────────────────────────────

def producto_por_ean(session, eans):
    """{producto_observer: ean} — a qué EAN comprado se le imputan sus ventas.

    Dos relaciones torcidas que hay que resolver acá:

    · Un EAN puede apuntar a VARIOS productos (6,78% de los que compramos, hasta
      8 casos). No son productos distintos: es el mismo item cargado varias veces
      en ObServer — el algodón Estrella está 8 veces, la Nivea Soft 5. Así que
      las ventas de todos ellos suman al mismo EAN; quedarse con uno solo, que
      es lo que hace un `DISTINCT ON`, SUBCUENTA las ventas.

    · Un producto puede tener VARIOS EAN (10.766 tienen 2, 2.132 tienen 3). Si
      compramos bajo dos de ellos, sus ventas se contarían dos veces. Pasa en 6
      productos, pero se resuelve igual: cada producto se imputa a un solo EAN,
      el menor, que es estable entre corridas.
    """
    q = (session.query(database.ObsCodigoBarras.producto_observer,
                       database.ObsCodigoBarras.codigo_barras)
         .filter(database.ObsCodigoBarras.codigo_barras.in_(list(eans))))
    elegido = {}
    for producto, ean in q.all():
        if producto not in elegido or ean < elegido[producto]:
            elegido[producto] = ean
    return elegido


def ventas_por_ean(session, desde=None, hasta=None, eans=None):
    """{ean: ventas} — unidades y facturación netas del período.

    La facturación usa `importe_neto` (lo que realmente paga el cliente) y cae a
    `importe` donde todavía no está sincronizado. `neto_pct` dice qué parte del
    total tiene el dato bueno: `importe` es BRUTO y sobreestima ~2,1% en general
    y hasta 5,95% en ventas particulares, así que sin ese aviso el margen queda
    inflado sin que se note.
    """
    if not eans:
        return {}
    imputado = producto_por_ean(session, eans)
    if not imputado:
        return {}

    V = database.ObsVentaDetalle
    q = (session.query(
            V.producto_observer,
            func.sum(V.cantidad),
            func.sum(func.coalesce(V.importe_neto, V.importe)),
            func.sum(case((V.importe_neto.isnot(None), V.importe), else_=0)),
            func.sum(V.importe),
            func.sum(func.coalesce(V.importe_a_cargo_os, 0)))
         .filter(V.producto_observer.in_(list(imputado)),
                 V.tipo_operacion.in_(TIPOS_VENTA)))
    if desde:
        q = q.filter(V.fecha_estadistica >= desde)
    if hasta:
        q = q.filter(V.fecha_estadistica <= hasta)

    out = {}
    for producto, unidades, facturacion, con_neto, bruto, a_cargo_os in q.group_by(V.producto_observer):
        ean = imputado[producto]
        d = out.setdefault(ean, {'ean': ean, 'unidades': 0.0, 'facturacion': 0.0,
                                 'bruto': 0.0, '_con_neto': 0.0, 'a_cargo_os': 0.0})
        d['unidades'] += float(unidades or 0)
        d['facturacion'] += float(facturacion or 0)
        d['bruto'] += float(bruto or 0)
        d['_con_neto'] += float(con_neto or 0)
        d['a_cargo_os'] += float(a_cargo_os or 0)

    for d in out.values():
        d['precio_promedio'] = d['facturacion'] / d['unidades'] if d['unidades'] else None
        d['neto_pct'] = 100 * d['_con_neto'] / d['bruto'] if d['bruto'] else 0.0
        del d['_con_neto']
    return out


def ventas_por_obra_social(session, ean, desde=None, hasta=None):
    """Desglose de un producto por convenio — el mismo deja distinto margen según quién cubre.

    Importa porque el 47,5% de las ventas son por obra social: un producto puede
    dejar 36% con una y perder plata con otra, y eso no se ve en el total.

    `a_cargo_os` es lo que el convenio DEBERÍA pagar, no lo que liquidó: los
    débitos por recetas devueltas no están conciliados (la tabla existe y está
    vacía). Para el margen real de un convenio, es una cota superior.
    """
    imputado = producto_por_ean(session, [ean])
    if not imputado:
        return []

    V = database.ObsVentaDetalle
    q = (session.query(
            V.obra_social_observer, V.es_venta_particular,
            func.sum(V.cantidad),
            func.sum(func.coalesce(V.importe_neto, V.importe)),
            func.sum(func.coalesce(V.importe_a_cargo_os, 0)))
         .filter(V.producto_observer.in_(list(imputado)),
                 V.tipo_operacion.in_(TIPOS_VENTA)))
    if desde:
        q = q.filter(V.fecha_estadistica >= desde)
    if hasta:
        q = q.filter(V.fecha_estadistica <= hasta)

    nombres = dict(session.query(database.ObsObraSocial.observer_id,
                                 database.ObsObraSocial.descripcion).all())
    filas = []
    for os_id, particular, unidades, facturacion, a_cargo_os in q.group_by(
            V.obra_social_observer, V.es_venta_particular):
        filas.append({
            'obra_social_id': os_id,
            'obra_social': 'Particular' if particular else nombres.get(os_id, 'Sin identificar'),
            'particular': bool(particular),
            'unidades': float(unidades or 0),
            'facturacion': float(facturacion or 0),
            'a_cargo_os': float(a_cargo_os or 0),
        })
    return sorted(filas, key=lambda f: -f['facturacion'])


def margen(precio_venta, costo):
    """% que queda sobre el precio de venta. None si falta alguno de los dos."""
    if not precio_venta or costo is None:
        return None
    return 100 * (float(precio_venta) - float(costo)) / float(precio_venta)


def sospecha_unidad(precio_venta, costo, ratio=0.5):
    """True si el precio de venta está tan por debajo del costo que lo más
    probable es que la unidad de compra no sea la de venta.

    Caso real: "PROFIL PRIME ZERO 12 X 3" se compra de a 1 caja a $41.952 y se
    vende como "PRIME ZERO ENV x 3" a $4.500 — el margen sale −832% cuando en
    realidad la caja trae 12 y el margen es ~+22%. No se corrige solo porque el
    tamaño del pack no está en ningún campo: se marca para que nadie decida con
    ese número. Son 2 de los 90 productos con margen negativo; los otros 83 son
    pérdidas plausibles y hay que mostrarlas como tales.
    """
    if not precio_venta or not costo or costo <= 0:
        return False
    return float(precio_venta) < float(costo) * ratio


def confianza_costo(dias):
    """Semáforo de la antigüedad del costo — es el indicador de calidad del margen.

    Con los costos subiendo ~1,5% mensual, cuanto más viejo el costo más
    optimista queda el margen. Distribución real: 1.417 productos hasta 15 días,
    850 entre 16 y 30, 218 entre 31 y 60, y 355 de más de 60.
    """
    if dias is None:
        return 'sin_costo'
    if dias <= 15:
        return 'confiable'
    if dias <= 30:
        return 'aceptable'
    if dias <= 60:
        return 'optimista'
    return 'no_decidir'
