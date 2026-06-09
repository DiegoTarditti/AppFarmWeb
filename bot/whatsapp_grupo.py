"""Envío de mensajes a un grupo de WhatsApp vía WAHA (whatsapp-web.js).
Distinto del adapter Cloud API (que NO soporta grupos). Lo usa la planilla de
reparto para publicar pedidos."""
import logging
import os

import requests

log = logging.getLogger(__name__)

WAHA_URL = (os.environ.get('WAHA_URL') or 'http://waha:3000').rstrip('/')
WAHA_API_KEY = os.environ.get('WAHA_API_KEY') or ''
WAHA_SESSION = os.environ.get('WAHA_SESSION') or 'default'
WAHA_GRUPO_ENVIOS = os.environ.get('WAHA_GRUPO_ENVIOS') or ''


def _headers():
    h = {'Content-Type': 'application/json'}
    if WAHA_API_KEY:
        h['X-Api-Key'] = WAHA_API_KEY
    return h


def publicar_en_grupo(texto, chat_id=None, session=None):
    """Manda texto al grupo configurado. Devuelve dict con:
      - ok: True/False
      - waha_msg_id: id del mensaje en WhatsApp (para matchear replies)
      - error: si falló
    """
    chat = chat_id or WAHA_GRUPO_ENVIOS
    sess = session or WAHA_SESSION
    if not chat:
        return {'ok': False, 'error': 'WAHA_GRUPO_ENVIOS no configurado'}
    try:
        r = requests.post(
            f'{WAHA_URL}/api/sendText',
            headers=_headers(),
            json={'chatId': chat, 'text': texto, 'session': sess},
            timeout=20,
        )
    except Exception as e:  # noqa: BLE001
        log.exception('WAHA sendText falló')
        return {'ok': False, 'error': str(e)}
    if not r.ok:
        return {'ok': False, 'error': f'HTTP {r.status_code}: {r.text[:200]}'}
    data = r.json()
    # WAHA devuelve id como dict {fromMe, remote, id, participant, _serialized}
    # o a veces como string. Tomamos el id "corto" (id.id) que es lo que aparece
    # en replyTo.id de los mensajes citados; el _serialized lo guardamos como fallback.
    id_obj = data.get('id')
    short_id = None
    full_id = None
    if isinstance(id_obj, dict):
        short_id = id_obj.get('id')
        full_id = id_obj.get('_serialized')
    elif isinstance(id_obj, str):
        full_id = id_obj
        # extraer la parte hex larga (el 'short id' suele ser el tercer segmento)
        parts = id_obj.split('_')
        if len(parts) >= 3:
            short_id = parts[2]
    # Guardamos los DOS concatenados con pipe para que el match_partial encuentre
    # cualquiera de los dos formatos en el replyTo.
    msg_id = f'{short_id or ""}|{full_id or ""}'.strip('|')
    return {'ok': True, 'waha_msg_id': msg_id, 'raw': data}


FRASES_TOMA = [
    'tomo', 'voy', 'lo tomo', 'yo voy', 'voy yo', 'lo agarro', 'oktomo',
]


def es_frase_de_toma(texto):
    """True si el texto matchea (case+espacios insensitive) una de las frases
    aceptadas para tomar un pedido."""
    t = (texto or '').strip().lower()
    # normalizar espacios múltiples
    t_norm = ' '.join(t.split())
    return t_norm in FRASES_TOMA


def configurar_webhook(url, session=None):
    """Setea el webhook de la sesión: WAHA va a POST `url` cada vez que entra
    un mensaje (incluye 'message' events). Pisa la config previa."""
    sess = session or WAHA_SESSION
    body = {
        'name': sess,
        'config': {
            'webhooks': [{
                'url': url,
                'events': ['message'],
            }],
        },
    }
    try:
        r = requests.put(f'{WAHA_URL}/api/sessions/{sess}',
                         headers=_headers(), json=body, timeout=15)
        return {'ok': r.ok, 'status': r.status_code, 'body': r.text[:300]}
    except Exception as e:  # noqa: BLE001
        return {'ok': False, 'error': str(e)}


def estado_sesion():
    """Status crudo de la sesión WAHA (para health checks)."""
    try:
        r = requests.get(f'{WAHA_URL}/api/sessions/{WAHA_SESSION}',
                         headers=_headers(), timeout=10)
        return r.json() if r.ok else {'error': f'HTTP {r.status_code}'}
    except Exception as e:  # noqa: BLE001
        return {'error': str(e)}
