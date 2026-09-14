"""Consultas de OS inferida por cliente y precio estimado con cobertura OS.

Read-only sobre obs_ventas_detalle + obs_obras_sociales + cliente_os_inferida.
Nunca escribe en tablas obs_* ni modifica ventas_detalle.
"""
from datetime import date, timedelta

from sqlalchemy import func, text

import database


def _hace_un_anio():
    """La ventana del histórico, calculada en Python y no con `INTERVAL '12
    months'`: esa sintaxis es sólo de Postgres y dejaba la función sin tests."""
    return date.today() - timedelta(days=365)


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
    """Qué parte de un producto termina pagando el paciente con esa OS, según el
    histórico real de ventas de los últimos 12 meses.

    Devuelve `{pct_paga_paciente, pct_cobertura_os, n_ventas}` o None si hay
    menos de 3 ventas del par (producto, OS).

    **Cuál de los dos porcentajes usar**: `importe` es BRUTO, y se reparte en
    tres: lo que paga el paciente + lo que va a cargo de la OS + el descuento
    comercial. Entonces

    · `pct_paga_paciente` — lo que el paciente realmente desembolsó. Es el
      número para decirle cuánto le sale, porque el descuento también lo
      beneficia a él.
    · `pct_cobertura_os` — estrictamente lo que cubre el convenio. La diferencia
      entre `100 - pct_paga_paciente` y este es el descuento comercial: medido
      sobre OSDE, el paciente paga el 38% pero la OS sólo cubre el 54%, y el 8%
      restante es descuento.

    Antes esto promediaba `importe_efectivo / importe` — sólo efectivo. Pero el
    42% de los pacientes con obra social paga con TARJETA, y ahí
    `importe_efectivo` queda en 0 y la cuenta concluía que el convenio cubrió el
    100%: decía 89% para OSDE cuando el paciente paga el 38%, y 72% para
    Recetario Solidario cuando paga el 61%.
    """
    if farmacia_id is None:
        import os as _os
        try:
            farmacia_id = int(_os.environ.get('OBSERVER_ID_FARMACIA', '10525'))
        except ValueError:
            farmacia_id = 10525

    V = database.ObsVentaDetalle

    # Las más recientes: con un ORDER BY, el límite deja de ser un recorte
    # arbitrario y pasa a ser "las últimas 200", que es lo que representa el
    # precio de hoy.
    rows = (session.query(
        V.importe, V.importe_efectivo, V.importe_tarjeta,
        V.importe_cheque, V.importe_cuenta_corriente, V.importe_a_cargo_os,
    )
    .filter(
        V.id_farmacia == farmacia_id,
        V.producto_observer == producto_observer_id,
        V.obra_social_observer == obra_social_observer_id,
        V.fecha_estadistica >= _hace_un_anio(),
        V.importe > 0,
    )
    .order_by(V.fecha_estadistica.desc())
    .limit(200).all())

    pagos, coberturas = [], []
    for imp, efec, tarj, cheq, ctacte, a_cargo in rows:
        imp_f = float(imp or 0)
        if imp_f <= 0:
            continue
        pagado = sum(float(x or 0) for x in (efec, tarj, cheq, ctacte))
        cubierto = float(a_cargo or 0)
        # Renglón sin ningún dato financiero: el pago quedó asentado en otra
        # línea de la misma operación. Contarlo diría "cubierto al 100%".
        if pagado == 0 and cubierto == 0:
            continue
        pagos.append(pagado / imp_f)
        coberturas.append(cubierto / imp_f)

    if len(pagos) < 3:
        return None

    return {
        'pct_paga_paciente': round(sum(pagos) / len(pagos) * 100, 1),
        'pct_cobertura_os': round(sum(coberturas) / len(coberturas) * 100, 1),
        'n_ventas': len(pagos),
    }


def calcular_os_inferida():
    """Wrapper que dispara el recálculo completo de OS inferida.
    Reusa el script existente."""
    from scripts.recalcular_os_por_cliente import recalcular
    return recalcular()