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

import database
from services.inflacion import DTO_MAXIMO, PRECIO_MINIMO, ajustar


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


def margen(precio_venta, costo):
    """% que queda sobre el precio de venta. None si falta alguno de los dos."""
    if not precio_venta or costo is None:
        return None
    return 100 * (float(precio_venta) - float(costo)) / float(precio_venta)


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
