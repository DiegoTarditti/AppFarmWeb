"""Panel de operadores del bot (handoff humano).

Cuando el bot deriva una consulta (estado='cola') o el operador toma una
conversación (estado='humano'), el operador la atiende desde acá: ve la
bandeja, abre el chat, lee el historial y le responde al cliente. El mensaje
del operador sale al canal real (Telegram hoy, WhatsApp mañana) vía bot.canales.

Mientras una conversación está en 'humano', el cerebro del bot NO responde
(ver bot/cerebro.procesar) → no pisa al operador.

Rutas:
  GET  /atencion                          → panel (bandeja + chat)
  GET  /atencion/api/conversaciones       → JSON bandeja (polling)
  GET  /atencion/api/<id>/mensajes        → JSON mensajes (polling)
  POST /atencion/<id>/tomar               → operador toma la conversación
  POST /atencion/<id>/responder           → operador envía un mensaje al cliente
  POST /atencion/<id>/cerrar              → libera (vuelve al bot)
"""
from flask import jsonify, render_template, request
from flask_login import current_user, login_required

from bot import canales, store


def init_app(app):

    @app.route('/atencion')
    @login_required
    def atencion_panel():
        return render_template('atencion.html', lineas=store.lineas_distintas())

    @app.route('/atencion/api/conversaciones')
    @login_required
    def atencion_conversaciones():
        linea = request.args.get('linea') or None
        return jsonify({'conversaciones': store.listar_conversaciones(linea)})

    @app.route('/atencion/api/<int:conv_id>/mensajes')
    @login_required
    def atencion_mensajes(conv_id):
        desde = request.args.get('desde', 0, type=int)
        conv = store.get_conversacion_full(conv_id)
        if not conv:
            return jsonify({'error': 'no existe'}), 404
        return jsonify({'conversacion': conv,
                        'mensajes': store.get_mensajes(conv_id, desde)})

    @app.route('/atencion/<int:conv_id>/tomar', methods=['POST'])
    @login_required
    def atencion_tomar(conv_id):
        store.set_atencion(conv_id, 'humano', operador_user_id=current_user.id)
        return jsonify({'ok': True, 'conversacion': store.get_conversacion_full(conv_id)})

    @app.route('/atencion/<int:conv_id>/responder', methods=['POST'])
    @login_required
    def atencion_responder(conv_id):
        texto = (request.json or {}).get('texto', '').strip()
        if not texto:
            return jsonify({'ok': False, 'error': 'mensaje vacío'}), 400
        conv = store.get_conversacion_full(conv_id)
        if not conv:
            return jsonify({'ok': False, 'error': 'no existe'}), 404
        # Si nadie la había tomado, la toma este operador al responder.
        if conv['estado'] != 'humano':
            store.set_atencion(conv_id, 'humano', operador_user_id=current_user.id)
        enviado = canales.enviar(conv['canal'], conv['canal_user_id'], texto)
        store.guardar_mensaje(conv_id, 'operador', texto)
        return jsonify({'ok': True, 'enviado': enviado})

    @app.route('/atencion/<int:conv_id>/cerrar', methods=['POST'])
    @login_required
    def atencion_cerrar(conv_id):
        # Vuelve al bot: la próxima vez que el cliente escriba, lo atiende el bot.
        store.set_atencion(conv_id, 'bot', operador_user_id=None)
        return jsonify({'ok': True})
