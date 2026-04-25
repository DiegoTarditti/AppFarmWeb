"""Registro unificado de procesos automáticos (sync, refresh, push, etc.).

Uso típico (context manager):

    from cron_log import registrar
    with registrar('mv_refresh', origen='web') as log:
        # ... hacer la cosa ...
        log.set_mensaje(f'{filas} filas refrescadas')

Si la cosa lanza excepción, la captura y la guarda como error.
"""

import time
import logging
from contextlib import contextmanager

_log = logging.getLogger(__name__)


class _CronLogContext:
    """Objeto exposed dentro del with para que el caller pueda setear mensaje."""
    def __init__(self, log_id):
        self.log_id = log_id
        self._mensaje = None
    def set_mensaje(self, msg):
        self._mensaje = str(msg)[:1000] if msg else None


@contextmanager
def registrar(proceso, origen='web'):
    """Context manager que registra inicio/fin/error del proceso en cron_log.

    Args:
        proceso: nombre corto del proceso (ej. 'sync_ventas', 'mv_refresh').
        origen: 'web' | 'dockerpanel' | 'manual' | etc.
    """
    from database import CronLog, get_db, now_ar

    t0 = time.time()
    log_id = None
    # Crear fila inicial en sesión separada para que se commitee aunque la operación
    # principal abra otra sesión y haga rollback.
    try:
        with get_db() as s:
            entry = CronLog(proceso=proceso, origen=origen, estado='corriendo')
            s.add(entry)
            s.commit()
            log_id = entry.id
    except Exception as e:
        _log.warning('No pude crear fila de cron_log para %s: %s', proceso, e)

    ctx = _CronLogContext(log_id)
    error = None
    try:
        yield ctx
    except Exception as e:
        error = f'{type(e).__name__}: {e}'[:1000]
        raise
    finally:
        if log_id:
            try:
                with get_db() as s:
                    entry = s.get(CronLog, log_id)
                    if entry:
                        entry.fin = now_ar()
                        entry.duracion_ms = int((time.time() - t0) * 1000)
                        entry.estado = 'error' if error else 'ok'
                        entry.mensaje = ctx._mensaje
                        entry.error = error
                        s.commit()
            except Exception as e:
                _log.warning('No pude cerrar fila %s de cron_log: %s', log_id, e)


def registrar_externo(proceso, estado, duracion_ms=None, mensaje=None,
                       error=None, origen='dockerpanel'):
    """Registra un proceso que ya corrió afuera de la app web (ej. DockerPanel).

    A diferencia del context manager, este recibe el resultado terminado.
    """
    from database import CronLog, get_db, now_ar
    from datetime import timedelta

    inicio = now_ar() - timedelta(milliseconds=duracion_ms or 0)
    try:
        with get_db() as s:
            entry = CronLog(
                proceso=proceso, origen=origen,
                inicio=inicio, fin=now_ar(),
                duracion_ms=duracion_ms, estado=estado,
                mensaje=str(mensaje)[:1000] if mensaje else None,
                error=str(error)[:1000] if error else None,
            )
            s.add(entry)
            s.commit()
            return entry.id
    except Exception as e:
        _log.warning('No pude registrar proceso externo %s: %s', proceso, e)
        return None


def purgar_viejos(dias=7):
    """Elimina filas de cron_log más viejas que N días. Idempotente."""
    from database import CronLog, get_db, now_ar
    from datetime import timedelta
    corte = now_ar() - timedelta(days=dias)
    with get_db() as s:
        n = s.query(CronLog).filter(CronLog.inicio < corte).delete()
        s.commit()
        return n
