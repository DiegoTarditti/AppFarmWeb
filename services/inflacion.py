"""Índice de inflación del costo de compra, y ajuste de precios a pesos de hoy.

Existe porque comparar un precio de julio contra uno de septiembre en pesos
nominales no dice nada: con los costos subiendo ~1,5% mensual, cualquier compra
vieja parece "barata" y cualquier compra nueva parece "cara". Sin esto, el
análisis de rentabilidad le marca al comprador errores que no cometió — medido
sobre jul-sep 2026, el "sobreprecio" del trimestre daba $4.269.436 sin ajustar y
$2.241.314 ajustado: la mitad era inflación.

El índice se mide como un IPC: variación del MISMO producto entre meses
consecutivos, y se toma la MEDIANA de todos los pares. Un promedio de precios
por mes no serviría, porque el mix de lo que se compra cambia mes a mes (un mes
con más Ozempic sube el promedio sin que nada haya aumentado).

El cálculo va en Python y no en SQL a propósito: son pocos miles de renglones,
y en SQL habría que usar `percentile_cont`/`to_char`, que no existen en SQLite
y dejarían la lógica sin tests.
"""
from __future__ import annotations

from decimal import Decimal
from statistics import median

import database

# Renglones que no son un precio de mercado y ensuciarían la medición:
# psicotrópicos y probadores entran facturados a $0, y hay renglones con 99,99%
# de descuento. Son compras reales, pero no sirven para medir cuánto aumentó algo.
PRECIO_MINIMO = 100
DTO_MAXIMO = 95


def _mes_anterior(periodo):
    """'2026-08' → '2026-07'."""
    anio, mes = int(periodo[:4]), int(periodo[5:7])
    return f'{anio - 1}-12' if mes == 1 else f'{anio}-{mes - 1:02d}'


def _precio_por_producto_y_mes(session, desde=None):
    """{(ean, 'YYYY-MM'): precio promedio} de las compras que sirven para medir."""
    q = (session.query(database.InvoiceItem, database.Invoice)
         .join(database.Invoice, database.Invoice.id == database.InvoiceItem.factura_id)
         .filter(database.Invoice.tipo_comprobante == 'FAC',
                 database.InvoiceItem.codigo_barra.isnot(None),
                 database.InvoiceItem.codigo_barra != '',
                 database.InvoiceItem.precio_unitario >= PRECIO_MINIMO))
    if desde:
        q = q.filter(database.Invoice.fecha >= desde)

    acum = {}
    for item, inv in q.all():
        if item.dto is not None and float(item.dto) >= DTO_MAXIMO:
            continue
        if not inv.fecha:
            continue
        clave = (item.codigo_barra, inv.fecha.strftime('%Y-%m'))
        suma, n = acum.get(clave, (0.0, 0))
        acum[clave] = (suma + float(item.precio_unitario), n + 1)
    return {k: suma / n for k, (suma, n) in acum.items()}


def _variacion_por_mes(session, desde=None):
    """[(periodo, variacion_pct, pares)] — cuánto subió cada mes contra el anterior.

    Un "par" es un producto comprado en dos meses consecutivos; su variación de
    precio es el dato. La mediana de los pares del mes es el índice.
    """
    precios = _precio_por_producto_y_mes(session, desde)
    ratios = {}
    for (ean, periodo), precio in precios.items():
        previo = precios.get((ean, _mes_anterior(periodo)))
        if previo and previo > 0:
            ratios.setdefault(periodo, []).append(precio / previo)
    return [(periodo, (median(rs) - 1) * 100, len(rs))
            for periodo, rs in sorted(ratios.items())]


def calcular_indice(session, desde=None):
    """Recalcula el índice desde las compras. Devuelve cuántos períodos escribió.

    NO pisa los períodos cargados a mano (`origen='manual'`): la idea es poder
    reemplazar un mes puntual por un índice externo sin que el recálculo lo borre.
    El caller hace commit.
    """
    manuales = {
        f.periodo for f in session.query(database.IndiceInflacion)
        .filter(database.IndiceInflacion.origen == 'manual').all()
    }
    escritos = 0
    for periodo, variacion, pares in _variacion_por_mes(session, desde):
        if periodo in manuales:
            continue
        fila = session.get(database.IndiceInflacion, periodo)
        if fila is None:
            fila = database.IndiceInflacion(periodo=periodo)
            session.add(fila)
        fila.variacion_pct = round(Decimal(str(variacion)), 4)
        fila.pares = pares
        fila.origen = 'propio'
        fila.calculado_en = database.now_ar()
        escritos += 1
    return escritos


def factores_a_hoy(session, hasta=None):
    """{periodo: factor} para llevar pesos de ese mes a pesos de `hasta`.

    `variacion_pct` de un período es cuánto subió ESE mes contra el anterior, así
    que el factor de un mes acumula las subidas de todos los meses posteriores:
    con ago +1,96% y sep +1,00%, julio = 1,0196 * 1,0100 = 1,0298.

    Incluye el mes anterior al primero con índice (es el que esa primera fila
    describe). Un período sin índice queda en 1.0 — no se inventa inflación.
    """
    filas = (session.query(database.IndiceInflacion)
             .order_by(database.IndiceInflacion.periodo).all())
    if not filas:
        return {}
    var = {f.periodo: float(f.variacion_pct or 0) / 100 for f in filas}
    base = hasta or filas[-1].periodo

    # De atrás hacia adelante: el mes previo a P hereda el factor de P
    # multiplicado por lo que subió P.
    factores = {base: 1.0}
    periodo, acumulado = base, 1.0
    primero = _mes_anterior(filas[0].periodo)
    while periodo > primero:
        acumulado *= 1 + var.get(periodo, 0.0)
        periodo = _mes_anterior(periodo)
        factores[periodo] = acumulado
    for f in filas:                       # posteriores a la base, si los hubiera
        factores.setdefault(f.periodo, 1.0)
    return factores


def ajustar(monto, periodo, factores):
    """Lleva `monto` (de `periodo`) a pesos de la base. Sin índice, no toca nada."""
    if monto is None:
        return None
    return float(monto) * factores.get(periodo, 1.0)
