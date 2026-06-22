"""Cron interno para los SLA del flujo de reparto.

Cada 60s recorre los pedidos publicados/tomados y:

  1. **Reaviso de publicación**: si pasó `sla_publicacion_reaviso_min` (o
     `× sla_factor_urgente` si el pedido es urgente) desde el `publicado_en`
     sin que nadie lo tome → manda un mensaje al grupo de cadetes
     ("⏰ Pedido #X — N min sin tomar"). Se persiste `reaviso_enviado_en`
     para no mandar más de uno por pedido.

  2. **Retiro vencido**: si pasó `sla_retiro_maximo_min` desde el `tomado_en`
     sin que el cadete marque "Retirado" → desasigna (limpia `cadete_id`,
     `tomado_por_wsap`, `tomado_en`, `publicado_en`) y vuelve a `pendiente`.
     En la planilla reaparece el botón "📤 Publicar".

Lock por socket bind (puerto 12348) para garantizar que SOLO UN worker de
gunicorn corra el cron — mismo patrón que `bot/telegram_grupo.iniciar_polling_thread`.

Activación: env var `REPARTO_SLA_CRON=true`. Apagado por default en local
si no querés que mande reavisos mientras desarrollás. En prod va prendido.
"""
import logging
import os
import threading
import time

log = logging.getLogger(__name__)

USAR_CRON = (os.environ.get('REPARTO_SLA_CRON') or '').lower() in ('1', 'true', 'yes', 'on')
TICK_SECONDS = 60

# Mantener referencia al socket para que GC no lo libere mientras el proceso vive.
_lock_keepalive = []


def iniciar_cron_thread():
    """Arranca el thread del cron. Llamar una vez en app.py al arranque.
    No-op si REPARTO_SLA_CRON está apagado o si OTRO worker ya tiene el lock."""
    if not USAR_CRON:
        log.warning('[sla cron] no arranca: REPARTO_SLA_CRON off')
        return

    # Lock por socket bind: solo UN worker de gunicorn corre el cron.
    # Mismo patrón que el polling de telegram_grupo (puerto distinto).
    LOCK_PORT = 12348
    try:
        import socket as _sk
        s = _sk.socket(_sk.AF_INET, _sk.SOCK_STREAM)
        s.setsockopt(_sk.SOL_SOCKET, _sk.SO_REUSEADDR, 0)
        s.bind(('127.0.0.1', LOCK_PORT))
        s.listen(1)
        _lock_keepalive.append(s)
    except OSError as e:
        log.warning('[sla cron] otro worker ya tiene el lock (port %d) — no arranco acá: %s',
                    LOCK_PORT, e)
        return

    t = threading.Thread(target=_worker, daemon=True, name='reparto_sla_cron')
    t.start()
    log.warning('[sla cron] worker started (lock adquirido)')


def _worker():
    while True:
        try:
            tick()
        except Exception as e:  # noqa: BLE001
            log.error('[sla cron] tick error: %s', e, exc_info=True)
        time.sleep(TICK_SECONDS)


def tick():
    """Una pasada del cron. Expuesta como función para tests."""
    import database
    from bot import envio as _envio_mod
    from bot import telegram_grupo

    cfg = _envio_mod.get_config()
    reaviso_min = int(cfg['sla_publicacion_reaviso_min'])
    maximo_min = int(cfg['sla_publicacion_maximo_min'])  # noqa: F841 — se usa para el badge rojo del template, no para acciones del cron
    retiro_max = int(cfg['sla_retiro_maximo_min'])
    factor = float(cfg['sla_factor_urgente'])

    ahora = database.now_ar()
    P = database.PedidoReparto

    with database.get_db() as s:
        # 1. Reaviso de publicación: publicado_en seteado, sin tomar, sin reaviso aún.
        #    Estado 'publicado' = en el grupo y a la espera de cadete.
        pendientes = (s.query(P)
                      .filter(P.publicado_en.isnot(None),
                              P.estado == 'publicado',
                              P.tomado_por_wsap.is_(None),
                              P.reaviso_enviado_en.is_(None))
                      .all())
        for p in pendientes:
            minutos = (ahora - p.publicado_en).total_seconds() / 60
            umbral = reaviso_min * factor if p.prioridad == 'urgente' else reaviso_min
            if minutos < umbral:
                continue
            # Mandar mensaje nuevo al grupo. Sin botón TOMAR — el botón sigue en el
            # mensaje original. Solo es un "bump" visual para que los cadetes lo vean.
            urgente_mark = ' 🚨' if p.prioridad == 'urgente' else ''
            destino = f' · {p.direccion}' if p.direccion else ''
            texto = f'⏰ Pedido #{p.id} — {int(minutos)} min sin tomar{urgente_mark}{destino}'
            try:
                r = telegram_grupo.publicar_en_grupo(texto)
                if r.get('ok'):
                    p.reaviso_enviado_en = ahora
            except Exception as e:  # noqa: BLE001
                log.error('[sla cron] error mandando reaviso #%d: %s', p.id, e)

        # 2. Retiro vencido: tomado pero no retirado, pasó el umbral.
        #    Desasignar = volver a estado 'pendiente' + limpiar campos del cadete +
        #    limpiar publicado_en para que reaparezca el botón "Publicar" en planilla.
        tomados = (s.query(P)
                   .filter(P.tomado_en.isnot(None),
                           P.retirado_en.is_(None),
                           P.entregado_en.is_(None),
                           P.estado == 'tomado')
                   .all())
        # Recolecto los message_id del grupo a sacarles los botones DESPUÉS del
        # commit (no bloquear el pool con HTTP a Telegram).
        msg_ids_a_limpiar = []
        for p in tomados:
            minutos = (ahora - p.tomado_en).total_seconds() / 60
            if minutos < retiro_max:
                continue
            log.warning('[sla cron] desasignando #%d (cadete %s no retiró en %d min)',
                        p.id, p.tomado_por_wsap, int(minutos))
            if p.waha_msg_id:
                try:
                    msg_ids_a_limpiar.append(int(p.waha_msg_id))
                except (TypeError, ValueError):
                    pass
            p.cadete_id = None
            p.tomado_por_wsap = None
            p.tomado_en = None
            p.tomado_dm_msg_id = None
            p.tomado_dm_user_id = None
            p.publicado_en = None        # ← reaparece botón "📤 Publicar" en planilla
            p.waha_msg_id = None         # mensaje original ya no es válido
            p.reaviso_enviado_en = None  # reset para el próximo ciclo
            p.estado = 'pendiente'

        s.commit()

    # Saco los botones del mensaje viejo del grupo (el botón RETIRADO que dejó
    # el cadete que abandonó el pedido). Fuera del with para no bloquear el pool.
    for mid in msg_ids_a_limpiar:
        try:
            telegram_grupo.sacar_botones_grupo(mid)
        except Exception as e:  # noqa: BLE001
            log.warning('[sla cron] sacar botones grupo msg=%d falló: %s', mid, e)
