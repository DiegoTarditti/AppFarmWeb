"""Rutas del webhook de WhatsApp Cloud API."""
import hashlib
import hmac
import json
import logging
import os

from flask import jsonify, request
from flask_login import login_required

from bot import store

log = logging.getLogger(__name__)

_WABA_VERIFY_TOKEN = os.environ.get('WABA_VERIFY_TOKEN', 'farmweb_wh_secret').strip()
_WABA_APP_SECRET = os.environ.get('WABA_APP_SECRET', '').strip()

_REENGANCHE_MINUTOS = float(os.environ.get('REENGANCHE_MINUTOS', '5'))


def init_app(app):

    @app.route('/whatsapp/webhook', methods=['GET'])
    def whatsapp_webhook_get():
        """Verificación del webhook: Meta llama con hub.mode=subscribe."""
        mode = request.args.get('hub.mode', '')
        token = request.args.get('hub.verify_token', '')
        challenge = request.args.get('hub.challenge', '')
        if mode == 'subscribe' and token == _WABA_VERIFY_TOKEN:
            return challenge, 200, {'Content-Type': 'text/plain'}
        return 'Forbidden', 403

    @app.route('/whatsapp/webhook', methods=['POST'])
    def whatsapp_webhook_post():
        """Webhook entrante: mensajes, status updates, etc."""
        # Verificar firma HMAC-SHA256 si WABA_APP_SECRET está configurado
        if _WABA_APP_SECRET:
            raw_body = request.get_data()
            sig_header = request.headers.get('X-Hub-Signature-256', '')
            expected = hmac.new(
                _WABA_APP_SECRET.encode(), raw_body, hashlib.sha256
            ).hexdigest()
            expected = f'sha256={expected}'
            if not hmac.compare_digest(sig_header, expected):
                log.warning('WhatsApp webhook: firma inválida')
                return 'Forbidden', 403

        try:
            from bot.whatsapp_bot import procesar_evento
            procesar_evento(request.get_json(silent=True) or {})
        except Exception as e:
            log.warning('WhatsApp webhook error: %s', e, exc_info=True)

        return jsonify({}), 200

    @app.route('/whatsapp/reenganche', methods=['GET'])
    def whatsapp_reenganche():
        """Endpoint de re-enganche para conversaciones de WhatsApp."""
        secret = request.args.get('secret', '')
        if not _WABA_VERIFY_TOKEN or secret != _WABA_VERIFY_TOKEN:
            return jsonify({'ok': False, 'error': 'no autorizado'}), 403

        convs = store.conversaciones_para_reenganche(
            minutos=_REENGANCHE_MINUTOS, max_minutos=120)
        enviados = 0
        for c in convs:
            if c.get('canal') != 'whatsapp':
                continue
            from bot import cerebro
            resp = cerebro.preparar_reenganche(c['id'])
            if resp and resp.get('texto'):
                from bot.whatsapp_bot import enviar_texto
                enviar_texto(c['canal_user_id'], resp['texto'])
                enviados += 1
        return jsonify({'ok': True, 'enviados': enviados})