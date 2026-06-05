"""Adaptador de Telegram (Fase 0) — traduce entre la API de Telegram y el
cerebro (agnóstico de canal). Corre con long polling, sin webhook, ideal para
probar en local. Cuando pasemos a WhatsApp, se reemplaza este archivo por un
adaptador de WhatsApp Cloud API; el cerebro NO cambia.

Correr:
    docker-compose exec -e TELEGRAM_BOT_TOKEN='123:ABC...' web python -m bot.telegram_bot
"""
import base64
import json
import os
import time

import requests

import database
from bot import audio, cerebro


def _descargar_bytes(base, token, file_id):
    """Descarga un archivo de Telegram y devuelve (bytes, file_path) crudos."""
    r = requests.get(f'{base}/getFile', params={'file_id': file_id}, timeout=15).json()
    path = (r.get('result') or {}).get('file_path')
    if not path:
        return None, None
    data = requests.get(f'https://api.telegram.org/file/bot{token}/{path}', timeout=60).content
    return data, path


def _descargar_foto(base, token, file_id):
    """Descarga una foto de Telegram y la devuelve como (base64, media_type)."""
    try:
        data, path = _descargar_bytes(base, token, file_id)
        if not data:
            return None, 'image/jpeg'
        media = 'image/png' if path.lower().endswith('.png') else 'image/jpeg'
        return base64.b64encode(data).decode(), media
    except Exception as e:  # noqa: BLE001
        print('descargar foto error:', e)
        return None, 'image/jpeg'


def _transcribir_voz(base, token, file_id):
    """Baja una nota de voz/audio de Telegram y la transcribe a texto (o None)."""
    try:
        data, path = _descargar_bytes(base, token, file_id)
        if not data:
            return None
        return audio.transcribir(data, filename=path.split('/')[-1] if path else 'audio.ogg')
    except Exception as e:  # noqa: BLE001
        print('transcribir voz error:', e)
        return None


def _typing(base, chat_id):
    """Muestra 'escribiendo…' en el chat (indicador nativo, dura ~5s). Lindo y
    barato: avisa que el bot está laburando mientras transcribe/piensa."""
    try:
        requests.post(f'{base}/sendChatAction',
                      data={'chat_id': chat_id, 'action': 'typing'}, timeout=10)
    except Exception:  # noqa: BLE001
        pass


def _aviso(base, chat_id, texto):
    """Mensaje corto de estado (para operaciones que tardan: audio, receta)."""
    try:
        requests.post(f'{base}/sendMessage',
                      data={'chat_id': chat_id, 'text': texto}, timeout=10)
    except Exception:  # noqa: BLE001
        pass


def _enviar(base, chat_id, resp):
    payload = {'chat_id': chat_id, 'text': resp['texto']}
    ops = resp.get('opciones') or []
    if ops:
        # Botones inline (pegados al mensaje, siempre visibles). El callback_data
        # es el label mismo → el cerebro lo matchea igual que si lo escribieran.
        kb = [[{'text': o, 'callback_data': o[:64]}] for o in ops]
        payload['reply_markup'] = json.dumps({'inline_keyboard': kb})
    try:
        requests.post(f'{base}/sendMessage', data=payload, timeout=15)
    except Exception as e:  # noqa: BLE001
        print('sendMessage error:', e)


def main():
    token = (os.environ.get('TELEGRAM_BOT_TOKEN') or '').strip()
    if not token:
        raise SystemExit('Falta TELEGRAM_BOT_TOKEN. Pedíselo a @BotFather y pasalo por env.')
    database.init_engine(os.environ.get('DATABASE_URL', 'sqlite:///farmacia.db'))
    base = f'https://api.telegram.org/bot{token}'

    # Limpia el webhook por si quedó seteado (no se puede polling con webhook activo).
    try:
        requests.get(f'{base}/deleteWebhook', timeout=10)
    except Exception:  # noqa: BLE001
        pass

    me = requests.get(f'{base}/getMe', timeout=10).json()
    print('Bot conectado:', me.get('result', {}).get('username', '?'), '— escuchando…')

    offset = None
    while True:
        params = {'timeout': 30}
        if offset:
            params['offset'] = offset
        try:
            r = requests.get(f'{base}/getUpdates', params=params, timeout=40)
            updates = r.json().get('result', [])
        except Exception as e:  # noqa: BLE001
            print('getUpdates error:', e)
            time.sleep(3)
            continue
        for u in updates:
            offset = u['update_id'] + 1
            imagen_b64, media_type = None, 'image/jpeg'
            if 'callback_query' in u:
                # Tocaron un botón inline.
                cq = u['callback_query']
                chat_id = ((cq.get('message') or {}).get('chat') or {}).get('id')
                texto = cq.get('data', '')
                nombre = (cq.get('from') or {}).get('first_name')
                try:   # saca el "relojito" del botón
                    requests.post(f'{base}/answerCallbackQuery',
                                  data={'callback_query_id': cq['id']}, timeout=10)
                except Exception:  # noqa: BLE001
                    pass
            else:
                msg = u.get('message') or {}
                chat_id = (msg.get('chat') or {}).get('id')
                texto = msg.get('text', '') or msg.get('caption', '')
                nombre = (msg.get('from') or {}).get('first_name')
                if msg.get('photo'):
                    # Foto (receta): tomamos la de mayor resolución.
                    if chat_id:
                        _aviso(base, chat_id, '📸 Leyendo tu receta, dame unos segundos…')
                        _typing(base, chat_id)
                    imagen_b64, media_type = _descargar_foto(
                        base, token, msg['photo'][-1]['file_id'])
                elif msg.get('voice') or msg.get('audio'):
                    # Nota de voz: transcribimos y lo tratamos como texto.
                    a = msg.get('voice') or msg.get('audio')
                    # ¿Ya la atiende un humano? Entonces NO avisamos "Escuchando…"
                    # (el bot no va a contestar), pero igual transcribimos para que
                    # el operador vea el texto en el panel.
                    derivada = bool(chat_id) and cerebro.esta_con_humano('telegram', str(chat_id))
                    if not audio.disponible():
                        if not derivada and chat_id:
                            _enviar(base, chat_id, {'texto': 'Por ahora no puedo escuchar '
                                    'audios 🙉 Escribime tu consulta por texto y te ayudo 🙂',
                                    'opciones': []})
                            continue
                        texto = '[nota de voz]'
                    else:
                        if chat_id and not derivada:
                            _aviso(base, chat_id, '🎙️ Escuchando tu audio…')
                            _typing(base, chat_id)
                        texto = _transcribir_voz(base, token, a['file_id']) or ''
                        if not texto:
                            if not derivada and chat_id:
                                _enviar(base, chat_id, {'texto': 'No pude entender el audio 😕 '
                                        'Probá de nuevo o escribímelo por texto.', 'opciones': []})
                                continue
                            texto = '[audio no transcripto]'
            if not chat_id:
                continue
            # 'escribiendo…' mientras el cerebro piensa (IA, búsqueda, visión).
            _typing(base, chat_id)
            try:
                resp = cerebro.procesar('telegram', str(chat_id), texto,
                                        imagen_b64=imagen_b64, media_type=media_type,
                                        nombre=nombre, linea='Telegram')
            except Exception as e:  # noqa: BLE001
                print('cerebro error:', e)
                resp = {'texto': 'Uy, tuve un problema. Probá de nuevo en un ratito 🙏',
                        'opciones': []}
            # resp None = la conversación la tomó un operador → el bot no responde.
            if resp is not None:
                _enviar(base, chat_id, resp)


if __name__ == '__main__':
    main()
