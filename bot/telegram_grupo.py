"""Bot de Telegram para el grupo de cadetes (reemplaza WAHA).

Diferencias vs whatsapp_grupo.py:
- Telegram tiene API oficial (gratis, sin riesgo de ban) y soporta:
  * Botones inline (TOMAR atómico vía callback_query)
  * Edición de mensajes propios del bot (tachar TOMAR cuando se toma)
  * Mensajes 1:1 (DM) sin necesidad de prefix tipo '@c.us'
- El bot recibe TODOS los mensajes del grupo SI tiene /setprivacy DISABLE en
  BotFather (clave para detectar frases tipo 'tomo' sin que mencionen al bot).

Identidad del cadete en Telegram:
- Cada usuario tiene un `user_id` (int, ej. 8803285963) que persiste para
  siempre y es propio del cadete. Se guarda en Cadete.telegram_user_id.
- Auto-magic: al primer TOMAR del cadete (callback) capturamos su user_id y
  matcheamos contra Cadete.nombre o lo guardamos para asignación posterior.
- Para DM 1:1 también se usa el user_id como chat_id (Telegram lo acepta).
"""
import logging
import os

import requests

log = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get('TELEGRAM_CADETES_BOT_TOKEN', '').strip()
GRUPO_CHAT_ID = os.environ.get('TELEGRAM_CADETES_GRUPO_CHAT_ID', '').strip()
WEBHOOK_SECRET = os.environ.get('TELEGRAM_CADETES_WEBHOOK_SECRET', '').strip()

API_BASE = f'https://api.telegram.org/bot{BOT_TOKEN}' if BOT_TOKEN else ''


def _post(method, **kwargs):
    """POST a la Bot API. Devuelve dict con la response de Telegram + http_ok."""
    if not API_BASE:
        return {'ok': False, 'error': 'TELEGRAM_CADETES_BOT_TOKEN no configurado'}
    try:
        r = requests.post(f'{API_BASE}/{method}', timeout=20, **kwargs)
    except Exception as e:  # noqa: BLE001
        log.exception('Telegram %s falló', method)
        return {'ok': False, 'error': str(e)}
    try:
        data = r.json()
    except ValueError:
        return {'ok': False, 'error': f'HTTP {r.status_code}: {r.text[:200]}'}
    if not data.get('ok'):
        return {'ok': False, 'error': data.get('description') or 'unknown', 'raw': data}
    return {'ok': True, 'result': data.get('result')}


def _get(method, **params):
    if not API_BASE:
        return {'ok': False, 'error': 'TELEGRAM_CADETES_BOT_TOKEN no configurado'}
    try:
        r = requests.get(f'{API_BASE}/{method}', params=params, timeout=15)
        data = r.json()
    except Exception as e:  # noqa: BLE001
        return {'ok': False, 'error': str(e)}
    if not data.get('ok'):
        return {'ok': False, 'error': data.get('description') or 'unknown', 'raw': data}
    return {'ok': True, 'result': data.get('result')}


_BOT_USERNAME = None


def bot_username():
    """@username del bot (cacheado vía getMe). Para deep links t.me/<u>?start=…
    que permiten DM al cadete que todavía no inició el chat con el bot."""
    global _BOT_USERNAME
    if _BOT_USERNAME is None:
        r = _get('getMe')
        _BOT_USERNAME = ((r.get('result') or {}).get('username') or '') if r.get('ok') else ''
    return _BOT_USERNAME


def deep_link_pedido(pedido_id):
    """Link que arranca el bot en privado y entrega el detalle del pedido.
    Vacío si no se pudo resolver el username."""
    u = bot_username()
    return f'https://t.me/{u}?start=ped_{pedido_id}' if u else ''


# ── Mensajes al grupo + botón inline TOMAR ──────────────────────────────────

def publicar_pedido(texto, pedido_id, chat_id=None):
    """Publica texto al grupo con un botón inline 'TOMAR' debajo. El callback_data
    del botón es 'tomar:{pedido_id}' para que el webhook sepa qué pedido se tomó.

    Devuelve {ok, telegram_msg_id, error?}.
    """
    chat = chat_id or GRUPO_CHAT_ID
    if not chat:
        return {'ok': False, 'error': 'TELEGRAM_CADETES_GRUPO_CHAT_ID no configurado'}
    keyboard = {
        'inline_keyboard': [[
            {'text': '📦 TOMAR', 'callback_data': f'tomar:{int(pedido_id)}'},
        ]],
    }
    r = _post('sendMessage', json={
        'chat_id': chat,
        'text': texto,
        'parse_mode': 'HTML',
        'reply_markup': keyboard,
        'disable_web_page_preview': True,
    })
    if not r['ok']:
        return r
    msg = r['result']
    return {'ok': True, 'telegram_msg_id': msg['message_id'], 'raw': msg}


def publicar_en_grupo(texto, chat_id=None):
    """Publica un mensaje SIN botón inline. Útil para confirmaciones tipo
    'Pedido #N tomado por @cadete'."""
    chat = chat_id or GRUPO_CHAT_ID
    if not chat:
        return {'ok': False, 'error': 'TELEGRAM_CADETES_GRUPO_CHAT_ID no configurado'}
    r = _post('sendMessage', json={
        'chat_id': chat,
        'text': texto,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True,
    })
    if not r['ok']:
        return r
    return {'ok': True, 'telegram_msg_id': r['result']['message_id'], 'raw': r['result']}


def editar_mensaje_grupo(message_id, nuevo_texto, sacar_kb=True, chat_id=None):
    """Edita un mensaje previo del bot en el grupo. Útil para tachar el botón
    TOMAR cuando alguien lo tomó (sacar_kb=True saca el botón)."""
    chat = chat_id or GRUPO_CHAT_ID
    body = {
        'chat_id': chat,
        'message_id': int(message_id),
        'text': nuevo_texto,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True,
    }
    if not sacar_kb:
        # Para mantener un keyboard distinto, el caller debe pasarlo;
        # por default lo sacamos.
        pass
    r = _post('editMessageText', json=body)
    return r


def sacar_botones_grupo(message_id, chat_id=None):
    """Quita el inline keyboard del mensaje del grupo SIN tocar el texto."""
    return setear_botones_grupo(message_id, [], chat_id=chat_id)


def setear_botones_grupo(message_id, botones, chat_id=None):
    """Reemplaza el inline keyboard del mensaje del grupo SIN tocar el texto.
    botones: lista de filas de InlineKeyboardButton dicts (vacío para sacar).
    Diego 2026-06-21: al TOMAR el botón TOMAR se reemplaza por el botón
    RETIRADO (mismo mensaje, distinto callback). El detalle del pedido queda
    visible en el grupo y solo el que tomó puede apretar RETIRADO."""
    chat = chat_id or GRUPO_CHAT_ID
    return _post('editMessageReplyMarkup', json={
        'chat_id': chat, 'message_id': int(message_id),
        'reply_markup': {'inline_keyboard': botones or []},
    })


def answer_callback(callback_query_id, texto=None, alert=False):
    """Responde el callback al usuario que clickeó el botón. Muestra un toast
    chico (alert=False) o un dialog (alert=True). Sin texto: cierra el spinner.

    Telegram exige responder al callback dentro de ~10s o el cliente del cadete
    muestra 'loading' eterno. Llamar siempre, incluso en error.
    """
    body = {'callback_query_id': callback_query_id}
    if texto:
        body['text'] = texto[:200]
        body['show_alert'] = bool(alert)
    return _post('answerCallbackQuery', json=body)


# ── DM 1:1 al cadete ────────────────────────────────────────────────────────

def enviar_dm(user_id, texto, botones=None):
    """Mensaje directo al cadete. user_id es el telegram_user_id (int).
    botones: lista de filas de InlineKeyboardButton dicts. Ejemplo:
        [[{'text': '✅ Retiré', 'callback_data': 'retiro:123'}]]

    OJO: el bot solo puede DM a usuarios que ANTES iniciaron conversación con él
    (vía /start o similar). Si nunca hablaron, Telegram devuelve 'Forbidden:
    bot can't initiate conversation'. La auto-magic resuelve esto: al primer
    TOMAR el cadete ya interactuó con el bot vía callback, y eso lo habilita
    para recibir DMs.
    """
    if not user_id:
        return {'ok': False, 'error': 'user_id vacío'}
    body = {
        'chat_id': int(user_id),
        'text': texto,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True,
    }
    if botones:
        body['reply_markup'] = {'inline_keyboard': botones}
    r = _post('sendMessage', json=body)
    if not r['ok']:
        return r
    return {'ok': True, 'telegram_msg_id': r['result']['message_id'], 'raw': r['result']}


def editar_botones_dm(user_id, message_id, botones):
    """Cambia el inline keyboard de un DM previo (no toca el texto). Útil
    para evolucionar 'Retiré' → 'Entregado' → sin botones."""
    if not user_id or not message_id:
        return {'ok': False, 'error': 'datos incompletos'}
    return _post('editMessageReplyMarkup', json={
        'chat_id': int(user_id),
        'message_id': int(message_id),
        'reply_markup': {'inline_keyboard': botones or []},
    })


# ── Parseo de updates entrantes ─────────────────────────────────────────────

def parsear_update(payload):
    """Convierte un Update de Telegram a un dict normalizado para reparto.py.

    Tipos devueltos en 'tipo':
      - 'callback_tomar': callback_query del botón TOMAR
          → {tipo, callback_query_id, user_id, user_name, user_username,
              pedido_id, message_id, chat_id}
      - 'mensaje_grupo': mensaje de texto en el grupo (para frase de toma)
          → {tipo, user_id, user_name, user_username, texto, message_id, chat_id}
      - 'mensaje_dm': mensaje en chat 1:1 con el bot (feedback de cadete)
          → {tipo, user_id, user_name, user_username, texto, chat_id}
      - 'unknown': para todo lo demás (status updates de admin, etc.)
    """
    if not isinstance(payload, dict):
        return {'tipo': 'unknown', 'raw': payload}

    cb = payload.get('callback_query')
    if cb:
        data = cb.get('data') or ''
        # Callbacks del workflow del cadete:
        #   tomar:<pid>      → toma el pedido (grupo → estado=tomado)
        #   retirado:<pid>   → retiró de farmacia (grupo → estado=en_ruta + DM)
        #   entregado:<pid>  → entregó al cliente (DM → estado=entregado)
        #   no_atiende:<pid> → fue al domicilio y no atendió nadie (DM → estado=no_atiende)
        #   reintentar:<pid> → volver a intentar entregar (DM → estado=en_ruta)
        _PREFIJOS = {'tomar:': 'callback_tomar',
                     'retirado:': 'callback_retirado',
                     'entregado:': 'callback_entregado',
                     'no_atiende:': 'callback_no_atiende',
                     'reintentar:': 'callback_reintentar'}
        prefijo_match = next((p for p in _PREFIJOS if data.startswith(p)), None)
        if prefijo_match:
            try:
                pid = int(data.split(':', 1)[1])
            except (ValueError, IndexError):
                pid = None
            user = cb.get('from') or {}
            msg = cb.get('message') or {}
            chat = msg.get('chat') or {}
            return {
                'tipo': _PREFIJOS[prefijo_match],
                'callback_query_id': cb.get('id'),
                'user_id': user.get('id'),
                'user_name': _full_name(user),
                'user_username': user.get('username'),
                'pedido_id': pid,
                'message_id': msg.get('message_id'),
                'chat_id': chat.get('id'),
            }
        return {'tipo': 'unknown', 'raw': cb}

    msg = payload.get('message')
    if msg:
        user = msg.get('from') or {}
        chat = msg.get('chat') or {}
        texto = msg.get('text') or ''
        ctype = chat.get('type') or ''
        common = {
            'user_id': user.get('id'),
            'user_name': _full_name(user),
            'user_username': user.get('username'),
            'texto': texto,
            'message_id': msg.get('message_id'),
            'chat_id': chat.get('id'),
        }
        if ctype in ('group', 'supergroup'):
            return {'tipo': 'mensaje_grupo', **common}
        if ctype == 'private':
            return {'tipo': 'mensaje_dm', **common}

    return {'tipo': 'unknown', 'raw': payload}


def _full_name(user):
    """Concat first_name + last_name de un user de Telegram."""
    if not user:
        return ''
    parts = [user.get('first_name') or '', user.get('last_name') or '']
    return ' '.join(p for p in parts if p).strip()


# ── Detección de frase de toma (fallback al botón) ──────────────────────────

FRASES_TOMA = [
    'tomo', 'voy', 'lo tomo', 'yo voy', 'voy yo', 'lo agarro', 'oktomo',
]


def es_frase_de_toma(texto):
    """True si el texto matchea (case+espacios insensitive) una frase aceptada."""
    t = (texto or '').strip().lower()
    t_norm = ' '.join(t.split())
    return t_norm in FRASES_TOMA


# ── Setup del webhook ───────────────────────────────────────────────────────

def setear_webhook(url):
    """Configura el webhook de Telegram apuntando a `url`. Suscribe a los
    eventos relevantes: mensajes y callbacks.

    Si TELEGRAM_CADETES_WEBHOOK_SECRET está seteado, Telegram lo manda en el
    header X-Telegram-Bot-Api-Secret-Token de cada request para validar origen.
    """
    body = {
        'url': url,
        'allowed_updates': ['message', 'callback_query', 'edited_message'],
        'drop_pending_updates': False,
    }
    if WEBHOOK_SECRET:
        body['secret_token'] = WEBHOOK_SECRET
    return _post('setWebhook', json=body)


def estado_bot():
    """Health check: getMe + getWebhookInfo combinados."""
    me = _get('getMe')
    wh = _get('getWebhookInfo')
    return {
        'me': me.get('result') if me.get('ok') else {'error': me.get('error')},
        'webhook': wh.get('result') if wh.get('ok') else {'error': wh.get('error')},
    }


def validar_webhook_secret(header_value):
    """True si el header X-Telegram-Bot-Api-Secret-Token coincide con el
    secret configurado. Si no hay secret configurado devuelve True (sin
    validar). Usado por el endpoint de webhook."""
    if not WEBHOOK_SECRET:
        return True
    return header_value == WEBHOOK_SECRET


# ── Polling mode (uso local, sin URL pública) ───────────────────────────────
#
# Cuando la app corre en local (ej. la planilla de Badia en LAN) Telegram no
# puede entregarnos webhooks porque localhost no es público. Polling resuelve:
# un thread llama getUpdates con long-polling (timeout=25s) → si llega algo
# reenvía el update a nuestro endpoint /telegram/cadetes/webhook por HTTP local
# (con el secret en el header).
#
# Activación: env var TELEGRAM_CADETES_USAR_POLLING=true. Si está vacío o
# false → no arranca (default seguro: Render no se queda corriendo polling
# por error y peleando con local por los updates).

USAR_POLLING = (os.environ.get('TELEGRAM_CADETES_USAR_POLLING') or '').lower() in ('1', 'true', 'yes', 'on')
LOCAL_WEBHOOK_URL = (os.environ.get('TELEGRAM_CADETES_LOCAL_WEBHOOK_URL')
                    or 'http://localhost:5000/telegram/cadetes/webhook').rstrip('/')


# Mantenemos referencia al file del lock para que el GC no lo libere mientras
# el proceso vive (cerrar el file libera el flock).
_polling_lock_keepalive = []


def iniciar_polling_thread():
    """Arranca el thread del polling worker. Llamar una vez en app.py al
    arranque. No-op si USAR_POLLING está apagado, falta el token o si OTRO
    worker de gunicorn ya tiene el lock del polling.

    El lock por flock previene el "Conflict: terminated by other getUpdates"
    cuando gunicorn corre con multiple workers (Telegram solo permite UNA
    sesión de getUpdates por bot)."""
    if not USAR_POLLING:
        log.warning('[telegram polling] no arranca: TELEGRAM_CADETES_USAR_POLLING off')
        return
    if not BOT_TOKEN:
        log.warning('[telegram polling] no arranca: TELEGRAM_CADETES_BOT_TOKEN vacío')
        return

    # Lock por socket bind: solo UN worker de gunicorn debe arrancar el polling.
    # El primero que binda al puerto gana, los demás reciben EADDRINUSE.
    # Más robusto que flock (no hay file descriptors heredados ni archivos
    # zombie en /tmp que sobreviven a restarts).
    # Usamos un puerto random alto y bind a 127.0.0.1 (no expuesto fuera).
    POLLING_LOCK_PORT = 12347
    try:
        import socket as _sk
        s = _sk.socket(_sk.AF_INET, _sk.SOCK_STREAM)
        s.setsockopt(_sk.SOL_SOCKET, _sk.SO_REUSEADDR, 0)  # NO reuse — queremos fallar
        s.bind(('127.0.0.1', POLLING_LOCK_PORT))
        s.listen(1)
        _polling_lock_keepalive.append(s)
    except OSError as e:
        log.warning('[telegram polling] otro worker ya tiene el lock (port %d) — no arranco acá: %s',
                    POLLING_LOCK_PORT, e)
        return

    import threading
    t = threading.Thread(target=_polling_worker, daemon=True, name='telegram_polling')
    t.start()
    log.warning('[telegram polling] worker started (lock adquirido)')


def _polling_worker():
    """Loop infinito: getUpdates con long-polling → reenvía al endpoint local."""
    import time
    # Antes de arrancar: borrar el webhook si quedó configurado (sino getUpdates
    # devuelve 409 Conflict). Idempotente: si no había webhook, no pasa nada.
    try:
        _post('deleteWebhook', json={'drop_pending_updates': False})
    except Exception:  # noqa: BLE001
        pass

    offset = 0
    while True:
        try:
            r = requests.get(
                f'{API_BASE}/getUpdates',
                params={'offset': offset, 'timeout': 25, 'allowed_updates':
                        '["message","callback_query","edited_message"]'},
                timeout=35,
            )
            data = r.json()
            if not data.get('ok'):
                log.warning('telegram getUpdates !ok: %s', data.get('description'))
                time.sleep(5)
                continue
            for upd in data.get('result') or []:
                offset = upd['update_id'] + 1
                _reenviar_al_endpoint_local(upd)
        except requests.Timeout:
            # Esperado con long-polling cuando no hay updates en 25s. Sin log.
            continue
        except Exception as e:  # noqa: BLE001
            log.warning('telegram polling falló: %s — sleep 5s', e)
            time.sleep(5)


def _reenviar_al_endpoint_local(update):
    """POST del update tal cual a nuestro endpoint /telegram/cadetes/webhook.
    Reusa toda la lógica del endpoint sin duplicar código."""
    try:
        headers = {'Content-Type': 'application/json'}
        if WEBHOOK_SECRET:
            headers['X-Telegram-Bot-Api-Secret-Token'] = WEBHOOK_SECRET
        requests.post(LOCAL_WEBHOOK_URL, json=update, headers=headers, timeout=15)
    except Exception as e:  # noqa: BLE001
        log.warning('reenvío al webhook local falló: %s', e)
