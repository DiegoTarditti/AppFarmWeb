"""Adapter WhatsApp Cloud API — parseo de webhook y envío de mensajes."""
import base64
import json
import logging
import os

import requests
from bot import cerebro, store

log = logging.getLogger(__name__)

WABA_TOKEN = os.environ.get('WABA_TOKEN', '').strip()
WABA_PHONE_ID = os.environ.get('WABA_PHONE_ID', '').strip()
API_URL = f'https://graph.facebook.com/v21.0/{WABA_PHONE_ID}/messages' if WABA_PHONE_ID else ''


def procesar_evento(payload_json):
    """Recibe el dict del webhook de Meta y llama a cerebro.procesar() si hay
    un mensaje entrante. Devuelve None si no hay mensaje o el evento no es
    relevante (status update, etc.)."""
    try:
        obj = (payload_json or {}).get('object')
        if obj != 'whatsapp_business_account':
            return None
        for entry in payload_json.get('entry', []):
            for change in entry.get('changes', []):
                value = change.get('value', {})
                messages = value.get('messages')
                if not messages:
                    continue
                msg = messages[0]
                from_number = msg.get('from')
                if not from_number:
                    continue
                msg_type = msg.get('type', '')
                contacts = value.get('contacts', [{}])
                nombre = contacts[0].get('profile', {}).get('name') if contacts else None

                texto = ''
                imagen_b64 = None
                ubicacion = None
                media_type = 'image/jpeg'

                if msg_type == 'text':
                    texto = msg.get('text', {}).get('body', '')
                elif msg_type == 'interactive':
                    inter = msg.get('interactive', {})
                    if inter.get('type') == 'button_reply':
                        texto = inter.get('button_reply', {}).get('title', '')
                    elif inter.get('type') == 'list_reply':
                        texto = inter.get('list_reply', {}).get('title', '')
                elif msg_type == 'location':
                    loc = msg.get('location', {})
                    ubicacion = {'lat': loc.get('latitude'), 'lng': loc.get('longitude')}
                elif msg_type == 'image':
                    media_id = msg.get('image', {}).get('id')
                    if media_id:
                        img_bytes = _descargar_media(media_id)
                        if img_bytes:
                            imagen_b64 = base64.b64encode(img_bytes).decode('utf-8')
                            media_type = msg.get('image', {}).get('mime_type', 'image/jpeg')
                elif msg_type == 'audio':
                    media_id = msg.get('audio', {}).get('id')
                    if media_id:
                        audio_bytes = _descargar_media(media_id)
                        if audio_bytes:
                            texto = _transcribir(audio_bytes) or ''
                else:
                    continue  # tipo no soportado (status, document, sticker, etc.)

                resp = cerebro.procesar(
                    canal='whatsapp', canal_user_id=from_number, texto=texto,
                    imagen_b64=imagen_b64, media_type=media_type,
                    nombre=nombre, linea='WhatsApp', ubicacion=ubicacion)

                if resp and resp.get('texto'):
                    _enviar(from_number, resp)
    except Exception as e:
        log.warning('procesar_evento error: %s', e, exc_info=True)


def _enviar(to, resp):
    """Envía la respuesta al cliente formateando según las opciones disponibles."""
    texto = resp.get('texto', '')
    opciones = resp.get('opciones') or []

    if not opciones:
        _post(to, {'type': 'text', 'text': {'body': texto[:4096]}})
        return

    # Decidir formato: reply buttons (≤3 ops, cada una ≤20 chars) o list message
    _cab = lambda s: s[:20] if len(s) > 20 else s
    _rows = lambda s: s[:24] if len(s) > 24 else s

    if len(opciones) <= 3 and all(len(o) <= 20 for o in opciones):
        buttons = [{'type': 'reply', 'reply': {'id': str(i + 1), 'title': _cab(o)}}
                   for i, o in enumerate(opciones)]
        payload = {
            'type': 'interactive',
            'interactive': {
                'type': 'button',
                'body': {'text': texto[:1024]},
                'action': {'buttons': buttons},
            },
        }
    else:
        rows = [{'id': str(i + 1), 'title': _rows(o)} for i, o in enumerate(opciones)]
        payload = {
            'type': 'interactive',
            'interactive': {
                'type': 'list',
                'body': {'text': texto[:1024]},
                'action': {'button': 'Opciones', 'sections': [
                    {'title': 'Opciones', 'rows': rows},
                ]},
            },
        }
    _post(to, payload)


def _post(to, payload):
    """POST a la API de WhatsApp Cloud."""
    if not WABA_TOKEN or not API_URL:
        log.warning('WhatsApp: no configurado (WABA_TOKEN o WABA_PHONE_ID vacíos)')
        return False
    try:
        r = requests.post(
            API_URL,
            json={'messaging_product': 'whatsapp', 'to': to, **payload},
            headers={'Authorization': f'Bearer {WABA_TOKEN}', 'Content-Type': 'application/json'},
            timeout=15,
        )
        if r.status_code not in (200, 201):
            log.warning('WhatsApp API error %s: %s', r.status_code, r.text[:300])
            return False
        return True
    except requests.RequestException as e:
        log.warning('WhatsApp API request error: %s', e)
        return False


def _descargar_media(media_id):
    """Descarga bytes de un media de WhatsApp vía la API."""
    if not WABA_TOKEN:
        return None
    try:
        # Obtener URL de descarga
        meta_url = f'https://graph.facebook.com/v21.0/{media_id}'
        r = requests.get(meta_url, headers={'Authorization': f'Bearer {WABA_TOKEN}'}, timeout=15)
        r.raise_for_status()
        url = r.json().get('url')
        if not url:
            return None
        # Descargar contenido
        r = requests.get(url, headers={'Authorization': f'Bearer {WABA_TOKEN}'}, timeout=30)
        r.raise_for_status()
        return r.content
    except requests.RequestException as e:
        log.warning('WhatsApp media download error: %s', e)
        return None


def _transcribir(audio_bytes):
    """Transcribe audio usando bot.audio.transcribir()."""
    try:
        from bot.audio import transcribir
        return transcribir(audio_bytes)
    except Exception as e:
        log.warning('WhatsApp audio transcription error: %s', e)
        return None


def enviar_texto(to, texto):
    """Enviar mensaje de texto simple (usado por canales.py y re-enganche)."""
    return _post(to, {'type': 'text', 'text': {'body': texto[:4096]}})