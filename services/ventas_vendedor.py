"""Estadísticas de ventas por vendedor (operador del POS).

Agrega `obs_ventas_detalle` por `operador_observer` (filtrado por la farmacia
operativa) y cruza con `obs_operadores` para el nombre. Solo ventas (`tipo='V'`).
El operador viene de ObServer (DW.ProductosVendidos.IdOperador); requiere haber
sincronizado ventas_detalle CON el código nuevo (que trae el operador) + operadores.
"""
from sqlalchemy import func

from database import ObsOperador, ObsVentaDetalle
from services.farmacia import farmacia_operativa


def ventas_por_vendedor(session, desde, hasta, id_farmacia=None):
    """Lista de {operador, nombre, operaciones, unidades, importe, renglones}
    para el rango [desde, hasta], ordenada por importe desc."""
    if id_farmacia is None:
        id_farmacia = farmacia_operativa()
    rows = (session.query(
                ObsVentaDetalle.operador_observer,
                func.count(func.distinct(ObsVentaDetalle.id_operacion)).label('operaciones'),
                func.sum(ObsVentaDetalle.cantidad).label('unidades'),
                func.sum(ObsVentaDetalle.importe).label('importe'),
                func.count(ObsVentaDetalle.id_producto_vendido).label('renglones'))
            .filter(ObsVentaDetalle.id_farmacia == id_farmacia,
                    ObsVentaDetalle.operador_observer.isnot(None),
                    ObsVentaDetalle.fecha_estadistica >= desde,
                    ObsVentaDetalle.fecha_estadistica <= hasta,
                    ObsVentaDetalle.tipo_operacion == 'V')
            .group_by(ObsVentaDetalle.operador_observer)
            .all())
    nombres = dict(session.query(ObsOperador.observer_id, ObsOperador.nombre).all())
    out = [{
        'operador': r[0],
        'nombre': nombres.get(r[0]) or '(sin nombre)',
        'operaciones': int(r[1] or 0),
        'unidades': round(float(r[2] or 0), 1),
        'importe': round(float(r[3] or 0)),
        'renglones': int(r[4] or 0),
    } for r in rows]
    out.sort(key=lambda x: -x['importe'])
    return out
