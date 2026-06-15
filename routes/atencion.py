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

import database
from bot import caja, canales, store


def init_app(app):

    @app.route('/atencion')
    @login_required
    def atencion_panel():
        # Modo manual (walk-in): /atencion?modo=manual&new=1 crea una BotConversacion
        # stub (sin chat WhatsApp/Telegram, solo para registrar el pedido) y abre el
        # panel en modo simplificado (sin bandeja ni columna de chat).
        # Refactor C — etapa 2: reemplaza /pedido/nuevo.
        modo = (request.args.get('modo') or '').strip()
        nueva = request.args.get('new') == '1'
        if modo == 'manual' and nueva:
            import uuid as _uuid
            observer_id = request.args.get('observer_id')
            # Deep-link con observer_id: vincular la conv al cliente automaticamente.
            cliente_id = None
            try:
                observer_id_int = int(observer_id) if observer_id else None
            except (TypeError, ValueError):
                observer_id_int = None
            with database.get_db() as s:
                if observer_id_int:
                    cliente_id = database.get_or_create_cliente(s, observer_id=observer_id_int)
                conv = database.BotConversacion(
                    canal='manual',
                    canal_user_id=f'manual-{_uuid.uuid4().hex[:10]}',
                    nombre_cliente='(walk-in)',
                    estado_atencion='humano',
                    nodo='pedido',
                    operador_user_id=current_user.id,
                    cliente_id=cliente_id,
                )
                s.add(conv); s.commit()
                from flask import redirect
                return redirect(f'/atencion?modo=manual&conv={conv.id}')
        return render_template('atencion.html', lineas=store.lineas_distintas(),
                               modo=modo, conv_inicial=request.args.get('conv'))

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

    @app.route('/atencion/operadores/crear', methods=['POST'])
    @login_required
    def atencion_operador_crear():
        if current_user.rol not in ('admin', 'dev'):
            return jsonify({'ok': False, 'error': 'solo un admin puede dar de alta operadores'}), 403
        from routes.auth_routes import hash_password
        b = request.json or {}
        username = (b.get('username') or '').strip().lower()
        nombre = (b.get('nombre') or '').strip()
        password = b.get('password') or ''
        if not username or len(password) < 6:
            return jsonify({'ok': False, 'error': 'usuario y contraseña (mín. 6) requeridos'}), 400
        with database.get_db() as s:
            if s.query(database.Usuario).filter_by(username=username).first():
                return jsonify({'ok': False, 'error': 'ese usuario ya existe'}), 400
            s.add(database.Usuario(
                username=username, nombre_completo=nombre or username, rol='operador',
                activo=True, password_hash=hash_password(password),
                permisos_json='{}', debe_cambiar_password=False))
            s.commit()
        return jsonify({'ok': True})

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
        # Operador tomó la conv → limpiar alerta "sin tomar"
        try:
            from services.informes_bot import resetear_conv
            with database.get_db() as _s:
                resetear_conv(_s, conv_id)
        except Exception:
            pass
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

    # ── Libreta de domicilios del cliente ────────────────────────────────────

    @app.route('/atencion/api/<int:conv_id>/domicilios')
    @login_required
    def atencion_domicilios(conv_id):
        return jsonify({'domicilios': store.listar_domicilios(conv_id)})

    @app.route('/atencion/<int:conv_id>/domicilio', methods=['POST'])
    @login_required
    def atencion_domicilio_crear(conv_id):
        b = request.json or {}
        if not (b.get('direccion') or '').strip():
            return jsonify({'ok': False, 'error': 'falta dirección'}), 400
        r = store.guardar_domicilio(conv_id, etiqueta=b.get('etiqueta'),
                                    direccion=b.get('direccion'),
                                    localidad=b.get('localidad'), origen='manual')
        return jsonify({'ok': r.get('ok', False),
                        'domicilios': store.listar_domicilios(conv_id)})

    @app.route('/atencion/domicilio/<int:dom_id>/delete', methods=['POST'])
    @login_required
    def atencion_domicilio_eliminar(dom_id):
        return jsonify(store.eliminar_domicilio(dom_id))

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

    # ── Pago acordado durante el chat (Fase A.5) ──────────────────────────────
    # El operador define la forma de pago + le manda el link/alias al cliente
    # MIENTRAS sigue chateando, antes del cierre de transacción. Acá se persiste
    # ese estado para que el modal 'Cerrar TX' lo precargue después.

    _PAGO_FIELDS = ('forma_pago_propuesta', 'total_acordado', 'link_mp', 'paga_con',
                    'dato_pago', 'tarjeta_marca', 'tarjeta_nombre', 'tarjeta_ult4',
                    'envio_costo')

    @app.route('/atencion/<int:conv_id>/pago')
    @login_required
    def atencion_pago_get(conv_id):
        with database.get_db() as s:
            conv = s.get(database.BotConversacion, conv_id)
            if not conv:
                return jsonify({'error': 'no existe'}), 404
            return jsonify({
                'forma_pago_propuesta': conv.forma_pago_propuesta,
                'total_acordado': float(conv.total_acordado) if conv.total_acordado is not None else None,
                'envio_costo': float(conv.envio_costo) if getattr(conv, 'envio_costo', None) is not None else None,
                'link_mp': conv.link_mp,
                'paga_con': float(conv.paga_con) if conv.paga_con is not None else None,
                'dato_pago': conv.dato_pago,
                'tarjeta_marca': conv.tarjeta_marca,
                'tarjeta_nombre': conv.tarjeta_nombre,
                'tarjeta_ult4': conv.tarjeta_ult4,
                'pago_acordado_en': conv.pago_acordado_en.isoformat() if conv.pago_acordado_en else None,
                'pago_confirmado_en': conv.pago_confirmado_en.isoformat() if conv.pago_confirmado_en else None,
            })

    @app.route('/atencion/<int:conv_id>/pago', methods=['POST'])
    @login_required
    def atencion_pago_set(conv_id):
        body = request.json or {}
        with database.get_db() as s:
            conv = s.get(database.BotConversacion, conv_id)
            if not conv:
                return jsonify({'ok': False, 'error': 'no existe'}), 404
            # Aplicar solo los campos que llegaron (no pisar con NULL si no vienen).
            for k in _PAGO_FIELDS:
                if k in body:
                    v = body[k]
                    if k in ('paga_con', 'total_acordado', 'envio_costo') and v not in (None, ''):
                        try: v = float(v)
                        except (TypeError, ValueError): v = None
                    elif v in ('', 'null'):
                        v = None
                    setattr(conv, k, v)
            # Timestamps automáticos.
            ahora = database.now_ar()
            if conv.forma_pago_propuesta and not conv.pago_acordado_en:
                conv.pago_acordado_en = ahora
            if conv.dato_pago and not conv.pago_confirmado_en:
                conv.pago_confirmado_en = ahora
            # Si limpia dato_pago, reseteo pago_confirmado_en (caso edit).
            if 'dato_pago' in body and not body['dato_pago']:
                conv.pago_confirmado_en = None
            s.commit()
            return jsonify({'ok': True,
                            'pago_acordado_en': conv.pago_acordado_en.isoformat() if conv.pago_acordado_en else None,
                            'pago_confirmado_en': conv.pago_confirmado_en.isoformat() if conv.pago_confirmado_en else None})

    @app.route('/atencion/<int:conv_id>/ticket', methods=['POST'])
    @login_required
    def atencion_crear_ticket(conv_id):
        """El operador confirma el pedido → va a la cola de caja."""
        items = (request.json or {}).get('items') or []
        ficha = store.get_ficha_de_conversacion(conv_id)
        return jsonify(caja.crear_ticket(
            conv_id, items, current_user.id,
            cliente_nombre=ficha['nombre'] if ficha else None))

    @app.route('/atencion/<int:conv_id>/cerrar-transaccion', methods=['POST'])
    @login_required
    def atencion_cerrar_transaccion(conv_id):
        """Persiste el pedido completo capturado en el modal 'Cerrar transacción'
        (spec: docs/fase_a_transaccion.md). Crea un PedidoReparto con todos los
        campos de pago/cobertura/destino y lo deja en estado='en_caja' para que
        el cajero lo cobre fiscalmente en ObServer y lo despache."""
        body = request.json or {}

        # Resolver drogueria_id (puede venir como id numérico o nombre).
        drogueria_id = None
        drog = body.get('drogueria')
        if drog not in (None, '', 'null'):
            try:
                drogueria_id = int(drog)
            except (TypeError, ValueError):
                with database.get_db() as s:
                    prov = (s.query(database.Provider)
                            .filter(database.Provider.razon_social.ilike(f'%{drog}%'))
                            .first())
                    if prov:
                        drogueria_id = prov.id

        # Datos del cliente y el domicilio ELEGIDO en el modal (cuando el cliente
        # tiene varias direcciones). Si no vino domicilio_id, cae al más reciente
        # (cliente con una sola dirección).
        ficha = store.get_ficha_de_conversacion(conv_id)
        dom = (store.get_domicilio(body['domicilio_id'])
               if body.get('domicilio_id') else None) or {}
        if not dom:
            doms = store.listar_domicilios(conv_id)
            dom = doms[0] if doms else {}

        # Operador que cierra (para el campo `tomo`).
        oper = (getattr(current_user, 'nombre_completo', None)
                or getattr(current_user, 'username', None) or 'operador')

        # Turno: lo asigna el operador de planilla al organizar /reparto/planilla,
        # no quien toma el pedido. Entra NULL y aparece en la sección 'Sin asignar'.
        with database.get_db() as s:
            conv = s.get(database.BotConversacion, conv_id)
            if not conv:
                return jsonify({'ok': False, 'error': 'conversación no existe'}), 404

            total = float(body.get('total') or 0) or None
            try:
                paga_con = float(body['paga_con']) if body.get('paga_con') not in (None, '') else None
            except (TypeError, ValueError):
                paga_con = None
            try:
                envio = float(body['envio_costo']) if body.get('envio_costo') not in (None, '') else None
            except (TypeError, ValueError):
                envio = None

            # Vuelto recalculado server-side (no se confía del valor del cliente):
            # solo aplica a efectivo. vuelto = pagaCon − total. El total viene
            # del ticket de ObServer que YA incluye el envío (Diego 2026-06-14),
            # por eso NO sumamos envío aca.
            vuelto_str = None
            if body.get('forma_pago') == 'efectivo' and paga_con is not None:
                vuelto_str = str(int(round(paga_con - (total or 0))))

            # Teléfono (se muestra en la planilla): si el pedido vino del chat, en
            # WhatsApp el canal_user_id ES el teléfono; si no, de la ficha del cliente.
            telefono = (ficha.get('telefono') if ficha else None) or \
                (conv.canal_user_id if conv.canal == 'whatsapp' else None)

            p = database.PedidoReparto(
                fecha=database.now_ar().date(),
                cliente_id=conv.cliente_id,
                cliente_nombre=(ficha['nombre'] if ficha else None) or conv.nombre_cliente,
                telefono=telefono,
                direccion=dom.get('direccion'),
                piso=dom.get('piso'),
                depto=dom.get('depto'),
                referencia=dom.get('referencia'),
                lat=dom.get('lat'),
                lng=dom.get('lng'),
                # ── Pago ──
                importe=total,                # total bruto ObServer
                total_paciente=total,         # cobrado al paciente (idem si no hay cobertura)
                forma_pago=body.get('forma_pago') or None,
                paga_con=paga_con,
                vuelto=vuelto_str,
                link_mp=body.get('link_mp') or None,
                # dato_pago_mp comparte campo entre nro op MP/transferencia y cupón
                # de tarjeta (se diferencia por forma_pago). Si vienen ambos en el
                # body (raro), prioriza dato_pago_mp.
                dato_pago_mp=(body.get('dato_pago_mp') or body.get('cupon_tarjeta') or '').strip() or None,
                tarjeta_ult4=body.get('tarjeta_ult4') or None,
                tarjeta_nombre=body.get('tarjeta_nombre') or None,
                tarjeta_marca=body.get('tarjeta_marca') or None,
                # ── Cobertura ──
                obra_social=body.get('obra_social') or None,
                receta_estado=body.get('requiere_receta') or None,
                requiere_firma=bool(body.get('requiere_firma') or False),
                # ── Detalle del pedido (Diego 2026-06-14) ──
                producto=(body.get('producto') or '').strip() or None,
                producto_observer_id=body.get('producto_observer_id') or None,
                nota=(body.get('nota') or '').strip() or None,
                observacion=(body.get('observacion') or '').strip() or None,
                # canal: cómo entró el pedido (atencion/mostrador/teléfono/otros).
                # Lo guardamos en la columna 'canal' del PedidoReparto (reemplaza el
                # 'atencion' hardcoded más abajo).
                # cupon_tarjeta: nro de cupón/comprobante cuando forma=tarjeta.
                # Se guarda en dato_pago_mp (campo unificado, lo diferenciás por forma_pago).
                # ── Stock + destino ──
                stock_status=body.get('stock') or None,
                drogueria_id=drogueria_id,
                destino=body.get('destino') or None,
                envio_costo=envio,
                # ── Workflow ──
                # Si el operador ya cobró (link MP confirmado, transfer con comprobante,
                # tarjeta presencial), saltamos 'en_caja' y vamos directo al estado
                # destino (mismo cálculo que usa /caja/pedido/<id>/cobrar).
                pagado=bool(body.get('pagado') or False),
                # SIEMPRE pasa por caja: aunque el operador ya haya cobrado, el
                # cajero tiene que emitir el ticket fiscal en ObServer y despachar.
                # `pagado` solo registra que la plata ya entró (badge "cobrado" +
                # "ticket fiscal pendiente" en la bandeja de caja).
                estado='en_caja',
                # canal: si vino en el body lo usamos (modo manual permite elegir
                # mostrador/teléfono/otros); default 'atencion' para mantener
                # backward compat con cierres anteriores.
                canal=(body.get('canal') or '').strip() or 'atencion',
                tomo=oper,
                # turno: lo asigna el operador de planilla (entra NULL).
                prioridad=body.get('prioridad') or 'normal',
            )
            s.add(p)
            s.commit()
            return jsonify({'ok': True, 'pedido_id': p.id, 'estado': p.estado})

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
        # El operador respondió → limpiar informe para que si el cliente vuelve
        # a quedar sin atención más tarde, se re-notifique al dueño.
        try:
            from services.informes_bot import resetear_conv
            with database.get_db() as _s:
                resetear_conv(_s, conv_id)
        except Exception:
            pass
        return jsonify({'ok': True, 'enviado': enviado})

    @app.route('/atencion/<int:conv_id>/cerrar', methods=['POST'])
    @login_required
    def atencion_cerrar(conv_id):
        # Vuelve al bot: la próxima vez que el cliente escriba, lo atiende el bot.
        store.set_atencion(conv_id, 'bot', operador_user_id=None)
        try:
            from services.informes_bot import resetear_conv
            with database.get_db() as _s:
                resetear_conv(_s, conv_id)
        except Exception:
            pass
        return jsonify({'ok': True})

    @app.route('/atencion/<int:conv_id>/reset-testing', methods=['POST'])
    @login_required
    def atencion_reset_testing(conv_id):
        return jsonify(store.reset_conversacion_testing(conv_id))

    # ── Respuestas rápidas ───────────────────────────────────────────────

    @app.route('/atencion/api/respuestas-rapidas')
    @login_required
    def atencion_respuestas_rapidas():
        with database.get_db() as s:
            rrs = (s.query(database.RespuestaRapida)
                   .filter(database.RespuestaRapida.activa.is_(True))
                   .order_by(database.RespuestaRapida.orden).all())
            return jsonify({'respuestas': [
                {'id': r.id, 'emoji': r.emoji or '', 'etiqueta': r.etiqueta,
                 'texto': r.texto, 'orden': r.orden or 0} for r in rrs]})

    @app.route('/atencion/respuestas-rapidas')
    @login_required
    def atencion_rr_config():
        if current_user.rol not in ('admin', 'dev'):
            return 'Sin permiso', 403
        with database.get_db() as s:
            rrs = s.query(database.RespuestaRapida).order_by(
                database.RespuestaRapida.orden).all()
            rrs_data = [{'id': r.id, 'emoji': r.emoji or '', 'etiqueta': r.etiqueta,
                         'texto': r.texto, 'orden': r.orden or 0, 'activa': r.activa}
                        for r in rrs]
        return render_template('respuestas_rapidas.html',
                               respuestas=rrs_data)

    @app.route('/atencion/respuestas-rapidas', methods=['POST'])
    @login_required
    def atencion_rr_crear():
        if current_user.rol not in ('admin', 'dev'):
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        b = request.json or {}
        etiqueta = (b.get('etiqueta') or '').strip()
        texto = (b.get('texto') or '').strip()
        if not etiqueta or not texto:
            return jsonify({'ok': False, 'error': 'falta etiqueta o texto'}), 400
        with database.get_db() as s:
            max_ord = s.query(database.RespuestaRapida.orden).order_by(
                database.RespuestaRapida.orden.desc()).first()
            orden = (max_ord[0] or 0) + 1
            rr = database.RespuestaRapida(
                emoji=(b.get('emoji') or '').strip()[:8] or None,
                etiqueta=etiqueta[:40], texto=texto, orden=orden, activa=True)
            s.add(rr)
            s.commit()
            return jsonify({'ok': True, 'id': rr.id})

    @app.route('/atencion/respuestas-rapidas/<int:rid>/edit', methods=['POST'])
    @login_required
    def atencion_rr_edit(rid):
        if current_user.rol not in ('admin', 'dev'):
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        b = request.json or {}
        with database.get_db() as s:
            rr = s.get(database.RespuestaRapida, rid)
            if not rr:
                return jsonify({'ok': False, 'error': 'no existe'}), 404
            if 'emoji' in b: rr.emoji = (b['emoji'] or '').strip()[:8] or None
            if 'etiqueta' in b: rr.etiqueta = (b['etiqueta'] or '').strip()[:40]
            if 'texto' in b: rr.texto = (b['texto'] or '').strip()
            s.commit()
            return jsonify({'ok': True})

    @app.route('/atencion/respuestas-rapidas/<int:rid>/delete', methods=['POST'])
    @login_required
    def atencion_rr_delete(rid):
        if current_user.rol not in ('admin', 'dev'):
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        with database.get_db() as s:
            rr = s.get(database.RespuestaRapida, rid)
            if rr:
                s.delete(rr)
                s.commit()
            return jsonify({'ok': True})

    @app.route('/atencion/respuestas-rapidas/<int:rid>/toggle', methods=['POST'])
    @login_required
    def atencion_rr_toggle(rid):
        if current_user.rol not in ('admin', 'dev'):
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        with database.get_db() as s:
            rr = s.get(database.RespuestaRapida, rid)
            if rr:
                rr.activa = not rr.activa
                s.commit()
            return jsonify({'ok': True, 'activa': rr.activa})

    @app.route('/atencion/respuestas-rapidas/reorder', methods=['POST'])
    @login_required
    def atencion_rr_reorder():
        if current_user.rol not in ('admin', 'dev'):
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        items = (request.json or {}).get('items') or []
        with database.get_db() as s:
            for it in items:
                rr = s.get(database.RespuestaRapida, it.get('id'))
                if rr:
                    rr.orden = it.get('orden', 0)
            s.commit()
            return jsonify({'ok': True})

    # ── Ofrecer oferta al cliente ───────────────────────────────────────

    @app.route('/atencion/<int:conv_id>/ofrecer', methods=['POST'])
    @login_required
    def atencion_ofrecer(conv_id):
        b = request.json or {}
        oferta_bot_id = b.get('oferta_bot_id')
        if not oferta_bot_id:
            return jsonify({'ok': False, 'error': 'falta oferta_bot_id'}), 400
        desc = b.get('descripcion', '') or 'este producto'
        tipo = b.get('tipo', 'descuento_pct') or 'descuento_pct'
        valor = b.get('valor') or 0
        tipo_txt = f'{valor}% de descuento' if tipo == 'descuento_pct' else '2x1'
        texto = f'¡Buenas noticias! 🎉 Tenemos {tipo_txt} en {desc}.\n¿Te interesa aprovecharlo? 🙂'
        conv = store.get_conversacion_full(conv_id)
        if not conv:
            return jsonify({'ok': False, 'error': 'no existe'}), 404
        enviado = canales.enviar(conv['canal'], conv['canal_user_id'], texto)
        store.guardar_mensaje(conv_id, 'operador', texto)
        store.registrar_oferta(conv_id, oferta_bot_id, current_user.id)
        return jsonify({'ok': True, 'enviado': enviado})

    # ── Obra social (inferida + confirmada) ─────────────────────────────

    @app.route('/atencion/api/obras-sociales')
    @login_required
    def atencion_obras_sociales_lista():
        """Lista global de OS con ventas en el último año."""
        q = """
            SELECT DISTINCT oos.observer_id, oos.descripcion
            FROM obs_obras_sociales oos
            JOIN obs_ventas_detalle ovd ON ovd.obra_social_observer = oos.observer_id
            WHERE ovd.fecha_estadistica >= NOW() - INTERVAL '12 months'
            ORDER BY oos.descripcion LIMIT 200
        """
        with database.get_db() as s:
            rows = s.execute(database.text(q)).fetchall()
        return jsonify([{'observer_id': r[0], 'descripcion': r[1]} for r in rows])

    @app.route('/atencion/api/clientes/<int:observer_id>/obra-social', methods=['GET', 'POST'])
    @login_required
    def atencion_os_cliente(observer_id):
        """GET: devuelve la OS (confirmada o inferida) para un cliente.
        POST: confirma/limpia OS. Body: {obra_social_observer_id: 123|null}"""
        from services.os_inferida import clear_os_confirmada, get_os_inferida, set_os_confirmada

        if request.method == 'GET':
            with database.get_db() as s:
                os_info = get_os_inferida(s, observer_id)
            if not os_info:
                return jsonify({'ok': True, 'tiene_os': False})
            return jsonify({'ok': True, 'tiene_os': True, **os_info})

        # POST
        body = request.json or {}
        os_id = body.get('obra_social_observer_id')
        with database.get_db() as s:
            if os_id is None:
                clear_os_confirmada(s, observer_id)
                s.commit()
                os_info = get_os_inferida(s, observer_id)
                return jsonify({'ok': True, 'os': os_info or {'tiene_os': False}})
            # Buscar la descripción en obs_obras_sociales
            row = s.query(database.ObsObraSocial).filter_by(
                observer_id=os_id).first()
            nombre = row.descripcion if row else f'OS #{os_id}'
            usuario = getattr(current_user, 'nombre_completo', None) or current_user.username
            set_os_confirmada(s, observer_id, os_id, nombre, usuario)
            s.commit()
            os_info = get_os_inferida(s, observer_id)
        return jsonify({'ok': True, 'os': os_info})
    @app.route('/atencion/api/precio-os')
    @login_required
    def atencion_precio_os():
        """Precio estimado con cobertura de OS para un producto.
        Query: ?producto_observer=X&obra_social=Y"""
        try:
            producto_observer = int(request.args.get('producto_observer', '0'))
            obra_social = int(request.args.get('obra_social', '0'))
        except ValueError:
            return jsonify({'ok': False, 'error': 'parámetros inválidos'}), 400
        if not producto_observer or not obra_social:
            return jsonify({'ok': False, 'error': 'faltan parámetros'}), 400
        from services.os_inferida import get_precio_os
        with database.get_db() as s:
            info = get_precio_os(s, producto_observer, obra_social)
        if not info:
            return jsonify({'ok': True, 'datos': False})
        return jsonify({'ok': True, 'datos': True, **info})
