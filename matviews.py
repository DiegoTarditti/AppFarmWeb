"""Helpers para refrescar vistas materializadas y consultar su estado."""

import logging
import time

from sqlalchemy import text

_log = logging.getLogger(__name__)

# Vistas registradas. Agregar acá cuando se cree una nueva.
MATVIEWS = ['mv_stats_drogas']


def refrescar_matview(session, view_name, concurrently=True):
    """Refresca una vista materializada y loguea el resultado en mv_refresh_log.

    Devuelve dict con stats: { 'ok', 'duracion_ms', 'filas', 'error' }.
    """
    from database import MvRefreshLog

    if view_name not in MATVIEWS:
        raise ValueError(f'Vista no registrada: {view_name}')

    t0 = time.time()
    error = None
    filas = None
    try:
        # CONCURRENTLY requiere que la vista ya tenga datos. Si está vacía
        # (recién creada con WITH NO DATA), el primer refresh debe ser sin CONCURRENTLY.
        if concurrently:
            try:
                session.execute(text(f'REFRESH MATERIALIZED VIEW CONCURRENTLY {view_name}'))
            except Exception as e:
                # Fallback: vista vacía, refresh sin CONCURRENTLY.
                if 'has not been populated' in str(e) or 'cannot refresh materialized view' in str(e).lower():
                    session.rollback()
                    session.execute(text(f'REFRESH MATERIALIZED VIEW {view_name}'))
                else:
                    raise
        else:
            session.execute(text(f'REFRESH MATERIALIZED VIEW {view_name}'))
        filas = session.execute(text(f'SELECT COUNT(*) FROM {view_name}')).scalar()
        session.commit()
    except Exception as e:
        session.rollback()
        error = str(e)[:500]
        _log.warning('Refresh de %s falló: %s', view_name, error)

    duracion_ms = int((time.time() - t0) * 1000)
    log = MvRefreshLog(
        view_name=view_name,
        duracion_ms=duracion_ms,
        filas=filas,
        error=error,
    )
    session.add(log)
    session.commit()
    return {'ok': error is None, 'duracion_ms': duracion_ms, 'filas': filas, 'error': error}


def estado_matview(session, view_name):
    """Devuelve dict con info del último refresh:
        { 'view_name', 'ultimo_refresh', 'horas_desde', 'filas', 'estado' }
    Estado: 'fresco' (<24h), 'viejo' (>=24h <72h), 'muy_viejo' (>=72h), 'nunca'.
    """
    from database import MvRefreshLog, now_ar

    log = (session.query(MvRefreshLog)
           .filter(MvRefreshLog.view_name == view_name,
                   MvRefreshLog.error.is_(None))
           .order_by(MvRefreshLog.refrescada_en.desc()).first())

    if not log:
        return {'view_name': view_name, 'ultimo_refresh': None,
                'horas_desde': None, 'filas': None, 'estado': 'nunca'}

    horas = (now_ar() - log.refrescada_en).total_seconds() / 3600
    if horas < 24:
        estado = 'fresco'
    elif horas < 72:
        estado = 'viejo'
    else:
        estado = 'muy_viejo'
    return {
        'view_name':      view_name,
        'ultimo_refresh': log.refrescada_en.isoformat(),
        'horas_desde':    round(horas, 1),
        'filas':          log.filas,
        'estado':         estado,
    }


def estado_todas_matviews(session):
    return {v: estado_matview(session, v) for v in MATVIEWS}
