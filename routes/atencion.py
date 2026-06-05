"""Panel de operadores del bot (handoff humano).

Cuando el bot deriva una consulta (estado='cola') o el operador toma una
conversación (estado='humano'), el operador la atiende desde acá: ve la
bandeja, abre el chat, lee el historial y le responde al cliente. El mensaje
del operador sale al canal real (Telegram hoy, WhatsApp mañana) vía bot.canales.

Mientras una conversación está en 'humano', el cerebro del bot NO responde
(ver bot/cerebro.procesar) → no pisa al operador.

Distribución: cola compartida (pull). Todo lo derivado cae en una bandeja; el
operador libre toca "Tomar" (anti-colisión: si ya la tomó otro, no la pisa).
Se puede transferir a otro operador o devolver a la cola.

Rutas:
  GET  /atencion                          → panel (bandeja + chat)
  GET  /atencion/api/conversaciones       → JSON bandeja (polling)
  GET  /atencion/api/operadores           → JSON operadores (dropdown transferir)
  GET  /atencion/api/<id>/mensajes        → JSON mensajes (polling)
  POST /atencion/<id>/tomar               → operador toma la conversación (pull)
  POST /atencion/<id>/transferir          → pasa a otro operador (+ nota)
  POST /atencion/<id>/devolver-cola       → libera a la cola
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

    def _nombre_actual():
        return getattr(current_user, 'nombre_completo', None) or current_user.username

    @app.route('/atencion/api/operadores')
    @login_required
    def atencion_operadores():
        return jsonify({'operadores': store.listar_operadores(), 'yo': current_user.id})

    @app.route('/atencion/heartbeat', methods=['POST'])
    @login_required
    def atencion_heartbeat():
        store.heartbeat(current_user.id)
        return jsonify({'ok': True})

    @app.route('/atencion/estado', methods=['POST'])
    @login_required
    def atencion_estado():
        return jsonify(store.set_presencia(current_user.id, (request.json or {}).get('estado', '')))

    @app.route('/atencion/<int:conv_id>/tomar', methods=['POST'])
    @login_required
    def atencion_tomar(conv_id):
        r = store.tomar(conv_id, current_user.id, _nombre_actual())
        if not r['ok'] and r.get('conflicto'):
            return jsonify({'ok': False, 'conflicto': r['conflicto']}), 409
        r['conversacion'] = store.get_conversacion_full(conv_id)
        return jsonify(r)

    @app.route('/atencion/<int:conv_id>/transferir', methods=['POST'])
    @login_required
    def atencion_transferir(conv_id):
        body = request.json or {}
        nuevo_id = body.get('operador_id')
        nota = (body.get('nota') or '').strip()
        ops = {o['id']: o['nombre'] for o in store.listar_operadores()}
        if nuevo_id not in ops:
            return jsonify({'ok': False, 'error': 'operador inválido'}), 400
        r = store.transferir(conv_id, nuevo_id, ops[nuevo_id], _nombre_actual(), nota)
        return jsonify(r)

    @app.route('/atencion/<int:conv_id>/devolver-cola', methods=['POST'])
    @login_required
    def atencion_devolver_cola(conv_id):
        return jsonify(store.devolver_a_cola(conv_id, _nombre_actual()))

    # ── Ficha del cliente ────────────────────────────────────────────────────

    @app.route('/atencion/api/<int:conv_id>/cliente')
    @login_required
    def atencion_cliente(conv_id):
        return jsonify({'ficha': store.get_ficha_de_conversacion(conv_id)})

    @app.route('/atencion/api/clientes/buscar')
    @login_required
    def atencion_clientes_buscar():
        return jsonify({'clientes': store.buscar_clientes(request.args.get('q', ''))})

    @app.route('/atencion/api/productos/buscar')
    @login_required
    def atencion_productos_buscar():
        return jsonify({'productos': store.buscar_productos_detalle(request.args.get('q', ''))})

    @app.route('/atencion/<int:conv_id>/vincular-cliente', methods=['POST'])
    @login_required
    def atencion_vincular_cliente(conv_id):
        oid = (request.json or {}).get('observer_id')
        store.vincular_cliente(conv_id, oid)
        return jsonify({'ok': True, 'ficha': store.get_ficha_de_conversacion(conv_id)})

    @app.route('/atencion/<int:conv_id>/crear-cliente', methods=['POST'])
    @login_required
    def atencion_crear_cliente(conv_id):
        datos = request.json or {}
        if not (datos.get('nombre') or datos.get('apellido')):
            return jsonify({'ok': False, 'error': 'falta nombre/apellido'}), 400
        store.crear_cliente_local(conv_id, datos, creado_por=current_user.id)
        return jsonify({'ok': True, 'ficha': store.get_ficha_de_conversacion(conv_id)})

    # ── Catálogo de ciudades ─────────────────────────────────────────────────

    @app.route('/atencion/api/ciudades')
    @login_required
    def atencion_ciudades():
        return jsonify({'ciudades': store.listar_ciudades()})

    @app.route('/atencion/ciudades', methods=['POST'])
    @login_required
    def atencion_ciudad_crear():
        body = request.json or {}
        return jsonify(store.crear_ciudad(body.get('nombre'), body.get('provincia')))

    @app.route('/atencion/ciudades/<int:ciudad_id>/delete', methods=['POST'])
    @login_required
    def atencion_ciudad_eliminar(ciudad_id):
        return jsonify(store.eliminar_ciudad(ciudad_id))

    @app.route('/atencion/<int:conv_id>/desvincular-cliente', methods=['POST'])
    @login_required
    def atencion_desvincular_cliente(conv_id):
        return jsonify(store.desvincular_cliente(conv_id))

    @app.route('/atencion/<int:conv_id>/ficha-notas', methods=['POST'])
    @login_required
    def atencion_guardar_notas(conv_id):
        return jsonify(store.guardar_notas_conversacion(
            conv_id, (request.json or {}).get('notas', '')))

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
            store.tomar(conv_id, current_user.id, _nombre_actual())
        enviado = canales.enviar(conv['canal'], conv['canal_user_id'], texto)
        store.guardar_mensaje(conv_id, 'operador', texto)
        return jsonify({'ok': True, 'enviado': enviado})

    @app.route('/atencion/<int:conv_id>/cerrar', methods=['POST'])
    @login_required
    def atencion_cerrar(conv_id):
        # Vuelve al bot: la próxima vez que el cliente escriba, lo atiende el bot.
        store.set_atencion(conv_id, 'bot', operador_user_id=None)
        return jsonify({'ok': True})
