"""Envío de mensajes salientes a cada canal (lo usa el panel de operadores para
responderle al cliente). Hoy: Telegram. Mañana se agrega WhatsApp Cloud API.

El token se lee del env (TELEGRAM_BOT_TOKEN) — el mismo que usa el bot.
"""
import os

import requests


def enviar(canal, canal_user_id, texto):
    """Envía un mensaje al cliente por el canal de su conversación.
    Devuelve True/False según pudo enviarlo."""
    if canal == 'telegram':
        return _enviar_telegram(canal_user_id, texto)
    # TODO: WhatsApp Cloud API
    return False


def _enviar_telegram(chat_id, texto):
    token = (os.environ.get('TELEGRAM_BOT_TOKEN') or '').strip()
    if not token:
        return False
    try:
        r = requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
                          data={'chat_id': chat_id, 'text': texto}, timeout=15)
        return r.ok
    except Exception:  # noqa: BLE001
        return False
