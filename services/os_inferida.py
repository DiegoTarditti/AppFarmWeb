"""Consultas de OS inferida por cliente y precio estimado con cobertura OS.

Read-only sobre obs_ventas_detalle + obs_obras_sociales + cliente_os_inferida.
Nunca escribe en tablas obs_* ni modifica ventas_detalle.
"""
from sqlalchemy import func, text

import database


def get_os_inferida(session, cliente_observer_id):
    """OS principal para un cliente (confirmada o inferida). None si no tiene.
    Busca primero en ClienteOsConfirmada (toma precedencia sobre la inferida).
    Devuelve {tiene_os, obra_social_id, obra_social_nombre, confianza_pct, confirmada, confirmado_por}."""
    # 1) OS confirmada por operador
    confirmada = session.query(database.ClienteOsConfirmada).filter_by(
        cliente_observer_id=cliente_observer_id).first()
    if confirmada:
        return {
            'tiene_os': True,
            'obra_social_id': confirmada.obra_social_observer_id,
            'obra_social_nombre': confirmada.obra_social_nombre,
            'confianza_pct': None,
            'confirmada': True,
            'confirmado_por': confirmada.confirmado_por,
        }

    # 2) OS inferida del histórico
    row = (session.query(
        database.ClienteOsInferida.obra_social_observer,
        database.ClienteOsInferida.confianza_pct,
        database.ObsObraSocial.descripcion,
    )
    .outerjoin(database.ObsObraSocial,
               database.ClienteOsInferida.obra_social_observer ==
               database.ObsObraSocial.observer_id)
    .filter(database.ClienteOsInferida.cliente_observer == cliente_observer_id,
            database.ClienteOsInferida.obra_social_observer.isnot(None))
    .order_by(database.ClienteOsInferida.confianza_pct.desc())
    .first())
    if not row:
        return None
    return {
        'tiene_os': True,
        'obra_social_id': row[0],
        'confianza_pct': float(row[1]) if row[1] is not None else None,
        'obra_social_nombre': row[2] or f'OS #{row[0]}',
        'confirmada': False,
    }


def set_os_confirmada(session, cliente_observer_id, obra_social_observer_id,
                      obra_social_nombre, usuario):
    """Upsert OS confirmada para un cliente."""
    from database import ClienteOsConfirmada, now_ar
    obj = session.query(ClienteOsConfirmada).filter_by(
        cliente_observer_id=cliente_observer_id).first()
    if obj:
        obj.obra_social_observer_id = obra_social_observer_id
        obj.obra_social_nombre = obra_social_nombre
        obj.confirmado_por = usuario
        obj.confirmado_en = now_ar()
    else:
        obj = ClienteOsConfirmada(
            cliente_observer_id=cliente_observer_id,
            obra_social_observer_id=obra_social_observer_id,
            obra_social_nombre=obra_social_nombre,
            confirmado_por=usuario,
        )
        session.add(obj)
    session.flush()


def clear_os_confirmada(session, cliente_observer_id):
    """Elimina la OS confirmada; vuelve a usar la inferida."""
    session.query(database.ClienteOsConfirmada).filter_by(
        cliente_observer_id=cliente_observer_id).delete()
    session.flush()


def get_precio_os(session, producto_observer_id, obra_social_observer_id,
                  farmacia_id=None):
    """Precio estimado con cobertura de OS para un producto, basado en el
    histórico real de ventas.

    Calcula AVG(importe_efectivo / importe) de los últimos 12 meses para el
    par (producto, OS). Solo incluye registros con importe > 0 y
    importe_efectivo no nulo.

    Devuelve {precio_paciente_estimado, pct_cobertura_os, n_ventas} o None si
    no hay datos suficientes (mínimo 3 ventas).
    """
    if farmacia_id is None:
        import os as _os
        try:
            farmacia_id = int(_os.environ.get('OBSERVER_ID_FARMACIA', '10525'))
        except ValueError:
            farmacia_id = 10525

    V = database.ObsVentaDetalle

    rows = (session.query(
        V.importe, V.importe_efectivo,
    )
    .filter(
        V.id_farmacia == farmacia_id,
        V.producto_observer == producto_observer_id,
        V.obra_social_observer == obra_social_observer_id,
        V.fecha_estadistica >= func.current_date() - text("INTERVAL '12 months'"),
        V.importe > 0,
        V.importe_efectivo.isnot(None),
    )
    .limit(200).all())

    if len(rows) < 3:
        return None

    ratios = []
    for imp, efec in rows:
        imp_f = float(imp or 0)
        efec_f = float(efec or 0)
        if imp_f > 0:
            ratios.append(efec_f / imp_f)

    if len(ratios) < 3:
        return None

    avg_ratio = sum(ratios) / len(ratios)
    pct_cobertura = round((1 - avg_ratio) * 100, 1)

    return {
        'pct_cobertura_os': pct_cobertura,
        'n_ventas': len(rows),
    }


def calcular_os_inferida():
    """Wrapper que dispara el recálculo completo de OS inferida.
    Reusa el script existente."""
    from scripts.recalcular_os_por_cliente import recalcular
    return recalcular()