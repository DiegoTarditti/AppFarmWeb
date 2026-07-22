"""Notificaciones a Telegram de alarmas críticas / altas.

Diseño:
- `enviar_telegram(mensaje)` — wrapper sobre la Bot API. Lee TOKEN y CHAT_ID
  de env vars. Si falta cualquiera, devuelve False sin tirar (fail-safe).
- `evaluar_y_notificar(session)` — corre `alarmas.evaluar_todas()`, filtra
  críticas/altas, deduplica con tabla `alarmas_notificadas` y manda solo
  las que corresponden.

Reglas de dedup:
- MIN_GAP_HORAS = 4: si la misma alarma fue notificada hace <4h, no se
  renotifica (evita spam si el cron corre cada 15 min).
- Resurrección: si la alarma estaba marcada 'resuelta' y vuelve a aparecer,
  notifica inmediatamente (es un "volvió a fallar X").
- Cuando una alarma no aparece en evaluar_todas, se marca 'resuelta' (no
  se manda mensaje "se resolvió" — solo notificamos cosas malas).

Severidades a notificar: 'critica' siempre, 'alta' opcional vía flag.
"""
import os
from datetime import datetime, timedelta
from typing import Optional
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

# Cuánto esperar entre notificaciones de la misma alarma
MIN_GAP_HORAS = 4


def _telegram_config():
    """Lee TOKEN + CHAT_ID de env vars. None si falta alguno."""
    token = (os.environ.get('TELEGRAM_BOT_TOKEN') or '').strip()
    chat_id = (os.environ.get('TELEGRAM_CHAT_ID') or '').strip()
    if not token or not chat_id:
        return None
    return {'token': token, 'chat_id': chat_id}


def enviar_telegram(mensaje: str, parse_mode: str = 'HTML') -> tuple[bool, Optional[str]]:
    """Manda un mensaje al bot. Devuelve (ok, error_msg_si_falla).
    No tira excepciones — fail-safe para que un fallo de notificación NO rompa
    el cron job que la dispara.
    """
    cfg = _telegram_config()
    if not cfg:
        return False, 'TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID no configurados'

    url = f"https://api.telegram.org/bot{cfg['token']}/sendMessage"
    data = urlparse.urlencode({
        'chat_id': cfg['chat_id'],
        'text': mensaje,
        'parse_mode': parse_mode,
        'disable_web_page_preview': 'true',
    }).encode('utf-8')

    try:
        req = urlrequest.Request(url, data=data, method='POST')
        with urlrequest.urlopen(req, timeout=10) as r:
            if r.status == 200:
                return True, None
            return False, f'HTTP {r.status}'
    except urlerror.HTTPError as e:
        try:
            body = e.read().decode('utf-8', 'replace')[:300]
        except Exception:
            body = ''
        return False, f'HTTP {e.code} {e.reason} {body}'
    except Exception as e:
        return False, f'{type(e).__name__}: {e}'


def _emoji_severidad(sev: str) -> str:
    return {
        'critica': '🚨',
        'alta': '⚠️',
        'media': '🟡',
        'baja': '🔵',
    }.get(sev, '🔔')


def _formatear_alarma(alarma, app_url: str) -> str:
    """Mensaje HTML para Telegram. Compacto pero informativo."""
    emoji = _emoji_severidad(alarma.severidad)
    link = f'{app_url}{alarma.link}' if alarma.link else f'{app_url}/admin/alarmas'
    return (
        f'{emoji} <b>{alarma.nombre}</b>\n'
        f'Severidad: <b>{alarma.severidad.upper()}</b>\n'
        f'Estado: {alarma.valor_actual}\n'
        f'Threshold: {alarma.threshold}\n'
        f'\n<i>{alarma.accion}</i>\n'
        f'\n→ <a href="{link}">Ver en panel</a>'
    )


def evaluar_y_notificar(session, severidades=('critica', 'alta'), app_url: str = '') -> dict:
    """Evalúa alarmas, deduplica con DB, manda Telegram para las que corresponde.

    Args:
        session: SQLAlchemy session.
        severidades: tupla con qué severidades notificar (default crítica + alta).
        app_url: prefijo del link en el mensaje (ej. https://farmacia-web-rj1z.onrender.com).

    Returns:
        dict con {'evaluadas': N, 'notificadas': N, 'silenciadas': N, 'errores': [...]}.
    """
    import alarmas as _alarmas
    from database import AlarmaNotificada

    ahora = datetime.now()
    gap = timedelta(hours=MIN_GAP_HORAS)

    # force=True: el cron de notificaciones dispara cada 15 min y compara
    # contra `alarmas_notificadas` para deduplicar. Si tomara el cache de 30s
    # de evaluar_todas podría procesar datos desactualizados respecto al estado
    # de la DB en ese instante exacto.
    todas = _alarmas.evaluar_todas(session, force=True)
    activas_por_nombre = {a.nombre: a for a in todas}

    notificadas = 0
    silenciadas = 0
    errores = []

    # Snapshot de filas existentes en alarmas_notificadas.
    # Limit defensivo + filtrar las muy viejas resueltas para no cargar todo
    # si la tabla crece (ej. bug que genera alarmas sin dedup).
    corte_viejo = ahora - timedelta(days=30)
    estados = {
        row.nombre: row
        for row in (session.query(AlarmaNotificada)
                    .filter((AlarmaNotificada.estado_actual != 'resuelta')
                            | (AlarmaNotificada.ultima_notif >= corte_viejo))
                    .limit(500).all())
    }

    # 1) Procesar alarmas que dispararon
    for nombre, alarma in activas_por_nombre.items():
        if alarma.severidad not in severidades:
            silenciadas += 1
            continue

        estado = estados.get(nombre)
        debe_notificar = False
        if estado is None:
            debe_notificar = True  # nunca se notificó
        elif estado.estado_actual == 'resuelta':
            debe_notificar = True  # resucitó
        elif estado.ultima_notif is None or (ahora - estado.ultima_notif) > gap:
            debe_notificar = True  # gap superado
        else:
            silenciadas += 1

        if not debe_notificar:
            # Igual actualizamos estado actual para que figure como activa
            if estado:
                estado.estado_actual = 'activa'
            continue

        ok, err = enviar_telegram(_formatear_alarma(alarma, app_url))
        if not ok:
            errores.append(f'{nombre}: {err}')
            continue

        # Persistir
        if estado is None:
            # Puede EXISTIR en DB pero no en `estados` — el snapshot filtra
            # las 'resueltas' con ultima_notif >30 días. Si una alarma
            # antigua resucita después de eso, cae acá. Sin este re-fetch,
            # session.add() dispara UniqueViolation (nombre es PK).
            estado = (session.query(AlarmaNotificada)
                      .filter_by(nombre=nombre).first())
        if estado is None:
            estado = AlarmaNotificada(
                nombre=nombre,
                ultima_notif=ahora,
                ultima_severidad=alarma.severidad,
                count_total=1,
                estado_actual='activa',
            )
            session.add(estado)
        else:
            estado.ultima_notif = ahora
            estado.ultima_severidad = alarma.severidad
            estado.count_total = (estado.count_total or 0) + 1
            estado.estado_actual = 'activa'
        notificadas += 1

    # 2) Alarmas previamente activas que ya no aparecen → marcar 'resuelta'
    for nombre, estado in estados.items():
        if nombre not in activas_por_nombre and estado.estado_actual == 'activa':
            estado.estado_actual = 'resuelta'

    session.commit()
    return {
        'evaluadas': len(todas),
        'notificadas': notificadas,
        'silenciadas': silenciadas,
        'errores': errores,
    }
