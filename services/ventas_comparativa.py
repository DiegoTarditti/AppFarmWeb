"""Comparación de ventas año contra año, agregada por mes.

Para un año elegido vs el anterior, agrega `obs_ventas_detalle` por mes y
devuelve tickets / importe / unidades alineados 1..12, con variación % mes a mes.
Un ticket = una operación (`id_operacion`). Solo ventas (`tipo_operacion='V'`).

Totales: se comparan en modo "acumulado justo" (YTD) — solo hasta el último mes
con datos del año actual, contra los MISMOS meses del año anterior. Así no se
castiga al año en curso por los meses que todavía no pasaron.

Fuente: ObServer DW.ProductosVendidos → sync a obs_ventas_detalle.
"""
from sqlalchemy import case, func

from database import ObsVentaDetalle
from services.farmacia import farmacia_operativa

_MESES = ['', 'Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun',
          'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']


def _var_pct(cur, prev):
    """Variación % de cur respecto de prev. None si no hay base de comparación."""
    if not prev:
        return None
    return round((cur - prev) / prev * 100, 1)


def anios_disponibles(session, id_farmacia=None):
    """Años (int) con ventas cargadas, desc. Para el selector de la pantalla."""
    if id_farmacia is None:
        id_farmacia = farmacia_operativa()
    rows = (session.query(ObsVentaDetalle.anio)
            .filter(ObsVentaDetalle.id_farmacia == id_farmacia,
                    ObsVentaDetalle.tipo_operacion == 'V',
                    ObsVentaDetalle.anio.isnot(None))
            .distinct().order_by(ObsVentaDetalle.anio.desc()).all())
    return [r[0] for r in rows]


def comparativa_anual(session, anio, anio_prev=None, id_farmacia=None,
                      mes_tope=None, mes_parcial=None):
    """Devuelve dict con la serie mensual de `anio` vs `anio_prev` (default anio-1).

    - mes_tope: último mes COMPLETO a incluir en los totales YTD. Si None, se usa
      el último mes con datos del año actual. Sirve para no comparar un mes en
      curso (parcial) contra el mismo mes ya cerrado del año anterior.
    - mes_parcial: número de mes en curso (se marca `parcial=True` en la serie).

    Estructura:
      meses: [ {mes, nombre, parcial, cur:{tickets,importe,unidades},
                prev:{...}, var_tickets, var_importe}, ... x12 ]
      totales: {cur:{...}, prev:{...}, var_tickets, var_importe, ticket_prom_cur/prev,
                meses_comparados, hasta_mes_nombre}
      meta: {anio, anio_prev}
    """
    if anio_prev is None:
        anio_prev = anio - 1
    if id_farmacia is None:
        id_farmacia = farmacia_operativa()

    # Transacciones (tickets): distinct IdOperacion de ventas — una venta real.
    # Ítems (renglones): cantidad de líneas de producto vendidas (= "Cant. Oper."
    #   del Analítico de ObServer, que en realidad cuenta líneas, no operaciones).
    # Importe y unidades: NETOS de devoluciones — las 'D' tienen importe y
    #   cantidad negativos, así que sumar V+D descuenta la devolución.
    tickets_v = func.count(func.distinct(
        case((ObsVentaDetalle.tipo_operacion == 'V', ObsVentaDetalle.id_operacion))))
    renglones_v = func.sum(case((ObsVentaDetalle.tipo_operacion == 'V', 1), else_=0))
    devol_imp = func.sum(
        case((ObsVentaDetalle.tipo_operacion == 'D', ObsVentaDetalle.importe), else_=0))
    rows = (session.query(
                ObsVentaDetalle.anio,
                ObsVentaDetalle.mes,
                tickets_v.label('tickets'),
                renglones_v.label('renglones'),
                func.sum(ObsVentaDetalle.cantidad).label('unidades'),
                func.sum(ObsVentaDetalle.importe).label('importe'),
                devol_imp.label('devol'))
            .filter(ObsVentaDetalle.id_farmacia == id_farmacia,
                    ObsVentaDetalle.tipo_operacion.in_(['V', 'D']),
                    ObsVentaDetalle.anio.in_([anio, anio_prev]),
                    ObsVentaDetalle.mes.isnot(None))
            .group_by(ObsVentaDetalle.anio, ObsVentaDetalle.mes)
            .all())

    # (anio, mes) -> métricas. devol se guarda como magnitud positiva (para mostrar).
    data = {}
    for r in rows:
        data[(r.anio, r.mes)] = {
            'tickets': int(r.tickets or 0),
            'renglones': int(r.renglones or 0),
            'importe': round(float(r.importe or 0)),
            'unidades': round(float(r.unidades or 0), 1),
            'devol': round(-float(r.devol or 0)),
        }

    _cero = {'tickets': 0, 'renglones': 0, 'importe': 0, 'unidades': 0, 'devol': 0}
    meses = []
    ultimo_mes_cur = 0
    for m in range(1, 13):
        cur = data.get((anio, m), dict(_cero))
        prev = data.get((anio_prev, m), dict(_cero))
        if cur['tickets']:
            ultimo_mes_cur = m
        meses.append({
            'mes': m,
            'nombre': _MESES[m],
            'parcial': (m == mes_parcial),
            'cur': cur,
            'prev': prev,
            'var_tickets': _var_pct(cur['tickets'], prev['tickets']),
            'var_renglones': _var_pct(cur['renglones'], prev['renglones']),
            'var_importe': _var_pct(cur['importe'], prev['importe']),
        })

    # Totales YTD justos: solo meses COMPLETOS en ambos años. Si el año está en
    # curso, mes_tope excluye el mes parcial para no comparar medio mes vs uno
    # entero. Si no se pasó, cae al último mes con datos.
    tope = mes_tope if mes_tope else (ultimo_mes_cur or 12)
    tope = max(1, min(12, tope))
    tot_cur = {'tickets': 0, 'renglones': 0, 'importe': 0, 'unidades': 0, 'devol': 0}
    tot_prev = {'tickets': 0, 'renglones': 0, 'importe': 0, 'unidades': 0, 'devol': 0}
    for mrow in meses[:tope]:
        for k in tot_cur:
            tot_cur[k] += mrow['cur'][k]
            tot_prev[k] += mrow['prev'][k]

    totales = {
        'cur': tot_cur,
        'prev': tot_prev,
        'var_tickets': _var_pct(tot_cur['tickets'], tot_prev['tickets']),
        'var_renglones': _var_pct(tot_cur['renglones'], tot_prev['renglones']),
        'var_importe': _var_pct(tot_cur['importe'], tot_prev['importe']),
        'var_unidades': _var_pct(tot_cur['unidades'], tot_prev['unidades']),
        'ticket_prom_cur': round(tot_cur['importe'] / tot_cur['tickets']) if tot_cur['tickets'] else 0,
        'ticket_prom_prev': round(tot_prev['importe'] / tot_prev['tickets']) if tot_prev['tickets'] else 0,
        'items_x_venta_cur': round(tot_cur['renglones'] / tot_cur['tickets'], 2) if tot_cur['tickets'] else 0,
        'meses_comparados': tope,
        'hasta_mes_nombre': _MESES[tope],
    }

    return {'meses': meses, 'totales': totales,
            'meta': {'anio': anio, 'anio_prev': anio_prev}}
