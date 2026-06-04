"""Transcripción de notas de voz (audio → texto) para el bot.

Claude no recibe audio nativo, así que transcribimos con un STT externo y le
pasamos el texto al cerebro (igual que una foto de receta se convierte en
lectura). Hoy: OpenAI Whisper vía HTTP (sin SDK, solo requests). Excelente en
español rioplatense y barato (~US$0,006/min).

Si no hay OPENAI_API_KEY, `transcribir` devuelve None y el adaptador degrada con
un mensaje pidiendo texto — nada se rompe. Para cambiar de proveedor (Deepgram,
Whisper local, etc.) se reemplaza solo esta función.
"""
import os

import requests


def disponible():
    return bool((os.environ.get('OPENAI_API_KEY') or '').strip())


def transcribir(audio_bytes, filename='audio.ogg'):
    """Devuelve el texto transcripto, o None si no se pudo (sin key / error)."""
    key = (os.environ.get('OPENAI_API_KEY') or '').strip()
    if not key or not audio_bytes:
        return None
    try:
        r = requests.post(
            'https://api.openai.com/v1/audio/transcriptions',
            headers={'Authorization': f'Bearer {key}'},
            files={'file': (filename, audio_bytes)},
            data={'model': 'whisper-1', 'language': 'es'},
            timeout=60,
        )
        if r.ok:
            return (r.json().get('text') or '').strip() or None
        print('whisper error:', r.status_code, r.text[:200])
    except Exception as e:  # noqa: BLE001
        print('transcribir audio error:', e)
    return None
