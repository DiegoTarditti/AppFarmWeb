"""Transcripción de notas de voz (audio → texto) para el bot.

Claude no recibe audio nativo, así que transcribimos y le pasamos el texto al
cerebro (igual que una foto de receta se convierte en lectura).

Motor por defecto: **Whisper local** (faster-whisper) — sin API key, gratis y
privado (el audio no sale de la LAN). Modelo configurable con `WHISPER_MODEL`
(default 'base'; subir a 'small'/'medium' si se quiere más exactitud a costa de
velocidad). Fallback: OpenAI Whisper API si está `OPENAI_API_KEY` y el modelo
local no carga. Sin ningún motor → devuelve None y el bot pide texto.

El modelo se baja una vez (~150 MB el 'base') a `WHISPER_CACHE` (default
`/app/.whisper_cache`, en el bind mount → persiste entre reinicios).
"""
import io
import os

import requests

_MODEL = None
_MODEL_FALLO = False


def _modelo_local():
    """Carga perezosa del modelo faster-whisper (singleton). None si no se puede."""
    global _MODEL, _MODEL_FALLO
    if _MODEL is not None or _MODEL_FALLO:
        return _MODEL
    try:
        from faster_whisper import WhisperModel
        nombre = os.environ.get('WHISPER_MODEL', 'base')
        cache = os.environ.get('WHISPER_CACHE', '/app/.whisper_cache')
        _MODEL = WhisperModel(nombre, device='cpu', compute_type='int8',
                              download_root=cache)
    except Exception as e:  # noqa: BLE001
        print('whisper local no disponible:', e)
        _MODEL_FALLO = True
    return _MODEL


def disponible():
    """True si hay algún motor de transcripción (local u OpenAI)."""
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return bool((os.environ.get('OPENAI_API_KEY') or '').strip())


def transcribir(audio_bytes, filename='audio.ogg'):
    """Devuelve el texto transcripto, o None si no se pudo."""
    if not audio_bytes:
        return None
    # 1) Whisper local (preferido: sin key, privado).
    modelo = _modelo_local()
    if modelo is not None:
        try:
            segmentos, _info = modelo.transcribe(io.BytesIO(audio_bytes), language='es')
            texto = ''.join(s.text for s in segmentos).strip()
            return texto or None
        except Exception as e:  # noqa: BLE001
            print('transcribir local error:', e)
    # 2) Fallback: OpenAI Whisper API.
    return _transcribir_openai(audio_bytes, filename)


def _transcribir_openai(audio_bytes, filename):
    key = (os.environ.get('OPENAI_API_KEY') or '').strip()
    if not key:
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
        print('whisper api error:', r.status_code, r.text[:200])
    except Exception as e:  # noqa: BLE001
        print('transcribir openai error:', e)
    return None
