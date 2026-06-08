"""Informes proactivos vía Telegram (mismo bot que el asistente).

Uso principal: alertar al dueño cuando hay conversaciones derivadas a operador
que llevan más de X minutos sin respuesta.
"""
import logging
import os
from datetime import datetime, timedelta

import requests
from sqlalchemy import text

log = logging.getLogger(__name__)

_TOKEN    = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
_OWNER_ID = os.environ.get('TELEGRAM_OWNER_CHAT_ID', '').strip()
_DIEGO_ID = os.environ.get('TELEGRAM_DIEGO_CHAT_ID', '').strip()   # reservado, sin uso por ahora
_UMBRAL   = int(os.environ.get('INFORMES_UMBRAL_MINUTOS', '15'))

_TG_URL = 'https://api.telegram.org/bot{token}/sendMessage'


def _esc(s):
    """Escapa caracteres especiales de Markdown v1 de Telegram."""
    return (s or '').replace('_', r'\_').replace('*', r'\*').replace('`', r'\`')


def enviar(chat_id, texto):
    """Envía un mensaje Markdown a un chat_id de Telegram. Retorna True si OK."""
    if not _TOKEN or not chat_id:
        return False
    try:
        r = requests.post(
            _TG_URL.format(token=_TOKEN),
            json={'chat_id': chat_id, 'text': texto,
                  'parse_mode': 'Markdown', 'disable_web_page_preview': True},
            timeout=10,
        )
        if r.status_code != 200:
            log.warning('Telegram informe error %s: %s', r.status_code, r.text[:200])
            return False
        return True
    except requests.RequestException as e:
        log.warning('Telegram informe request error: %s', e)
        return False


def _formato_tiempo(dt):
    """'hace 25 min' / 'hace 2 h 5 min'."""
    delta = datetime.utcnow() - dt
    mins = max(0, int(delta.total_seconds() // 60))
    if mins < 60:
        return f'hace {mins} min'
    h, m = divmod(mins, 60)
    return f'hace {h} h {m} min' if m else f'hace {h} h'


def _query_chats(session, estado_atencion, tipo, minutos):
    """Query genérica para ambos tipos de alerta."""
    corte = datetime.utcnow() - timedelta(minutes=minutos)
    rows = session.execute(text("""
        SELECT c.id,
               c.canal,
               c.nombre_cliente,
               m.texto     AS ultimo_msg,
               m.creado_en AS desde
        FROM   bot_conversaciones c
        JOIN   bot_mensajes m ON m.id = (
            SELECT id FROM bot_mensajes
            WHERE  conversacion_id = c.id AND origen = 'cliente'
            ORDER  BY creado_en DESC
            LIMIT  1
        )
        WHERE  c.estado_atencion = :ea
          AND  m.creado_en       < :corte
          AND  NOT EXISTS (
              SELECT 1 FROM informe_enviado ie
              WHERE  ie.tipo = :tipo
                AND  ie.conversacion_id = c.id
          )
        ORDER  BY m.creado_en ASC
    """), {'ea': estado_atencion, 'corte': corte, 'tipo': tipo}).fetchall()
    return [dict(r._mapping) for r in rows]


def _marcar_enviados(session, chats, tipo):
    for c in chats:
        try:
            session.execute(text("""
                INSERT INTO informe_enviado (tipo, conversacion_id, enviado_en)
                VALUES (:tipo, :cid, NOW())
                ON CONFLICT (tipo, conversacion_id) DO NOTHING
            """), {'tipo': tipo, 'cid': c['id']})
        except Exception:
            pass
    session.commit()


def _bloque_chats(chats, encabezado):
    lines = [encabezado, '']
    for c in chats:
        canal  = (c['canal'] or '').capitalize()
        nombre = _esc(c['nombre_cliente'] or 'Desconocido')
        tiempo = _formato_tiempo(c['desde'])
        raw    = (c['ultimo_msg'] or '').strip()
        msg    = _esc((raw[:60] + '…') if len(raw) > 60 else (raw or '[sin texto]'))
        lines.append(f'• *{nombre}* · {canal} · {tiempo}')
        lines.append(f'  _{msg}_')
        lines.append('')
    return lines


def disparar_sin_atender(session):
    """Consulta chats sin tomar (cola) y sin responder (operador) y notifica al dueño."""
    if not _OWNER_ID:
        return

    umbral = _UMBRAL
    sin_tomar   = _query_chats(session, 'cola',   'sin_tomar',   umbral)
    sin_atender = _query_chats(session, 'humano', 'sin_atender', umbral)

    if not sin_tomar and not sin_atender:
        return

    lines = []
    if sin_tomar:
        lines += _bloque_chats(sin_tomar,
            f'🚨 *Sin tomar* ({len(sin_tomar)}) — nadie los abrió aún')
    if sin_atender:
        lines += _bloque_chats(sin_atender,
            f'🔔 *Sin responder* ({len(sin_atender)}) — operador no contestó')

    lines.append('→ /atencion para atender')

    if enviar(_OWNER_ID, '\n'.join(lines)):
        if sin_tomar:
            _marcar_enviados(session, sin_tomar, 'sin_tomar')
        if sin_atender:
            _marcar_enviados(session, sin_atender, 'sin_atender')
        log.info('Informe: %d sin tomar, %d sin atender', len(sin_tomar), len(sin_atender))


def resetear_conv(session, conv_id):
    """Limpia el registro de informe para una conv (llamar al responder o cerrar).
    Permite que si el cliente vuelve a quedar sin atención, se re-notifique."""
    try:
        session.execute(text("""
            DELETE FROM informe_enviado
            WHERE conversacion_id = :cid
        """), {'cid': conv_id})
        session.commit()
    except Exception as e:
        log.debug('resetear_conv informe error: %s', e)
