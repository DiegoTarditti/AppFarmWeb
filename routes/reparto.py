"""Rutas de reparto (v1): definir rutas (cuadrantes N/S/E/O) + armar el reparto
del día (cargar pedidos, auto-asignar por cuadrante, reasignar a mano, exportar).

Carga manual: el operador agrega cada pedido (cliente + domicilio/dirección + nota).
El motor de asignación vive en services/reparto.py.
"""
import json
import logging
import os
import uuid
from datetime import datetime

from flask import jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

import database
from auth import tiene_perfil
from bot import store
from services import reparto

log = logging.getLogger(__name__)


def _notificar_cliente_pedido(s, pedido, nuevo_estado):
    """Avisa al cliente del pedido (via WAHA DM) cuando entra a en_ruta o
    entregado. No bloquea el commit si falla — loguea y sigue."""
    if nuevo_estado not in ('en_ruta', 'entregado'):
        return None
    if not pedido.cliente_id:
        return None
    cli = s.get(database.Cliente, pedido.cliente_id)
    if not cli:
        return None
    raw = (cli.whatsapp or cli.telefono or '').strip()
    if not raw:
        return None
    from bot.whatsapp_grupo import enviar_dm, normalizar_wa_id
    wa_id = normalizar_wa_id(raw)
    if not wa_id:
        return None
    nombre = (cli.nombre or '').strip().split(' ')[0] or 'Cliente'
    if nuevo_estado == 'en_ruta':
        texto = (f'🛵 Hola {nombre}! Tu pedido ya salió a domicilio.\n'
                 f'📍 {pedido.direccion or "tu dirección"}\n\n'
                 f'En unos minutos pasa el repartidor.')
    else:
        texto = f'✅ Hola {nombre}! Tu pedido fue entregado. ¡Gracias por elegirnos!'
    try:
        r = enviar_dm(wa_id, texto)
        if not r.get('ok'):
            log.warning('notificar pedido #%s al cliente fallo: %s',
                        pedido.id, r.get('error'))
        return r
    except Exception as e:  # noqa: BLE001
        log.exception('notificar pedido #%s al cliente: %s', pedido.id, e)
        return None


def _persistir_mensaje_reparto(*, es_grupo, chat_id, wa_id_emisor, push_name, body, from_me):
    """Persiste un mensaje del grupo de cadetes o un DM de cadete en
    BotConversacion + BotMensaje. Si es de un cadete (DM o grupo) intenta
    auto-vincular Cadete.wa_id por match de push_name.

    Pensado para llamarse desde el webhook WAHA. No re-raise: si falla, loguea
    y devuelve None (la persistencia no debe romper la lógica de toma).
    """
    from bot import whatsapp_grupo
    with database.get_db() as s:
        # Identificar el Cadete por wa_id (si ya lo tenemos vinculado).
        cadete = None
        if wa_id_emisor:
            cadete = (s.query(database.Cadete)
                      .filter(database.Cadete.wa_id == wa_id_emisor)
                      .first())
        # Si no hay vínculo previo pero el push_name apunta inequívoco a un
        # cadete sin wa_id, autocompletar (silencio: 0 o 2+ matches → skip).
        # IMPORTANTE: solo auto-vincular si el wa_id_emisor es un numero real
        # (@c.us). WhatsApp 2026+ usa LID (@lid) en grupos para anonimizar al
        # participante — guardar el LID como wa_id no sirve para mandar DMs
        # despues. Para grupos, ignorar el participant LID; el wa_id real lo
        # carga el operador a mano desde el modal (telefono → wa_id @c.us).
        if (not cadete and wa_id_emisor and push_name
                and wa_id_emisor.endswith('@c.us')):
            nombre_norm = ' '.join(push_name.lower().split())
            from sqlalchemy import func as _func
            candidatos = (s.query(database.Cadete)
                          .filter(_func.lower(database.Cadete.nombre).like(f'%{nombre_norm}%'),
                                  database.Cadete.wa_id.is_(None))
                          .limit(2).all())
            if len(candidatos) == 1:
                cadete = candidatos[0]
                cadete.wa_id = wa_id_emisor
        # Get-or-create la conversación.
        if es_grupo:
            canal = 'whatsapp_grupo'
            canal_user_id = whatsapp_grupo.WAHA_GRUPO_ENVIOS or (chat_id or 'grupo')
            conv = (s.query(database.BotConversacion)
                    .filter_by(canal=canal, canal_user_id=canal_user_id).first())
            if not conv:
                conv = database.BotConversacion(
                    canal=canal, canal_user_id=canal_user_id,
                    nombre_cliente='Grupo de cadetes',
                    estado_atencion='humano', nodo='reparto')
                s.add(conv); s.flush()
        else:
            # DM con un cadete particular. PRIVACIDAD: solo persistimos DMs si
            # el remitente es un cadete reconocido. WAHA recibe webhooks de TODOS
            # los DMs al numero vinculado (incluidos chats personales del operador
            # con familiares, amigos, etc.) — sin este guard quedarian todos en DB.
            if not wa_id_emisor or not cadete:
                return None
            conv = (s.query(database.BotConversacion)
                    .filter_by(canal='whatsapp', canal_user_id=wa_id_emisor).first())
            if not conv:
                conv = database.BotConversacion(
                    canal='whatsapp', canal_user_id=wa_id_emisor,
                    nombre_cliente=cadete.nombre,
                    estado_atencion='humano', nodo='reparto',
                    cadete_id=cadete.id)
                s.add(conv); s.flush()
            elif not conv.cadete_id:
                conv.cadete_id = cadete.id
        # Insertar el mensaje. fromMe ⇒ lo mandó el operador; else lo mandó un cadete.
        # En el grupo, fromMe también puede ser el bot publicando un pedido — lo
        # marcamos como 'operador' igualmente porque sale desde la farmacia.
        origen = 'operador' if from_me else 'cliente'
        s.add(database.BotMensaje(conversacion_id=conv.id, origen=origen, texto=body))
        conv.ultimo_en = database.now_ar()
        s.commit()
        return conv.id

def _persistir_dm_telegram(telegram_user_id, push_name, body):
    """Persiste un DM recibido por Telegram al bot del grupo de cadetes.
    Si el remitente está vinculado a un Cadete, la conv queda con cadete_id.
    Si no, queda como conv 'huérfana' (cadete_id=NULL) para que el operador
    pueda verla en el panel y decidir si vincularla o ignorarla.
    Devuelve conv.id.
    """
    with database.get_db() as s:
        cad = (s.query(database.Cadete)
               .filter(database.Cadete.telegram_user_id == telegram_user_id)
               .first())
        # Fallback: si no hay match por tg_user_id, probamos por nombre. Si
        # encontramos un cadete sin tg_user_id cuyo nombre contiene al
        # push_name (mismo criterio que el TOMAR), aprovechamos el DM para
        # autovincular. Así no hace falta tomar un pedido para empezar a
        # chatear.
        if not cad and push_name:
            from sqlalchemy import func as _func
            nombre_norm = ' '.join(push_name.lower().split())
            if nombre_norm:
                cad = (s.query(database.Cadete)
                       .filter(_func.lower(database.Cadete.nombre)
                               .like(f'%{nombre_norm}%'),
                               database.Cadete.telegram_user_id.is_(None))
                       .first())
                if cad:
                    cad.telegram_user_id = telegram_user_id
        # Get-or-create conv. Identificamos por canal_user_id (= tg user_id)
        # para que mensajes consecutivos del mismo user vayan al mismo lugar
        # esté o no vinculado a Cadete.
        canal_user_id = str(telegram_user_id)
        conv = None
        if cad:
            conv = (s.query(database.BotConversacion)
                    .filter(database.BotConversacion.cadete_id == cad.id)
                    .order_by(database.BotConversacion.ultimo_en.desc())
                    .first())
        if not conv:
            conv = (s.query(database.BotConversacion)
                    .filter_by(canal='telegram_cadete', canal_user_id=canal_user_id)
                    .first())
        if not conv:
            conv = database.BotConversacion(
                canal='telegram_cadete', canal_user_id=canal_user_id,
                nombre_cliente=(cad.nombre if cad else push_name) or 'Sin vincular',
                estado_atencion='humano', nodo='reparto',
                cadete_id=cad.id if cad else None)
            s.add(conv); s.flush()
        elif cad and not conv.cadete_id:
            # Conv huérfana que recién se autovinculó: pegarla al cadete.
            conv.cadete_id = cad.id
            conv.nombre_cliente = cad.nombre or conv.nombre_cliente
        if not cad:
            log.warning(
                '[telegram DM] persistido como huérfano: user_id=%s name=%r. '
                'Vinculá ese tg_user_id a un Cadete en /cadetes para responderle.',
                telegram_user_id, push_name)
        s.add(database.BotMensaje(conversacion_id=conv.id, origen='cliente', texto=body))
        conv.ultimo_en = database.now_ar()
        s.commit()
        log.info('[telegram DM] guardado conv_id=%s cadete_id=%s body=%r',
                    conv.id, conv.cadete_id, body[:60])
        return conv.id


_ROLES_OK = ('admin', 'dev', 'farmacia')
# Perfiles que tocan rutas de /reparto/* (incluye las APIs internas que usa
# /pedido/nuevo y la planilla del día).
_PERFILES_OK = ('pedido_manual', 'planilla_envios')


def _ok():
    # Roles legacy entran directo; operadores entran si tienen alguno de los perfiles.
    if getattr(current_user, 'rol', None) in _ROLES_OK:
        return True
    return any(tiene_perfil(current_user, p) for p in _PERFILES_OK)


def _armado_enabled():
    """Vista de armado por ruta + optimización + mapa. Se conserva para otros
    clientes pero se apaga por farmacia con REPARTO_OPTIMIZACION (en Badia no se
    usa la optimización; el default es OFF). El control por cadete es la vista
    principal de /reparto."""
    return os.environ.get('REPARTO_OPTIMIZACION', '0').strip().lower() in ('1', 'true', 'yes', 'on')


def _fecha(arg):
    try:
        return datetime.strptime((arg or '')[:10], '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return database.now_ar().date()


def _ruta_dict(r, cadetes=None):
    poly = []
    if r.poligono:
        try:
            poly = json.loads(r.poligono)
        except (ValueError, TypeError):
            poly = []
    nombre_cad = ''
    if r.cadete_id and cadetes is not None:
        nombre_cad = cadetes.get(r.cadete_id, '')
    return {'id': r.id, 'nombre': r.nombre, 'cuadrante': r.cuadrante,
            'color': r.color or '#1D9E75', 'cadete': nombre_cad or (r.cadete or ''),
            'cadete_id': r.cadete_id, 'activa': r.activa,
            'poligono': poly, 'n_puntos': len(poly)}


def _cadete_dict(c):
    return {'id': c.id, 'nombre': c.nombre, 'telefono': c.telefono or '',
            'tarifa_dia': float(c.tarifa_dia) if c.tarifa_dia is not None else None,
            'activo': c.activo, 'token': c.token or '',
            'wa_id': c.wa_id or '',
            'telegram_user_id': c.telegram_user_id}


def _mapa_cadetes(s):
    """{id: nombre} de todos los cadetes (para resolver el nombre en las rutas)."""
    return {c.id: c.nombre for c in s.query(database.Cadete).all()}


def _pedido_dict(p, cadetes=None, rutas_cadete=None):
    """Serializa un PedidoReparto. Resuelve el nombre del cadete (override) y el
    cadete EFECTIVO (override por `cadete_id` o, si no tiene, el de su ruta).
    `cadetes`: {id: nombre}. `rutas_cadete`: {ruta_id: cadete_id}."""
    nombre_cad = ''
    if p.cadete_id and cadetes is not None:
        nombre_cad = cadetes.get(p.cadete_id, '')
    efectivo_id = reparto.cadete_efectivo_id(p, rutas_cadete or {})
    efectivo_nombre = cadetes.get(efectivo_id, '') if (efectivo_id and cadetes) else ''
    return {
        'id': p.id, 'cliente_nombre': p.cliente_nombre or 's/cliente',
        'telefono': p.telefono or '',
        'direccion': p.direccion or '', 'nota': p.nota or '',
        'cuadrante': p.cuadrante, 'ruta_id': p.ruta_id, 'estado': p.estado,
        'prioridad': p.prioridad or 'normal',
        'orden': p.orden_en_ruta or 0, 'lat': p.lat, 'lng': p.lng,
        # Campos nuevos
        'tomo': p.tomo or '',
        'canal': p.canal or 'manual',
        'importe': float(p.importe) if p.importe is not None else None,
        'forma_pago': p.forma_pago or '',
        'vuelto': p.vuelto or '',
        'requiere_receta': bool(p.requiere_receta),
        'pagado': bool(p.pagado),
        'turno': p.turno or '',
        'cadete_id': p.cadete_id,
        'cadete_nombre': nombre_cad,
        'cadete_efectivo_id': efectivo_id,
        'cadete_efectivo_nombre': efectivo_nombre,
        'entregado_por': p.entregado_por or '',
        'recibio': p.recibio or '',
        'observacion': p.observacion or '',
        'producto': p.producto or '',
        'piso': p.piso or '',
        'depto': p.depto or '',
        'referencia': p.referencia or '',
        # Control por cadete
        'envio_costo': float(p.envio_costo) if p.envio_costo is not None else None,
        'envio_sin_cargo': bool(p.envio_sin_cargo),
        'total_paciente': float(p.total_paciente) if p.total_paciente is not None else None,
        'receta_estado': p.receta_estado or '',
        'envio_liquidado': bool(p.envio_liquidado),
    }


def init_app(app):

    # ── Definir rutas ────────────────────────────────────────────────────────

    @app.route('/rutas')
    @login_required
    def rutas_panel():
        if not _ok():
            return 'Sin permiso', 403
        return render_template('rutas.html')

    @app.route('/rutas/api')
    @login_required
    def rutas_api():
        if not _ok():
            return jsonify({'error': 'sin permiso'}), 403
        reparto.seed_rutas_si_vacio()
        with database.get_db() as s:
            cad = _mapa_cadetes(s)
            rs = (s.query(database.RutaReparto)
                  .order_by(database.RutaReparto.orden, database.RutaReparto.id).all())
            cs = (s.query(database.Cadete)
                  .order_by(database.Cadete.activo.desc(), database.Cadete.nombre).all())
            return jsonify({'rutas': [_ruta_dict(r, cad) for r in rs],
                            'cadetes': [_cadete_dict(c) for c in cs]})

    @app.route('/rutas', methods=['POST'])
    @login_required
    def rutas_guardar():
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        b = request.json or {}
        with database.get_db() as s:
            if b.get('id'):
                r = s.get(database.RutaReparto, b['id'])
                if not r:
                    return jsonify({'ok': False, 'error': 'no existe'}), 404
            else:
                r = database.RutaReparto(cuadrante=(b.get('cuadrante') or None))
                s.add(r)
            r.nombre = (b.get('nombre') or '').strip() or 'Ruta'
            if 'cuadrante' in b:
                r.cuadrante = (b.get('cuadrante') or None)
            r.color = b.get('color') or '#1D9E75'
            if 'cadete' in b:
                r.cadete = (b.get('cadete') or '').strip() or None
            if 'cadete_id' in b:
                r.cadete_id = b.get('cadete_id') or None
            if 'activa' in b:
                r.activa = bool(b['activa'])
            if 'poligono_texto' in b:   # zona pegada de Google Maps (esquinas)
                parsed = reparto.parse_poligono(b.get('poligono_texto'))
                r.poligono = json.dumps(parsed) if parsed else None
            s.commit()
            return jsonify({'ok': True, 'id': r.id})

    @app.route('/rutas/cargar-distritos', methods=['POST'])
    @login_required
    def rutas_cargar_distritos():
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        return jsonify(reparto.seed_distritos_oficiales())

    @app.route('/rutas/<int:rid>/delete', methods=['POST'])
    @login_required
    def rutas_eliminar(rid):
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        with database.get_db() as s:
            r = s.get(database.RutaReparto, rid)
            if r:
                s.delete(r)
                s.commit()
        return jsonify({'ok': True})

    # ── Cadetes (repartidores) ───────────────────────────────────────────────

    @app.route('/cadetes')
    @login_required
    def cadetes_panel():
        if not _ok():
            return 'Sin permiso', 403
        return render_template('cadetes.html')

    @app.route('/cadetes/api')
    @login_required
    def cadetes_api():
        if not _ok():
            return jsonify({'error': 'sin permiso'}), 403
        with database.get_db() as s:
            cs = (s.query(database.Cadete)
                  .order_by(database.Cadete.activo.desc(), database.Cadete.nombre).all())
            # cuántas zonas (rutas) tiene asignada cada cadete
            zonas = {}
            for r in s.query(database.RutaReparto).filter(
                    database.RutaReparto.cadete_id.isnot(None)).all():
                zonas[r.cadete_id] = zonas.get(r.cadete_id, 0) + 1
            out = []
            for c in cs:
                d = _cadete_dict(c)
                d['zonas'] = zonas.get(c.id, 0)
                out.append(d)
            return jsonify({'cadetes': out})

    @app.route('/cadetes', methods=['POST'])
    @login_required
    def cadetes_guardar():
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        b = request.json or {}
        nombre = (b.get('nombre') or '').strip()
        with database.get_db() as s:
            if b.get('id'):
                c = s.get(database.Cadete, b['id'])
                if not c:
                    return jsonify({'ok': False, 'error': 'no existe'}), 404
            else:
                if not nombre:
                    return jsonify({'ok': False, 'error': 'falta nombre'}), 400
                c = database.Cadete(nombre=nombre, token=uuid.uuid4().hex[:12])
                s.add(c)
            if nombre:
                c.nombre = nombre
            if 'telefono' in b:
                c.telefono = (b.get('telefono') or '').strip() or None
                # Auto-derivar wa_id desde el telefono si todavia no lo tenia.
                # Permite mandar DMs desde el panel sin esperar a que el cadete
                # escriba primero. Si el telefono se borra, el wa_id queda como
                # estaba (puede haber sido vinculado por webhook).
                if c.telefono and not c.wa_id:
                    from bot.whatsapp_grupo import normalizar_wa_id
                    c.wa_id = normalizar_wa_id(c.telefono)
            if 'wa_id' in b:
                c.wa_id = (b.get('wa_id') or '').strip() or None
            if 'telegram_user_id' in b:
                raw_tg = b.get('telegram_user_id')
                if raw_tg in (None, '', '—'):
                    c.telegram_user_id = None
                else:
                    try:
                        c.telegram_user_id = int(str(raw_tg).strip())
                    except (TypeError, ValueError):
                        return jsonify({'ok': False, 'error': 'telegram_user_id debe ser número'}), 400
            if 'tarifa_dia' in b:
                try:
                    c.tarifa_dia = float(b['tarifa_dia']) if b.get('tarifa_dia') not in (None, '') else None
                except (TypeError, ValueError):
                    pass
            if 'activo' in b:
                c.activo = bool(b['activo'])
            # Asignar token a cadetes existentes que no tengan (on-demand)
            if not c.token:
                c.token = uuid.uuid4().hex[:12]
            s.commit()
            return jsonify({'ok': True, 'id': c.id, 'token': c.token})

    @app.route('/cadetes/<int:cid>/delete', methods=['POST'])
    @login_required
    def cadetes_eliminar(cid):
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        with database.get_db() as s:
            c = s.get(database.Cadete, cid)
            if c:
                # desvincular de sus rutas (no las borramos)
                for r in s.query(database.RutaReparto).filter(
                        database.RutaReparto.cadete_id == cid).all():
                    r.cadete_id = None
                s.delete(c)
                s.commit()
        return jsonify({'ok': True})

    # ── Armar reparto del día ────────────────────────────────────────────────

    @app.route('/reparto')
    @login_required
    def reparto_panel():
        if not _ok():
            return 'Sin permiso', 403
        # Vista principal: control de envíos por cadete (header + detalle).
        return render_template('reparto_control.html', armado_enabled=_armado_enabled())

    @app.route('/reparto/armado')
    @login_required
    def reparto_armado():
        """Vista de armado por ruta + mapa + optimización. Solo si la farmacia la
        tiene habilitada (REPARTO_OPTIMIZACION); si no, redirige al control."""
        if not _ok():
            return 'Sin permiso', 403
        if not _armado_enabled():
            return redirect(url_for('reparto_panel'))
        return render_template('reparto.html')

    # /pedido/nuevo se movió a routes/pedidos.py

    @app.route('/reparto/api')
    @login_required
    def reparto_api():
        if not _ok():
            return jsonify({'error': 'sin permiso'}), 403
        reparto.seed_rutas_si_vacio()
        fecha = _fecha(request.args.get('fecha'))
        P = database.PedidoReparto
        with database.get_db() as s:
            cad = _mapa_cadetes(s)
            rs = (s.query(database.RutaReparto)
                  .order_by(database.RutaReparto.orden, database.RutaReparto.id).all())
            rutas_cad = {r.id: r.cadete_id for r in rs if r.cadete_id}
            ps = (s.query(P).filter(P.fecha == fecha, P.estado != 'anulado')
                  .order_by(P.orden_en_ruta, P.id).all())
            cfg = reparto.envio.get_config()
            cs = (s.query(database.Cadete)
                  .filter(database.Cadete.activo.is_(True))
                  .order_by(database.Cadete.nombre).all())
            usuarios = (s.query(database.Usuario)
                        .filter(database.Usuario.activo.is_(True))
                        .order_by(database.Usuario.nombre_completo).all())
            usuarios_list = [{'id': u.id, 'nombre': u.nombre_completo or u.username}
                             for u in usuarios]
            return jsonify({'fecha': fecha.strftime('%Y-%m-%d'),
                            'farmacia': {'lat': cfg['farmacia_lat'], 'lng': cfg['farmacia_lng']},
                            'ciudades': reparto.envio.listar_ciudades(),
                            'rutas': [_ruta_dict(r, cad) for r in rs],
                            'pedidos': [_pedido_dict(p, cad, rutas_cad) for p in ps],
                            'cadetes': [_cadete_dict(c) for c in cs],
                            'usuarios': usuarios_list})

    # APIs de cliente viven en routes/clientes.py (/api/clientes/*).
    # Redirects 308 retirados el 2026-06-10 — ya no hay callers vivos.

    @app.route('/api/pedido/obs-presets')
    @login_required
    def api_pedido_obs_presets():
        """Lista los presets de observación (activos) para alimentar el datalist."""
        if not _ok():
            return jsonify({'error': 'sin permiso'}), 403
        with database.get_db() as s:
            rows = (s.query(database.PedidoObsPreset)
                    .filter(database.PedidoObsPreset.activo.is_(True))
                    .order_by(database.PedidoObsPreset.orden,
                              database.PedidoObsPreset.id).all())
            return jsonify({'presets': [{'id': r.id, 'texto': r.texto} for r in rows]})

    @app.route('/api/pedido/obs-presets', methods=['POST'])
    @login_required
    def api_pedido_obs_presets_crear():
        """Agrega un preset nuevo (o reactiva si existía desactivado).
        Body: {texto: 'PAMI - Traer ...'}. Idempotente por texto."""
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        texto = ((request.json or {}).get('texto') or '').strip()
        if not texto:
            return jsonify({'ok': False, 'error': 'texto vacío'}), 400
        if len(texto) > 160:
            return jsonify({'ok': False, 'error': 'texto muy largo (máx 160)'}), 400
        with database.get_db() as s:
            P = database.PedidoObsPreset
            ya = s.query(P).filter(P.texto == texto).first()
            if ya:
                ya.activo = True
                s.commit()
                return jsonify({'ok': True, 'id': ya.id, 'reusado': True})
            nuevo = P(texto=texto, orden=999)
            s.add(nuevo)
            s.commit()
            return jsonify({'ok': True, 'id': nuevo.id, 'reusado': False})

    @app.route('/reparto/pedido', methods=['POST'])
    @login_required
    def reparto_crear_pedido():
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        b = request.json or {}
        direccion = (b.get('direccion') or '').strip()
        domicilio_id = b.get('domicilio_id')
        if not direccion and domicilio_id:
            d = store.get_domicilio(domicilio_id)
            direccion = (d or {}).get('direccion') or 'ubicación 📍'
        if not (b.get('cliente_nombre') or direccion):
            return jsonify({'ok': False, 'error': 'falta cliente o dirección'}), 400
        coords = reparto.coords_de_pedido(domicilio_id, direccion, b.get('localidad'))
        lat, lng = coords if coords else (None, None)
        cuad = reparto.cuadrante_de(lat, lng)
        # Parsear importe string → float
        importe = None
        raw_importe = b.get('importe')
        if raw_importe is not None and raw_importe != '':
            try:
                importe = float(str(raw_importe).replace(',', '.'))
            except (TypeError, ValueError):
                pass
        envio_costo = None
        raw_envio = b.get('envio')
        if raw_envio is not None and raw_envio != '':
            try:
                envio_costo = float(str(raw_envio).replace(',', '.'))
            except (TypeError, ValueError):
                pass
        with database.get_db() as s:
            ruta = reparto.ruta_para_punto(s, lat, lng)   # zona (polígono) → sino cuadrante
            _cid = b.get('cliente_id')
            _oid = b.get('observer_id')
            # Si viene cliente_id directo, usarlo; si solo observer_id, resolver
            if not _cid and _oid:
                _cid = database.get_or_create_cliente(s, observer_id=_oid)
            p = database.PedidoReparto(
                fecha=database.now_ar().date(),
                cliente_id=_cid,
                cliente_nombre=(b.get('cliente_nombre') or '').strip() or None,
                direccion=direccion or None, lat=lat, lng=lng,
                nota=(b.get('nota') or '').strip() or None,
                cuadrante=cuad, ruta_id=(ruta.id if ruta else None),
                prioridad=(b.get('prioridad') if b.get('prioridad') in
                           ('urgente', 'normal', 'programado') else 'normal'),
                estado='pendiente',
                # Campos nuevos
                tomo=((b.get('tomo') or '').strip()
                      or (current_user.nombre_completo if hasattr(current_user, 'nombre_completo') and current_user.nombre_completo
                          else getattr(current_user, 'username', None))
                      or None),
                canal=(b.get('canal') or 'manual').strip(),
                importe=importe,
                forma_pago=(b.get('forma_pago') or '').strip() or None,
                vuelto=(b.get('vuelto') or '').strip() or None,
                # Efectivo: con cuánto paga (sirve para conciliar y para calcular el
                # vuelto en la planilla/ticket del cadete).
                paga_con=(float(b['paga_con']) if (b.get('paga_con') not in (None, '')) else None),
                # Nro de comprobante MP/transferencia, o cupón de tarjeta. Si está
                # cargado, el frontend ya seteó pagado=true (verificación manual).
                dato_pago_mp=(b.get('dato_pago_mp') or '').strip() or None,
                # Marca de la tarjeta (Visa/Master/...) si forma=debito/credito.
                tarjeta_marca=(b.get('tarjeta_marca') or '').strip() or None,
                requiere_receta=bool(b.get('requiere_receta')),
                pagado=bool(b.get('pagado')),
                turno=(b.get('turno') or '').strip() or None,
                cadete_id=b.get('cadete_id') or None,
                entregado_por=(b.get('entregado_por') or '').strip() or None,
                recibio=(b.get('recibio') or '').strip() or None,
                observacion=(b.get('observacion') or '').strip() or None,
                producto=(b.get('producto') or '').strip() or None,
                producto_observer_id=b.get('producto_observer_id') or None,
                envio_costo=envio_costo,
                # Domicilio estructurado (piso/depto/referencia separados de direccion)
                piso=(b.get('piso') or '').strip() or None,
                depto=(b.get('depto') or '').strip() or None,
                referencia=(b.get('referencia') or '').strip() or None,
            )
            s.add(p)
            s.flush()
            # Auto-persistir el domicilio: si hay cliente_id + direccion + lat/lng
            # y NO se eligió un domicilio_id existente, crearlo para que aparezca
            # en el dropdown la próxima vez. Evita duplicados por dir+loc+piso+depto.
            if _cid and direccion and (lat is not None and lng is not None) and not domicilio_id:
                D = database.DomicilioCliente
                _piso = (b.get('piso') or '').strip() or None
                _depto = (b.get('depto') or '').strip() or None
                _ref = (b.get('referencia') or '').strip() or None
                ya = (s.query(D)
                      .filter(D.cliente_id == _cid,
                              D.direccion == direccion,
                              D.localidad == (b.get('localidad') or None),
                              D.piso == _piso, D.depto == _depto)
                      .first())
                if not ya:
                    s.add(D(cliente_id=_cid, etiqueta='Casa',
                            direccion=direccion,
                            localidad=(b.get('localidad') or None),
                            piso=_piso, depto=_depto, referencia=_ref,
                            lat=lat, lng=lng, origen='direccion',
                            geo_actualizado_en=database.now_ar()))
            s.commit()
            return jsonify({'ok': True, 'id': p.id, 'cuadrante': cuad,
                            'asignado': bool(ruta)})

    @app.route('/whatsapp/grupo/webhook', methods=['POST'])
    def reparto_whatsapp_grupo_webhook():
        """Recibe eventos de WAHA (grupo + DMs de cadetes).

        Flujo:
          1) Persiste TODO mensaje entrante (no solo "Tomo") en BotConversacion +
             BotMensaje para que el panel de /reparto/planilla pueda mostrarlo.
          2) Auto-vincula Cadete.wa_id si el push_name matchea inequivocamente.
          3) Si es frase de toma con reply citando el msg del pedido → asigna.

        Sin login (WAHA hace POST). Si WAHA_WEBHOOK_SECRET está seteado en el
        server, se exige ese valor en el header X-Webhook-Secret o ?secret= —
        así no cualquiera con la URL puede inyectar "tomas" de pedidos."""
        secret = os.environ.get('WAHA_WEBHOOK_SECRET', '').strip()
        if secret and (request.headers.get('X-Webhook-Secret')
                       or request.args.get('secret', '')) != secret:
            return jsonify({'ok': False, 'error': 'forbidden'}), 403
        from bot import whatsapp_grupo
        payload = request.json or {}
        log.debug('[WHATSAPP-WEBHOOK] %s', json.dumps(payload, ensure_ascii=False)[:1500])
        # WAHA puede mandar {event, session, payload:{...}} o el payload directo
        msg = payload.get('payload') if 'payload' in payload else payload
        if not isinstance(msg, dict):
            return jsonify({'ok': True, 'ignored': 'no msg'})
        # Texto
        body = (msg.get('body') or '').strip()
        if not body:
            return jsonify({'ok': True, 'ignored': 'empty'})

        # ── Paso 1: persistir el mensaje + auto-vincular wa_id ────────────────
        push_name = (msg.get('notifyName') or msg.get('pushName')
                     or (msg.get('_data') or {}).get('notifyName') or '')
        chat_id = msg.get('from') or msg.get('chatId') or ''
        # participant: en mensajes de grupo es el wa_id de quien escribió.
        # En DMs (from = wa_id del cadete) participant suele venir vacío.
        participant = (msg.get('participant') or msg.get('author')
                       or (msg.get('_data') or {}).get('author') or '')
        es_grupo = chat_id.endswith('@g.us')
        # Si es un grupo, debe ser EXACTAMENTE el grupo de reparto configurado.
        # WAHA dispara webhooks por TODOS los grupos del numero vinculado
        # (incluye grupos personales del operador). Sin este filtro entraban
        # mensajes de cualquier lado al panel del grupo de cadetes.
        if es_grupo and chat_id != whatsapp_grupo.WAHA_GRUPO_ENVIOS:
            return jsonify({'ok': True, 'ignored': 'other_group'})
        wa_id_emisor = whatsapp_grupo.normalizar_wa_id(participant if es_grupo else chat_id)
        from_me = bool(msg.get('fromMe'))
        try:
            _persistir_mensaje_reparto(es_grupo=es_grupo, chat_id=chat_id,
                                       wa_id_emisor=wa_id_emisor,
                                       push_name=push_name, body=body,
                                       from_me=from_me)
        except Exception as e:  # noqa: BLE001 — la persistencia no debe romper el flujo de toma
            log.exception('persistir mensaje reparto falló: %s', e)

        # Si el mensaje es fromMe (lo mandó la farmacia desde WAHA), ya quedó
        # persistido por el endpoint responder del panel — NO duplicar.
        # Trade-off: si el operador escribe al grupo desde su celular físico
        # (no desde el panel), ese msg no aparece en el historial del panel.
        # Vivible: hoy todo sale por el panel.
        if from_me:
            return jsonify({'ok': True, 'ignored': 'self'})
        # ── Paso 2: si no es frase de toma, listo (ya quedó persistido) ───────
        if not whatsapp_grupo.es_frase_de_toma(body):
            return jsonify({'ok': True, 'ignored': 'not_take_phrase'})
        # Reply citando? WAHA expone el ID del msg original en payload.replyTo.id
        # (formato corto, sin el wrap "true_chat_<id>_<participant>"). Hacemos
        # matching parcial: el waha_msg_id que guardamos es el _serialized largo
        # y este ID corto está incluido adentro.
        reply_to = msg.get('replyTo') or {}
        quoted_id = reply_to.get('id') if isinstance(reply_to, dict) else None
        # Fallback a paths viejos (WAHA versiones anteriores)
        if not quoted_id:
            q = (msg.get('_data') or {}).get('quotedMsg')
            if isinstance(q, dict):
                _id = q.get('id')
                quoted_id = _id.get('_serialized') if isinstance(_id, dict) else _id
        if not quoted_id:
            return jsonify({'ok': True, 'ignored': 'no_quoted_msg'})
        # Buscar pedido por waha_msg_id (matching parcial — WAHA puede devolver
        # el id sin el prefijo 'true_' o el _serialized completo).
        with database.get_db() as s:
            P = database.PedidoReparto
            p = (s.query(P)
                 .filter(P.waha_msg_id.like(f'%{quoted_id}%'))
                 .first())
            if not p:
                # Fallback: probar al revés (quoted_id contiene waha_msg_id)
                cand = s.query(P).filter(P.waha_msg_id.isnot(None)).order_by(P.id.desc()).limit(50).all()
                p = next((x for x in cand if x.waha_msg_id and (x.waha_msg_id in quoted_id or quoted_id in x.waha_msg_id)), None)
            if not p:
                return jsonify({'ok': True, 'ignored': 'pedido_not_found', 'quoted_id': quoted_id})
            # Anti-doble-toma: si ya está tomado, avisar (push_name ya viene del paso 1)
            push_name_toma = push_name or msg.get('from') or 'alguien'
            if p.tomado_por_wsap:
                whatsapp_grupo.publicar_en_grupo(
                    f'⚠️ Pedido #{p.id} ya lo había tomado *{p.tomado_por_wsap}*.')
                return jsonify({'ok': True, 'ignored': 'ya_tomado', 'pedido_id': p.id})
            p.tomado_por_wsap = push_name_toma[:80]
            p.tomado_en = database.now_ar()
            # Match con tabla cadetes por nombre (case+espacios insensible).
            # Si encuentra → asigna cadete_id (queda visible en columna Cadete).
            from sqlalchemy import func as _func
            nombre_norm = ' '.join(push_name_toma.lower().split())
            cad = (s.query(database.Cadete)
                   .filter(_func.lower(database.Cadete.nombre).like(f'%{nombre_norm}%'))
                   .first())
            if cad:
                p.cadete_id = cad.id
                p.estado = 'en_ruta'   # opcional: al tomar lo pasa a en_ruta
                _notificar_cliente_pedido(s, p, 'en_ruta')
            s.commit()
            extra = f' (cadete del sistema: {cad.nombre})' if cad else ' (sin match en cadetes)'
            whatsapp_grupo.publicar_en_grupo(
                f'✅ Pedido #{p.id} tomado por *{push_name_toma}*.{extra}')
            return jsonify({'ok': True, 'pedido_id': p.id, 'tomado_por': push_name_toma,
                            'cadete_id': cad.id if cad else None})

    @app.route('/whatsapp/grupo/setup-webhook', methods=['POST'])
    @login_required
    def reparto_whatsapp_setup_webhook():
        """Endpoint admin: configura el webhook de WAHA para que apunte a
        nuestro receptor (web:5000/whatsapp/grupo/webhook, network docker)."""
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        from bot import whatsapp_grupo
        url_interno = 'http://web:5000/whatsapp/grupo/webhook'
        r = whatsapp_grupo.configurar_webhook(url_interno)
        return jsonify(r)

    # ── Telegram: webhook del grupo de cadetes (reemplaza WAHA, 2026-06) ────

    @app.route('/telegram/cadetes/webhook', methods=['POST'])
    def reparto_telegram_cadetes_webhook():
        """Recibe Updates de Telegram: callbacks del botón TOMAR + mensajes
        del grupo y DMs de cadetes.

        Flujo del TOMAR (callback_tomar):
          1) Identifica el pedido por callback_data ('tomar:<pedido_id>').
          2) Asignación atómica: si ya fue tomado, avisa al cadete que clickeó.
          3) Auto-magic: si el Cadete con ese nombre no tiene telegram_user_id,
             lo guarda. Si no hay match, deja sin cadete_id (queda en historial).
          4) Edita el mensaje del grupo: saca el botón + agrega 'Tomado por X'.
          5) DM al cadete con detalle completo del pedido.

        Sin login: Telegram hace POST desde su backend (no es alcanzable
        desde fuera salvo por Meta/Telegram con secret_token)."""
        from bot import telegram_grupo

        # Validar X-Telegram-Bot-Api-Secret-Token si está configurado.
        secret_header = request.headers.get('X-Telegram-Bot-Api-Secret-Token', '')
        if not telegram_grupo.validar_webhook_secret(secret_header):
            log.warning('Telegram webhook: secret inválido')
            return jsonify({'ok': False, 'error': 'forbidden'}), 403

        payload = request.json or {}
        upd = telegram_grupo.parsear_update(payload)
        tipo = upd.get('tipo')
        log.info('[telegram webhook] tipo=%s user=%s', tipo, upd.get('user_name'))

        if tipo == 'callback_tomar':
            log.info('[telegram callback_tomar] pid=%s user_id=%s user_name=%s',
                        upd.get('pedido_id'), upd.get('user_id'), upd.get('user_name'))
            return _telegram_procesar_tomar(upd, telegram_grupo)
        if tipo == 'callback_retirado':
            log.info('[telegram callback_retirado] pid=%s user_id=%s',
                        upd.get('pedido_id'), upd.get('user_id'))
            return _telegram_procesar_retirado(upd, telegram_grupo)
        if tipo == 'callback_entregado':
            log.info('[telegram callback_entregado] pid=%s user_id=%s',
                        upd.get('pedido_id'), upd.get('user_id'))
            return _telegram_procesar_entregado(upd, telegram_grupo)

        if tipo == 'mensaje_dm':
            texto_dm = (upd.get('texto') or '').strip()
            uid_dm = upd.get('user_id')
            nom_dm = upd.get('user_name') or upd.get('user_username') or 'alguien'
            # Deep link: /start ped_<id> → entregar el detalle si es el que tomó.
            if uid_dm and texto_dm.startswith('/start ped_'):
                raw = texto_dm[len('/start ped_'):].strip()
                if raw.isdigit():
                    return _telegram_entregar_detalle_dm(int(raw), uid_dm, nom_dm, telegram_grupo)
            log.info('telegram DM de %s: %s', nom_dm, texto_dm[:80])
            # Persistir el DM SI viene de un cadete reconocido (privacidad: no
            # guardamos DMs de gente random que escriba al bot). Lo dejamos
            # disponible en el panel /reparto/planilla → DMs cadetes.
            if uid_dm and texto_dm:
                try:
                    _persistir_dm_telegram(uid_dm, nom_dm, texto_dm)
                except Exception as e:  # noqa: BLE001
                    log.warning('persistir DM telegram falló: %s', e)
            return jsonify({'ok': True, 'tipo': 'mensaje_dm'})

        if tipo == 'mensaje_grupo':
            # No implementamos frase libre — el botón TOMAR es el único path.
            return jsonify({'ok': True, 'ignored': 'mensaje_grupo_sin_handler'})

        return jsonify({'ok': True, 'ignored': tipo or 'unknown'})

    def _telegram_procesar_tomar(upd, tg):
        """Asigna un pedido cuando un cadete clickea el botón TOMAR.
        Atómico: si ya fue tomado, el segundo cadete recibe un toast."""
        pedido_id = upd.get('pedido_id')
        user_id = upd.get('user_id')
        user_name = upd.get('user_name') or upd.get('user_username') or 'alguien'
        callback_qid = upd.get('callback_query_id')
        message_id = upd.get('message_id')
        if not pedido_id or not user_id or not callback_qid:
            tg.answer_callback(callback_qid, 'Datos inválidos', alert=True)
            return jsonify({'ok': False, 'error': 'callback inválido'}), 400

        with database.get_db() as s:
            P = database.PedidoReparto
            p = s.get(P, pedido_id)
            if not p:
                tg.answer_callback(callback_qid, 'Pedido no existe', alert=True)
                return jsonify({'ok': True, 'ignored': 'pedido_no_existe'})

            # Anti-doble-toma: si ya está tomado, avisar al cadete que clickeó.
            if p.tomado_por_wsap:
                tg.answer_callback(
                    callback_qid,
                    f'Ya lo tomó {p.tomado_por_wsap}',
                    alert=False)
                return jsonify({'ok': True, 'ignored': 'ya_tomado', 'pedido_id': p.id})

            # Auto-magic en 3 pasos:
            #  1) Buscar Cadete por telegram_user_id (vínculo previo).
            #  2) Si no, buscar por nombre LIKE (push_name → matching parcial)
            #     y completar el telegram_user_id si encuentra match único.
            #  3) Si tampoco, AUTO-CREAR un Cadete con el push_name como
            #     nombre + tg_user_id. Diego 2026-06-19: tomar un pedido ya
            #     es señal fuerte de que es cadete; mejor crearlo solo y que
            #     el operador edite nombre/teléfono/tarifa en /cadetes que
            #     que el pedido quede sin asignar.
            cad = (s.query(database.Cadete)
                   .filter(database.Cadete.telegram_user_id == user_id).first())
            if not cad:
                from sqlalchemy import func as _func
                nombre_norm = ' '.join(user_name.lower().split())
                if nombre_norm:
                    cad = (s.query(database.Cadete)
                           .filter(_func.lower(database.Cadete.nombre)
                                   .like(f'%{nombre_norm}%'))
                           .first())
                    if cad and not cad.telegram_user_id:
                        cad.telegram_user_id = user_id
            if not cad:
                cad = database.Cadete(
                    nombre=user_name[:60] or f'Cadete tg:{user_id}',
                    telegram_user_id=user_id,
                    token=uuid.uuid4().hex[:12],
                    activo=True)
                s.add(cad); s.flush()
                log.warning('[telegram TOMAR] auto-alta cadete id=%s nombre=%r '
                            'tg_user_id=%s (editalo en /cadetes)',
                            cad.id, cad.nombre, user_id)

            # TOMAR: estado nuevo 'tomado' (cadete se reservó el pedido, todavía
            # no fue a retirar a la farmacia). Diego 2026-06-19: separamos
            # 'tomado' → 'en_ruta' (al apretar Retirado) → 'entregado'.
            p.tomado_por_wsap = user_name[:80]
            p.tomado_en = database.now_ar()
            p.tomado_dm_user_id = user_id
            p.estado = 'tomado'
            if cad:
                p.cadete_id = cad.id
            # NO notificar al cliente acá — solo cuando pase a 'en_ruta'
            # (Retirado) o 'entregado'. Tomar es estado interno.

            # Diego 2026-06-19: NO publicar nada nuevo en el grupo de Telegram
            # ni en la conv interna del grupo. El seguimiento pasa al DM del
            # cadete. (Antes había un BotMensaje "✅ Pedido #X tomado por Y"
            # que sumaba ruido — sacado.)

            s.commit()
            cad_id = cad.id if cad else None
            cad_nombre = cad.nombre if cad else None

        # Toast al cadete que clickeó.
        tg.answer_callback(callback_qid, '✅ Tomado · vení a retirar')

        # Reemplazar el botón TOMAR por "📦 RETIRADO de farmacia" en el mismo
        # mensaje del grupo. NO mandar DM al cadete todavía: el detalle (que
        # tiene info sensible como vuelto, OS, paga_con) se manda recién cuando
        # confirma el retiro físico (estado en_ruta). Esto evita que el cadete
        # acumule DMs de pedidos que después se liberan por demora (cron SLA).
        if message_id:
            botones = [[{'text': '📦 RETIRADO de farmacia',
                         'callback_data': f'retirado:{pedido_id}'}]]
            tg.setear_botones_grupo(message_id, botones)

        return jsonify({'ok': True, 'pedido_id': pedido_id, 'tomado_por': user_name,
                        'cadete_id': cad_id})

    def _telegram_procesar_retirado(upd, tg):
        """Cadete apretó '📦 RETIRADO de farmacia' en el GRUPO (no en DM).
        Acciones:
          1. Valida estado='tomado' (sino: fue desasignado o ya está más adelante).
          2. Valida que sea el cadete que tomó (anti-abuso).
          3. Estado → 'en_ruta'.
          4. Notifica al cliente vía WAHA ('Tu pedido salió').
          5. Manda DM al cadete con DETALLE COMPLETO + botón '✅ Entregado'.
             Diego 2026-06-21: el detalle se manda recién acá (no al TOMAR)
             para no ensuciar Telegram del cadete con pedidos que después
             se liberan por demora (cron SLA).
          6. Saca el botón del mensaje del grupo (queda como info sin acción).
        """
        pedido_id = upd.get('pedido_id')
        user_id = upd.get('user_id')
        callback_qid = upd.get('callback_query_id')
        message_id_grupo = upd.get('message_id')  # ID del mensaje en el grupo
        if not pedido_id or not callback_qid:
            return jsonify({'ok': False, 'error': 'callback inválido'}), 400
        with database.get_db() as s:
            p = s.get(database.PedidoReparto, pedido_id)
            if not p:
                tg.answer_callback(callback_qid, 'Pedido no existe', alert=True)
                return jsonify({'ok': True, 'ignored': 'pedido_no_existe'})
            # El pedido tiene que estar en 'tomado' (no en 'en_ruta', 'entregado'
            # ni 'pendiente' por desasignación del cron SLA).
            if p.estado != 'tomado':
                tg.answer_callback(
                    callback_qid,
                    f'Pedido ya no disponible (estado: {p.estado})',
                    alert=True)
                return jsonify({'ok': True, 'ignored': 'estado_no_tomado',
                                'estado': p.estado})
            # Validar que sea el cadete que tomó (anti-abuso).
            if p.tomado_dm_user_id and p.tomado_dm_user_id != user_id:
                tg.answer_callback(callback_qid, 'No sos el cadete asignado', alert=True)
                return jsonify({'ok': True, 'ignored': 'cadete_distinto'})
            p.estado = 'en_ruta'
            p.retirado_en = database.now_ar()
            try:
                _notificar_cliente_pedido(s, p, 'en_ruta')
            except Exception as e:  # noqa: BLE001
                log.warning('notificar cliente en_ruta falló: %s', e)
            detalle = _telegram_armar_detalle_pedido(p)
            s.commit()
        tg.answer_callback(callback_qid, '✓ En ruta')
        # Sacar botón del mensaje del grupo (queda como info, sin acción).
        if message_id_grupo:
            tg.sacar_botones_grupo(message_id_grupo)
        # Mandar DM al cadete con detalle + botón Entregado.
        botones = [[{'text': '✅ Entregado al cliente',
                     'callback_data': f'entregado:{pedido_id}'}]]
        res_dm = tg.enviar_dm(user_id, detalle, botones=botones)
        if res_dm.get('ok'):
            # Persistir el message_id del DM para que _telegram_procesar_entregado
            # pueda editar el botón al apretar Entregado.
            try:
                with database.get_db() as s:
                    p2 = s.get(database.PedidoReparto, pedido_id)
                    if p2:
                        p2.tomado_dm_msg_id = res_dm.get('telegram_msg_id')
                        s.commit()
            except Exception as e:  # noqa: BLE001
                log.warning('Persistir tomado_dm_msg_id falló: %s', e)
        else:
            log.warning('Telegram DM detalle (en_ruta) falló: %s', res_dm.get('error'))
        return jsonify({'ok': True, 'pedido_id': pedido_id, 'estado': 'en_ruta'})

    def _telegram_procesar_entregado(upd, tg):
        """Cadete apretó '✅ Entregado al cliente'. Estado → 'entregado'.
        Saca el botón del DM (queda inerte)."""
        pedido_id = upd.get('pedido_id')
        user_id = upd.get('user_id')
        callback_qid = upd.get('callback_query_id')
        if not pedido_id or not callback_qid:
            return jsonify({'ok': False, 'error': 'callback inválido'}), 400
        with database.get_db() as s:
            p = s.get(database.PedidoReparto, pedido_id)
            if not p:
                tg.answer_callback(callback_qid, 'Pedido no existe', alert=True)
                return jsonify({'ok': True, 'ignored': 'pedido_no_existe'})
            # Solo desde 'en_ruta'. Si está en otro estado: doble-click, ya
            # entregado, o algo raro — abortar sin pisar nada.
            if p.estado != 'en_ruta':
                tg.answer_callback(
                    callback_qid,
                    f'Pedido no está en ruta (estado: {p.estado})',
                    alert=True)
                return jsonify({'ok': True, 'ignored': 'estado_no_en_ruta',
                                'estado': p.estado})
            if p.tomado_dm_user_id and p.tomado_dm_user_id != user_id:
                tg.answer_callback(callback_qid, 'No sos el cadete asignado', alert=True)
                return jsonify({'ok': True, 'ignored': 'cadete_distinto'})
            p.estado = 'entregado'
            p.entregado_en = database.now_ar()
            try:
                _notificar_cliente_pedido(s, p, 'entregado')
            except Exception as e:  # noqa: BLE001
                log.warning('notificar cliente entregado falló: %s', e)
            dm_msg_id = p.tomado_dm_msg_id
            s.commit()
        tg.answer_callback(callback_qid, '✅ Entregado')
        # Sacar los botones del DM (fin del workflow del cadete).
        if dm_msg_id and user_id:
            tg.editar_botones_dm(user_id, dm_msg_id, [])
        return jsonify({'ok': True, 'pedido_id': pedido_id, 'estado': 'entregado'})

    def _telegram_armar_detalle_pedido(p):
        """Texto HTML con el detalle del pedido para el DM al cadete.
        Incluye total, lo que tiene que cobrar (si no está pagado), forma de
        pago, vuelto. Diego 2026-06-19/23: el cadete usa este DM como única
        fuente de info en la calle.

        Mejoras 2026-06-23:
        - Header con hora de publicación (creado_en) → 'publicado HH:MM'
        - Badge ⭐ PAMI cuando p.obra_social contiene 'PAMI'
        - Link MP en línea aparte si forma=link_mp (Telegram lo hace clickeable)
        - Caja 'COBRAR' más visible con separadores
        """
        def _arg(n):
            return f'$ {float(n):,.0f}'.replace(',', '.')

        # Header con timestamp del pedido
        header = f'📦 <b>Pedido #{p.id}</b>'
        if p.creado_en:
            try:
                # Hora local; ahora calculamos cuánto tiempo pasó.
                ahora = database.now_ar()
                delta = ahora - p.creado_en
                mins = int(delta.total_seconds() // 60)
                if 0 <= mins < 60:
                    when = f'hace {mins} min'
                elif mins < 60 * 24 and mins >= 0:
                    when = f'a las {p.creado_en.strftime("%H:%M")}'
                else:
                    when = p.creado_en.strftime('%d/%m %H:%M')
                header += f' · <i>{when}</i>'
            except Exception:  # noqa: BLE001
                pass
        partes = [header]

        if p.cliente_nombre:
            cliente_n = f'👤 <b>{p.cliente_nombre}</b>'
            # Badge PAMI destacado al lado del nombre (más fácil de ver que
            # como 'OS: PAMI' enterrado en Detalle).
            if p.obra_social and 'pami' in p.obra_social.lower():
                cliente_n += ' ⭐ <b>PAMI</b>'
            partes.append(cliente_n)
        if p.telefono:
            partes.append(f'📞 {p.telefono}')
        if p.direccion:
            d = p.direccion
            if p.piso:
                d += f', piso {p.piso}'
            if p.depto:
                d += f', dto {p.depto}'
            partes.append(f'📍 {d}')
            if p.localidad:
                partes.append(f'   {p.localidad}')
            if p.referencia:
                partes.append(f'   ↳ {p.referencia}')
        if p.lat and p.lng:
            partes.append(f'🗺 https://www.google.com/maps?q={p.lat},{p.lng}')

        partes.append('')
        partes.append('━━━ <b>PAGO</b> ━━━')
        # total_paciente incluye el envío (el operador carga el total final).
        total = float(p.total_paciente) if p.total_paciente is not None else (
            float(p.importe) if p.importe is not None else None)
        if total is not None:
            partes.append(f'💵 Total: <b>{_arg(total)}</b>')
        if p.envio_costo is not None:
            envio_txt = 'SIN CARGO' if p.envio_sin_cargo else _arg(p.envio_costo)
            partes.append(f'🛵 Envío al cliente: {envio_txt}')
        if p.forma_pago:
            partes.append(f'💳 Forma: <b>{p.forma_pago}</b>')

        # Cobrar / pagado — caja destacada
        if p.pagado:
            partes.append('')
            partes.append('✅ <b>YA ESTÁ PAGADO</b>')
            partes.append('   <i>no cobrar al cliente</i>')
        elif total is not None:
            partes.append('')
            partes.append('🟡 <b>━━━━━━━━━━━━━━━━━</b>')
            partes.append(f'💰 <b>COBRAR: {_arg(total)}</b>')
            partes.append('🟡 <b>━━━━━━━━━━━━━━━━━</b>')

        # Detalles de pago según forma
        if p.paga_con is not None:
            partes.append(f'   paga con: {_arg(p.paga_con)}')
        if p.vuelto:
            partes.append(f'   vuelto: $ {p.vuelto}')
        # Link MP clickeable (Telegram lo autoreconoce) si forma es link_mp.
        if (p.forma_pago or '').lower() in ('link_mp', 'link mp', 'mercado pago', 'mp') and p.link_mp:
            partes.append(f'   🔗 {p.link_mp}')
        if p.dato_pago_mp:
            partes.append(f'   nro op: <code>{p.dato_pago_mp}</code>')

        partes.append('')
        partes.append('━━━ <b>DETALLE</b> ━━━')
        if p.producto:
            partes.append(f'💊 {p.producto}')
        if p.observacion:
            partes.append(f'📝 {p.observacion}')
        # OS solo si NO es PAMI (PAMI ya se mostró arriba como badge).
        if p.obra_social and 'pami' not in p.obra_social.lower():
            partes.append(f'🏥 OS: {p.obra_social}')
        meta = []
        if p.prioridad and p.prioridad != 'normal':
            meta.append({'urgente': '🔴 URGENTE', 'programado': '🕐 prog.'}
                        .get(p.prioridad, p.prioridad))
        if p.receta_estado == 'pendiente' or (p.receta_estado is None and bool(p.requiere_receta)):
            meta.append('📋 receta pendiente')
        if p.requiere_firma:
            meta.append('✍ requiere firma')
        if meta:
            partes.append(' · '.join(meta))
        return '\n'.join(partes)

    def _telegram_entregar_detalle_dm(pedido_id, user_id, user_name, tg):
        """Deep link /start ped_<id>: entrega el detalle por DM SOLO si quien lo
        abre es el cadete que tomó el pedido. De paso aprende/vincula su
        telegram_user_id (auto-onboarding)."""
        from sqlalchemy import func as _f
        nom = ' '.join((user_name or '').lower().split())
        autorizado = False
        detalle = None
        with database.get_db() as s:
            p = s.get(database.PedidoReparto, pedido_id)
            if not p:
                tg.enviar_dm(user_id, 'Ese pedido no existe.')
                return jsonify({'ok': True, 'detalle': 'no_existe'})
            # Identificar al cadete por telegram_user_id; si no, por nombre (y aprender).
            cad = (s.query(database.Cadete)
                   .filter(database.Cadete.telegram_user_id == user_id).first())
            if not cad and nom:
                cad = (s.query(database.Cadete)
                       .filter(_f.lower(database.Cadete.nombre).like(f'%{nom}%')).first())
                if cad and not cad.telegram_user_id:
                    cad.telegram_user_id = user_id
            # Si el pedido lo tomó este cadete (por nombre) y no estaba vinculado, vincular.
            if p.cadete_id is None and cad and p.tomado_por_wsap \
               and ' '.join(p.tomado_por_wsap.lower().split()) == nom:
                p.cadete_id = cad.id
            autorizado = bool(cad and p.cadete_id == cad.id)
            if autorizado:
                detalle = _telegram_armar_detalle_pedido(p)
            s.commit()
        if autorizado and detalle:
            tg.enviar_dm(user_id, detalle)
            return jsonify({'ok': True, 'detalle': 'enviado'})
        tg.enviar_dm(user_id, 'Ese pedido no está asignado a vos.')
        return jsonify({'ok': True, 'detalle': 'no_autorizado'})

    @app.route('/telegram/cadetes/setup-webhook', methods=['POST'])
    @login_required
    def reparto_telegram_setup_webhook():
        """Endpoint admin: configura el webhook de Telegram apuntando al
        endpoint público (Render). Llamar UNA vez después de deploy o cuando
        cambie la URL pública."""
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        from bot import telegram_grupo
        url = (request.json or {}).get('url')
        if not url:
            return jsonify({'ok': False,
                            'error': 'pasá url en el body ({"url": "https://..."})'}), 400
        r = telegram_grupo.setear_webhook(url)
        return jsonify(r)

    # ── Chat de reparto (panel en /reparto/planilla) ────────────────────────
    # 5 endpoints que sirven la lista de conversaciones (grupo + DMs) y permiten
    # mandar mensajes desde el panel sin salir de la planilla.

    def _conv_grupo_or_none(s):
        # Migración WAHA → Telegram (2026-06): el canal sigue siendo
        # 'whatsapp_grupo' por compat con queries históricas (busca convs
        # creadas en ambas eras). El canal_user_id pasó a ser el chat_id de
        # Telegram (string del int negativo).
        from bot import telegram_grupo as _tg
        if not _tg.GRUPO_CHAT_ID:
            return None
        return (s.query(database.BotConversacion)
                .filter_by(canal='whatsapp_grupo', canal_user_id=str(_tg.GRUPO_CHAT_ID))
                .first())

    def _msg_to_dict(m):
        return {'id': m.id, 'origen': m.origen, 'texto': m.texto,
                'creado_en': m.creado_en.isoformat() if m.creado_en else None}

    @app.route('/api/reparto/chat/resumen')
    @login_required
    def api_reparto_chat_resumen():
        """Listado para el panel: estado del grupo + DMs por cadete ordenados
        por último mensaje. El frontend hace polling de este endpoint cada 3s."""
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        with database.get_db() as s:
            grupo_info = None
            grupo = _conv_grupo_or_none(s)
            if grupo:
                ult = (s.query(database.BotMensaje)
                       .filter_by(conversacion_id=grupo.id)
                       .order_by(database.BotMensaje.id.desc()).first())
                grupo_info = {
                    'conv_id': grupo.id,
                    'ultimo': ult.texto[:80] if ult else '',
                    'ultimo_en': ult.creado_en.isoformat() if (ult and ult.creado_en) else None,
                    'ultimo_origen': ult.origen if ult else None,
                }
            # DMs: conversaciones con cadete_id O del canal telegram_cadete
            # (incluye huérfanos sin vincular, así el operador los ve y los
            # puede asignar). Limitamos a últimas 50.
            from sqlalchemy import or_ as _or
            convs = (s.query(database.BotConversacion)
                     .filter(_or(
                         database.BotConversacion.cadete_id.isnot(None),
                         database.BotConversacion.canal == 'telegram_cadete'))
                     .order_by(database.BotConversacion.ultimo_en.desc())
                     .limit(50).all())
            dms = []
            for c in convs:
                ult = (s.query(database.BotMensaje)
                       .filter_by(conversacion_id=c.id)
                       .order_by(database.BotMensaje.id.desc()).first())
                cad = s.get(database.Cadete, c.cadete_id) if c.cadete_id else None
                dms.append({
                    'conv_id': c.id,
                    'cadete_id': c.cadete_id,
                    'sin_vincular': c.cadete_id is None,
                    'tg_user_id': c.canal_user_id if c.canal == 'telegram_cadete' else None,
                    'nombre': cad.nombre if cad else (c.nombre_cliente or 'Sin vincular'),
                    'ultimo': ult.texto[:80] if ult else '',
                    'ultimo_en': ult.creado_en.isoformat() if (ult and ult.creado_en) else None,
                    'ultimo_origen': ult.origen if ult else None,
                })
            return jsonify({'ok': True, 'grupo': grupo_info, 'dms': dms})

    @app.route('/api/reparto/chat/grupo/mensajes')
    @login_required
    def api_reparto_chat_grupo_mensajes():
        """Mensajes del grupo. ?desde_id=N → solo posteriores (para append
        incremental en el polling). Sin desde_id → últimos 80."""
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        try:
            desde_id = int(request.args.get('desde_id') or 0)
        except (TypeError, ValueError):
            desde_id = 0
        with database.get_db() as s:
            grupo = _conv_grupo_or_none(s)
            if not grupo:
                return jsonify({'ok': True, 'mensajes': [], 'conv_id': None})
            q = s.query(database.BotMensaje).filter_by(conversacion_id=grupo.id)
            if desde_id:
                q = q.filter(database.BotMensaje.id > desde_id)
            else:
                q = q.order_by(database.BotMensaje.id.desc()).limit(80)
            msgs = q.all()
            if not desde_id:
                msgs = sorted(msgs, key=lambda m: m.id)
            return jsonify({'ok': True, 'conv_id': grupo.id,
                            'mensajes': [_msg_to_dict(m) for m in msgs]})

    @app.route('/api/reparto/chat/dm/<int:conv_id>/mensajes')
    @login_required
    def api_reparto_chat_dm_mensajes(conv_id):
        """DM con un cadete o conv huérfana (sin cadete vinculado todavía).
        Identifica por conv_id directo, así soporta ambos casos."""
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        try:
            desde_id = int(request.args.get('desde_id') or 0)
        except (TypeError, ValueError):
            desde_id = 0
        with database.get_db() as s:
            conv = s.get(database.BotConversacion, conv_id)
            if not conv:
                return jsonify({'ok': True, 'mensajes': [], 'conv_id': None})
            q = s.query(database.BotMensaje).filter_by(conversacion_id=conv.id)
            if desde_id:
                q = q.filter(database.BotMensaje.id > desde_id)
            else:
                q = q.order_by(database.BotMensaje.id.desc()).limit(80)
            msgs = q.all()
            if not desde_id:
                msgs = sorted(msgs, key=lambda m: m.id)
            return jsonify({'ok': True, 'conv_id': conv.id,
                            'mensajes': [_msg_to_dict(m) for m in msgs]})

    @app.route('/api/reparto/chat/grupo/responder', methods=['POST'])
    @login_required
    def api_reparto_chat_grupo_responder():
        """El operador escribe al grupo desde el panel. Manda por WAHA y
        persiste como BotMensaje(origen='operador')."""
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        texto = ((request.json or {}).get('texto') or '').strip()
        if not texto:
            return jsonify({'ok': False, 'error': 'texto vacío'}), 400
        from bot import telegram_grupo
        r = telegram_grupo.publicar_en_grupo(texto)
        if not r.get('ok'):
            return jsonify({'ok': False, 'error': r.get('error') or 'fallo Telegram'}), 502
        # Persistir el mensaje saliente.
        with database.get_db() as s:
            grupo = _conv_grupo_or_none(s)
            if not grupo:
                # Si el grupo aún no tiene conv (1er mensaje saliente nunca), crearla.
                grupo = database.BotConversacion(
                    canal='whatsapp_grupo',
                    canal_user_id=str(telegram_grupo.GRUPO_CHAT_ID),
                    nombre_cliente='Grupo de cadetes',
                    estado_atencion='humano', nodo='reparto')
                s.add(grupo); s.flush()
            m = database.BotMensaje(conversacion_id=grupo.id, origen='operador', texto=texto)
            s.add(m)
            grupo.ultimo_en = database.now_ar()
            grupo.operador_user_id = current_user.id
            s.commit()
            return jsonify({'ok': True, 'mensaje': _msg_to_dict(m)})

    @app.route('/api/reparto/chat/dm/<int:conv_id>/responder', methods=['POST'])
    @login_required
    def api_reparto_chat_dm_responder(conv_id):
        """El operador escribe a una conv DM. La conv puede tener Cadete
        vinculado (canal 'telegram_cadete' con cadete_id) o ser huérfana
        (canal_user_id = tg user_id, cadete_id NULL). En ambos casos
        sacamos el tg_user_id de donde haya."""
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        texto = ((request.json or {}).get('texto') or '').strip()
        if not texto:
            return jsonify({'ok': False, 'error': 'texto vacío'}), 400
        from bot import telegram_grupo
        with database.get_db() as s:
            conv = s.get(database.BotConversacion, conv_id)
            if not conv:
                return jsonify({'ok': False, 'error': 'conv no existe'}), 404
            # Resolver tg_user_id: prioridad al Cadete vinculado, fallback al
            # canal_user_id de la conv (caso huérfano).
            tg_uid = None
            cadete = s.get(database.Cadete, conv.cadete_id) if conv.cadete_id else None
            if cadete and cadete.telegram_user_id:
                tg_uid = cadete.telegram_user_id
            elif conv.canal == 'telegram_cadete' and conv.canal_user_id:
                try:
                    tg_uid = int(conv.canal_user_id)
                except (TypeError, ValueError):
                    tg_uid = None
            if not tg_uid:
                return jsonify({'ok': False, 'error':
                                'no tengo telegram_user_id para esta conv (¿WAHA legacy?)'}), 400
            r = telegram_grupo.enviar_dm(tg_uid, texto)
            if not r.get('ok'):
                return jsonify({'ok': False, 'error': r.get('error') or 'fallo Telegram'}), 502
            m = database.BotMensaje(conversacion_id=conv.id, origen='operador', texto=texto)
            s.add(m)
            conv.ultimo_en = database.now_ar()
            conv.operador_user_id = current_user.id
            s.commit()
            return jsonify({'ok': True, 'mensaje': _msg_to_dict(m)})

    @app.route('/api/reparto/chat/dm/<int:conv_id>/vincular-cadete', methods=['POST'])
    @login_required
    def api_reparto_chat_dm_vincular(conv_id):
        """Toma una conv huérfana (canal=telegram_cadete, cadete_id=NULL),
        crea un Cadete nuevo con el nombre que recibió y la vincula. El
        operador puede editar todos los campos del cadete en /cadetes."""
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        nombre = ((request.json or {}).get('nombre') or '').strip()
        if not nombre:
            return jsonify({'ok': False, 'error': 'falta nombre'}), 400
        with database.get_db() as s:
            conv = s.get(database.BotConversacion, conv_id)
            if not conv:
                return jsonify({'ok': False, 'error': 'conv no existe'}), 404
            if conv.cadete_id:
                return jsonify({'ok': False, 'error': 'la conv ya está vinculada'}), 400
            if conv.canal != 'telegram_cadete' or not conv.canal_user_id:
                return jsonify({'ok': False, 'error': 'la conv no es de Telegram'}), 400
            try:
                tg_uid = int(conv.canal_user_id)
            except (TypeError, ValueError):
                return jsonify({'ok': False, 'error': 'canal_user_id no es numérico'}), 400
            # Si ya hay un cadete con ese tg_user_id, solo vincular (no duplicar).
            cad = (s.query(database.Cadete)
                   .filter(database.Cadete.telegram_user_id == tg_uid).first())
            if not cad:
                cad = database.Cadete(
                    nombre=nombre[:60],
                    telegram_user_id=tg_uid,
                    token=uuid.uuid4().hex[:12],
                    activo=True)
                s.add(cad); s.flush()
            conv.cadete_id = cad.id
            conv.nombre_cliente = cad.nombre
            s.commit()
            return jsonify({'ok': True, 'cadete_id': cad.id})

    @app.route('/api/reparto/alertas-cadetes')
    @login_required
    def api_reparto_alertas_cadetes():
        """Devuelve la lista de DMs de cadetes con un mensaje pendiente de
        respuesta (último msg es del cadete y pasaron > N min).

        Niveles:
          - 'aviso': pasó sla_respuesta_cadete_aviso_min → banner sticky.
          - 'modal': pasó sla_respuesta_cadete_modal_min → modal bloqueante.

        El frontend de /reparto/planilla hace polling a este endpoint para
        levantar el banner y/o el modal.
        """
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        from bot import envio as _envio_mod
        cfg = _envio_mod.get_config()
        aviso_min = int(cfg['sla_respuesta_cadete_aviso_min'])
        modal_min = int(cfg['sla_respuesta_cadete_modal_min'])
        ahora = database.now_ar()
        alertas = []
        with database.get_db() as s:
            from sqlalchemy import desc as _desc
            # Convs DM cadete. Tomamos también las huérfanas (sin cadete_id)
            # porque el operador igual tiene que responder.
            convs = (s.query(database.BotConversacion)
                     .filter(database.BotConversacion.canal == 'telegram_cadete')
                     .all())
            for conv in convs:
                ultimo = (s.query(database.BotMensaje)
                          .filter(database.BotMensaje.conversacion_id == conv.id)
                          .order_by(_desc(database.BotMensaje.creado_en))
                          .first())
                if not ultimo or ultimo.origen != 'cliente':
                    continue
                minutos = (ahora - ultimo.creado_en).total_seconds() / 60
                if minutos < aviso_min:
                    continue
                nivel = 'modal' if minutos >= modal_min else 'aviso'
                alertas.append({
                    'conv_id': conv.id,
                    'cadete_id': conv.cadete_id,
                    'cadete_nombre': conv.nombre_cliente or 'Sin vincular',
                    'ultimo_texto': (ultimo.texto or '')[:80],
                    'minutos': int(minutos),
                    'nivel': nivel,
                })
        # Ordenar por minutos desc (las más urgentes arriba).
        alertas.sort(key=lambda a: -a['minutos'])
        return jsonify({'ok': True, 'alertas': alertas,
                        'aviso_min': aviso_min, 'modal_min': modal_min})

    @app.route('/reparto/pedido/<int:pid>/publicar', methods=['POST'])
    @login_required
    def reparto_pedido_publicar(pid):
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        from bot import telegram_grupo
        with database.get_db() as s:
            p = s.get(database.PedidoReparto, pid)
            if not p:
                return jsonify({'ok': False, 'error': 'no existe'}), 404
            # Armar texto del mensaje. ⚠️ PRIVACIDAD: el grupo de cadetes solo
            # necesita ubicación para decidir si lo toma. NO mandar nombre, teléfono,
            # producto, total, forma de pago, vuelto, observación ni receta — todo
            # ese detalle se le pasa al cadete por chat 1:1 cuando lo tome (DM).
            partes = [f'🚚 <b>Pedido #{p.id}</b>']
            if p.direccion:
                partes.append(f'📍 {p.direccion}')
            if p.lat is not None and p.lng is not None:
                partes.append(f'🗺 https://www.google.com/maps?q={p.lat},{p.lng}')
            meta = []
            if p.turno:
                meta.append({'mañana': '🌅 Mañana', 'tarde': '🌆 Tarde'}.get(p.turno, p.turno))
            if p.prioridad == 'urgente':
                meta.append('🚨 URGENTE')
            if meta:
                partes.append(' · '.join(meta))
            # Cobro: el grupo solo necesita saber SI hay que cobrar (efectivo).
            # El monto/vuelto va por DM 1:1 cuando lo toman (privacidad).
            if not p.pagado and (p.forma_pago or '').strip().lower() == 'efectivo':
                partes.append('💵 <b>COBRAR en efectivo</b>')
            else:
                partes.append('✅ Pagado — no cobrar')
            partes.append('')
            partes.append('Click <b>TOMAR</b> abajo para asignártelo.')
            texto = '\n'.join(partes)
            # Versión sin tags HTML para persistir en BotConversacion: la
            # planilla muestra texto plano y veríamos los <b>..</b> literales.
            texto_plano = texto.replace('<b>', '').replace('</b>', '')
            # publicar_pedido manda al grupo CON botón inline 'TOMAR'.
            # El callback_data del botón es 'tomar:<pid>' → cuando un cadete
            # clickea, el webhook /telegram/cadetes/webhook lo procesa.
            r = telegram_grupo.publicar_pedido(texto, p.id)
            if not r.get('ok'):
                return jsonify({'ok': False, 'error': r.get('error')
                                or 'sin respuesta Telegram'}), 502
            # Reusamos la columna waha_msg_id para guardar el message_id de
            # Telegram (es un int, lo guardamos como string). Eso permite que
            # el webhook edite el mensaje al asignarse (sacar botón TOMAR).
            p.waha_msg_id = str(r.get('telegram_msg_id') or '')
            p.publicado_en = database.now_ar()
            # Avanzar el estado a 'publicado' solo si todavía no había salido
            # del flujo pre-publicación. Republish de un pedido ya tomado /
            # en_ruta no lo regresa al estado anterior.
            if p.estado in ('pendiente', 'en_planilla', 'esperando_drog'):
                p.estado = 'publicado'
            # Persistir el publish en la conv del grupo para que aparezca en el
            # panel de /reparto/planilla (sino la timeline del grupo solo
            # tendría lo que llega del webhook).
            grupo_uid = str(telegram_grupo.GRUPO_CHAT_ID) if telegram_grupo.GRUPO_CHAT_ID else None
            grupo = (s.query(database.BotConversacion)
                     .filter_by(canal='whatsapp_grupo', canal_user_id=grupo_uid)
                     .first()) if grupo_uid else None
            if not grupo and grupo_uid:
                grupo = database.BotConversacion(
                    canal='whatsapp_grupo',
                    canal_user_id=grupo_uid,
                    nombre_cliente='Grupo de cadetes',
                    estado_atencion='humano', nodo='reparto')
                s.add(grupo); s.flush()
            if grupo:
                s.add(database.BotMensaje(conversacion_id=grupo.id,
                                          origen='operador', texto=texto_plano))
                grupo.ultimo_en = database.now_ar()
            s.commit()
            return jsonify({'ok': True, 'waha_msg_id': p.waha_msg_id,
                            'publicado_en': p.publicado_en.isoformat()})

    @app.route('/reparto/pedido/<int:pid>/asignar', methods=['POST'])
    @login_required
    def reparto_asignar(pid):
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        ruta_id = (request.json or {}).get('ruta_id')
        with database.get_db() as s:
            p = s.get(database.PedidoReparto, pid)
            if not p:
                return jsonify({'ok': False, 'error': 'no existe'}), 404
            p.ruta_id = ruta_id or None
            s.commit()
        return jsonify({'ok': True})

    @app.route('/reparto/pedido/<int:pid>/estado', methods=['POST'])
    @login_required
    def reparto_estado(pid):
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        estado = (request.json or {}).get('estado', 'pendiente')
        with database.get_db() as s:
            p = s.get(database.PedidoReparto, pid)
            if not p:
                return jsonify({'ok': False, 'error': 'pedido no existe'}), 404
            # Guard server-side: un pedido esperando stock de droguería NO puede
            # marcarse entregado. El botón está oculto en la UI, pero el POST
            # entraba igual (bypass). Diego review 2026-06-24.
            if p.estado == 'esperando_drog' and estado == 'entregado':
                return jsonify({'ok': False,
                                'error': 'El pedido está esperando stock de droguería; '
                                         'no se puede marcar entregado.'}), 400
            p.estado = estado
            s.commit()
        return jsonify({'ok': True})

    @app.route('/reparto/pedido/<int:pid>/cobrar', methods=['POST'])
    @login_required
    def reparto_cobrar(pid):
        """Marca/desmarca el pedido como cobrado por el cadete (panel interno)."""
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        pagado = bool((request.json or {}).get('pagado', True))
        with database.get_db() as s:
            p = s.get(database.PedidoReparto, pid)
            if p:
                p.pagado = pagado
                s.commit()
        return jsonify({'ok': True})

    @app.route('/reparto/cadete/<int:cid>/liquidar', methods=['POST'])
    @login_required
    def reparto_liquidar_cadete(cid):
        """Liquida los envíos del cadete: marca como liquidados sus pedidos
        ENTREGADOS y aún no liquidados de la fecha. Usa el cadete EFECTIVO
        (override por pedido o el de su ruta), igual que el control. Devuelve
        cuántos y el total liquidado."""
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        fecha = _fecha((request.json or {}).get('fecha'))
        P = database.PedidoReparto
        total = 0.0
        n = 0
        with database.get_db() as s:
            rutas_cad = {r.id: r.cadete_id for r in s.query(database.RutaReparto)
                         .filter(database.RutaReparto.cadete_id.isnot(None)).all()}
            ps = (s.query(P).filter(P.fecha == fecha, P.estado == 'entregado',
                                    P.envio_liquidado.is_(False)).all())
            ahora = database.now_ar()
            for p in ps:
                if reparto.cadete_efectivo_id(p, rutas_cad) != cid:
                    continue
                p.envio_liquidado = True
                p.envio_liquidado_en = ahora
                total += float(p.envio_costo or 0)
                n += 1
            s.commit()
        return jsonify({'ok': True, 'liquidados': n, 'total': total})

    @app.route('/reparto/pedido/<int:pid>/liquidar', methods=['POST'])
    @login_required
    def reparto_liquidar_pedido(pid):
        """Marca/desmarca UN envío como pagado al cadete (toggle envio_liquidado).
        Granular: complementa el 'Liquidar' por cadete (que es en lote)."""
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        with database.get_db() as s:
            p = s.get(database.PedidoReparto, pid)
            if not p:
                return jsonify({'ok': False, 'error': 'no existe'}), 404
            p.envio_liquidado = not p.envio_liquidado
            p.envio_liquidado_en = database.now_ar() if p.envio_liquidado else None
            estado = p.envio_liquidado
            s.commit()
        return jsonify({'ok': True, 'envio_liquidado': estado})

    @app.route('/reparto/pedido/<int:pid>/delete', methods=['POST'])
    @login_required
    def reparto_eliminar(pid):
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        try:
            with database.get_db() as s:
                p = s.get(database.PedidoReparto, pid)
                if p:
                    s.delete(p)
                    s.commit()
            return jsonify({'ok': True})
        except Exception as e:  # noqa: BLE001
            log.exception('borrar pedido #%s fallo: %s', pid, e)
            return jsonify({'ok': False, 'error': str(e)[:200]}), 500

    @app.route('/reparto/pedido/<int:pid>/ticket-pdf')
    @login_required
    def reparto_ticket_pdf(pid):
        """PDF del ticket del cadete (formato 80mm de ancho) para imprimir desde
        el browser. Diego 2026-06-16: prefirió PDF en vez de impresión directa
        ESC/POS por DockerPanel — la impresión la dispara desde Windows.

        Genera con reportlab usando un width de 80mm y fuente Courier monoespaciada.
        Incluye todo lo que el cadete necesita para entregar."""
        if not _ok():
            return 'sin permiso', 403
        from io import BytesIO

        from reportlab.lib.pagesizes import mm
        from reportlab.lib.units import mm as MM
        from reportlab.pdfgen import canvas
        with database.get_db() as s:
            p = s.get(database.PedidoReparto, pid)
            if not p:
                return 'pedido no existe', 404
            cad = _mapa_cadetes(s)
            rutas_cad = {r.id: r.cadete_id for r in s.query(database.RutaReparto)
                         .filter(database.RutaReparto.cadete_id.isnot(None)).all()}
            ef_id = reparto.cadete_efectivo_id(p, rutas_cad)
            cadete_nom = cad.get(ef_id, '') if ef_id else ''
            # total_paciente YA incluye el envío (el operador carga el total con
            # envío). El producto se deriva: producto = total - envío.
            # Si el envío va sin cargo, el operador NO lo suma al total → el
            # producto es igual al total y el cadete no cobra el envío.
            total_monto = float(p.total_paciente) if p.total_paciente is not None else None
            envio_v = float(p.envio_costo) if p.envio_costo is not None else None
            envio_sc = bool(p.envio_sin_cargo)
            producto_monto = (round((total_monto or 0) - (0 if envio_sc else (envio_v or 0)), 2)
                              if total_monto is not None else None)
            cobrar = None if p.pagado else total_monto
            paga_con = float(p.paga_con) if p.paga_con is not None else None
            fecha = p.creado_en.strftime('%d/%m %H:%M') if p.creado_en else ''
            data = dict(
                id=p.id, fecha=fecha, cliente=p.cliente_nombre or '',
                telefono=p.telefono or '', direccion=p.direccion or '',
                localidad=p.localidad or '',
                piso=p.piso or '', depto=p.depto or '', referencia=p.referencia or '',
                observacion=p.observacion or '', producto=p.producto or '',
                receta_pendiente=(p.receta_estado == 'pendiente')
                                  or (p.receta_estado is None and bool(p.requiere_receta)),
                pagado=bool(p.pagado), forma_pago=p.forma_pago or '',
                producto_monto=producto_monto, envio=envio_v, envio_sin_cargo=envio_sc,
                total=total_monto,
                cobrar=cobrar, paga_con=paga_con, vuelto=p.vuelto or '',
                obra_social=p.obra_social or '', cadete=cadete_nom,
            )

        # 80mm wide, alto fluido según contenido. Margen interno 5mm.
        # Más aire que la versión anterior (Diego 2026-06-16): los textos no
        # tocan las líneas separadoras, y un bloque grande de firma al final.
        W = 80 * MM
        margin = 5 * MM
        usable = W - 2 * margin
        # Estimación de líneas para calcular alto del PDF.
        n_lines_est = 24 + len(data['producto']) // 34 + len(data['observacion']) // 34
        H = (35 + n_lines_est * 5.2) * MM
        buf = BytesIO()
        c = canvas.Canvas(buf, pagesize=(W, H))
        y = H - margin
        # Espaciado vertical entre líneas (más generoso).
        def line(txt='', size=8, bold=False, sep=False, spacer=False):
            nonlocal y
            if sep:
                y -= 2.5 * MM
                c.setLineWidth(0.3)
                c.line(margin, y, W - margin, y)
                y -= 3 * MM
                return
            if spacer:
                y -= 2 * MM
                return
            c.setFont('Courier-Bold' if bold else 'Courier', size)
            # Wrap a 34 caracteres (cabe cómodo en 80mm con Courier 8pt).
            txt = txt or ''
            chunks = [txt[i:i+34] for i in range(0, max(1, len(txt)), 34)] if txt else ['']
            for chunk in chunks:
                c.drawString(margin, y, chunk)
                y -= (size * 0.42 + 1.6) * MM
        # Header
        line('FARMACIA BADIA', size=11, bold=True)
        line(f'PEDIDO #{data["id"]}   {data["fecha"]}', size=9, bold=True)
        line(sep=True)
        # Cliente / dirección
        line(f'Cliente: {data["cliente"]}', bold=True)
        if data['telefono']:
            line(f'Tel:     {data["telefono"]}')
        line(f'Direc:   {data["direccion"]}')
        if data['piso'] or data['depto']:
            extras = ' '.join(filter(None, [
                f'Piso {data["piso"]}' if data['piso'] else '',
                f'Dpto {data["depto"]}' if data['depto'] else '',
            ]))
            line(f'         {extras}')
        if data['localidad']:
            line(f'Ciudad:  {data["localidad"]}', bold=True)
        if data['referencia']:
            line(f'Ref:     {data["referencia"]}')
        line(sep=True)
        # Producto
        if data['producto']:
            line('PRODUCTO', bold=True)
            line(spacer=True)
            line(data['producto'])
        if data['observacion']:
            line(spacer=True)
            line(f'OBS: {data["observacion"]}', bold=True)
        if data['receta_pendiente']:
            line(spacer=True)
            line('!! RECETA PENDIENTE !!', bold=True)
        line(sep=True)
        # Pago: SIEMPRE desglosamos Producto + Envío + Total, y al final
        # indicamos si está pagado (cadete solo entrega) o lo cobra el cadete.
        line('IMPORTES', bold=True)
        line(spacer=True)
        prod_v = data['producto_monto'] or 0
        env_v = data['envio'] or 0
        total_v = data['total'] or 0       # total cargado (ya incluye envío)
        if prod_v > 0:
            line(f'  Producto: $ {prod_v:,.0f}'.replace(',', '.'))
        if data['envio_sin_cargo']:
            line('  Envio:    SIN CARGO', bold=True)
        elif env_v > 0:
            line(f'  Envio:    $ {env_v:,.0f}'.replace(',', '.'))
        if total_v > 0:
            line(f'  TOTAL:    $ {total_v:,.0f}'.replace(',', '.'), size=11, bold=True)
        line(spacer=True)
        if data['pagado']:
            line(f'>> PAGADO ({data["forma_pago"] or "—"}) <<', bold=True)
            line('   no cobrar en entrega', size=7)
        else:
            line('>> COBRA CADETE <<', bold=True)
            line(spacer=True)
            line(f'  Forma:    {data["forma_pago"] or "—"}')
            if data['paga_con'] is not None:
                line(f'  Paga con: $ {data["paga_con"]:,.0f}'.replace(',', '.'))
            if data['vuelto']:
                line(f'  Vuelto:   $ {data["vuelto"]}', bold=True)
        line(sep=True)
        # Cobertura / cadete
        if data['obra_social']:
            line(f'OS: {data["obra_social"]}')
        if data['cadete']:
            line(f'Cadete: {data["cadete"]}')
        if data['obra_social'] or data['cadete']:
            line(sep=True)
        # Espacio AMPLIO para firma del cliente (Diego 2026-06-16).
        line('Firma del cliente:', size=8)
        y -= 14 * MM   # espacio en blanco para firmar
        c.setLineWidth(0.4)
        c.line(margin, y, W - margin, y)
        y -= 4 * MM
        line('Aclaración y DNI:', size=7)
        y -= 10 * MM
        c.line(margin, y, W - margin, y)
        c.showPage(); c.save()
        from flask import Response
        return Response(buf.getvalue(), mimetype='application/pdf',
                        headers={'Content-Disposition': f'inline; filename=ticket-pedido-{pid}.pdf'})

    @app.route('/reparto/pedido/<int:pid>/ticket')
    @login_required
    def reparto_ticket_data(pid):
        """Datos del ticket que se lleva el cadete (lo imprime el DockerPanel local
        en la térmica 80mm vía ESC/POS). El browser de la farmacia hace fetch acá y
        postea el JSON a http://localhost:5055/print-ticket. Acá NO se imprime: solo
        se arma el payload con TODO lo que el cadete necesita (incl. vuelto/teléfono,
        que la bandeja de caja oculta)."""
        if not _ok():
            return jsonify({'error': 'sin permiso'}), 403
        with database.get_db() as s:
            p = s.get(database.PedidoReparto, pid)
            if not p:
                return jsonify({'error': 'no existe'}), 404
            cad = _mapa_cadetes(s)
            rutas_cad = {r.id: r.cadete_id for r in s.query(database.RutaReparto)
                         .filter(database.RutaReparto.cadete_id.isnot(None)).all()}
            ef_id = reparto.cadete_efectivo_id(p, rutas_cad)
            # total_paciente YA incluye el envío (el operador carga el total).
            # Producto = total - envío. Si el envío va sin cargo, el operador
            # NO lo sumó al total → producto = total.
            # Si ya está pagado, el cadete no cobra.
            total_monto = float(p.total_paciente) if p.total_paciente is not None else None
            envio = float(p.envio_costo) if p.envio_costo is not None else None
            envio_sc = bool(p.envio_sin_cargo)
            producto_monto = (round((total_monto or 0) - (0 if envio_sc else (envio or 0)), 2)
                              if total_monto is not None else None)
            cobrar = None if p.pagado else total_monto
            return jsonify({
                'id': p.id,
                'fecha': p.creado_en.strftime('%d/%m %H:%M') if p.creado_en else '',
                'farmacia': 'Badia',
                'cliente': p.cliente_nombre or '',
                'telefono': p.telefono or '',
                'direccion': p.direccion or '',
                'piso': p.piso or '', 'depto': p.depto or '', 'referencia': p.referencia or '',
                'observacion': p.observacion or '',
                'producto': p.producto or '',
                'receta_pendiente': (p.receta_estado == 'pendiente')
                                    or (p.receta_estado is None and bool(p.requiere_receta)),
                'pagado': bool(p.pagado),
                'forma_pago': p.forma_pago or '',
                'producto_monto': producto_monto,
                'envio': envio,
                'envio_sin_cargo': envio_sc,
                'total': total_monto,
                'cobrar': cobrar,
                'paga_con': float(p.paga_con) if p.paga_con is not None else None,
                'vuelto': p.vuelto or '',
                'obra_social': p.obra_social or '',
                'cadete': cad.get(ef_id, '') if ef_id else '',
            })

    @app.route('/reparto/ruta/<int:rid>/optimizar', methods=['POST'])
    @login_required
    def reparto_optimizar(rid):
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        fecha = _fecha((request.json or {}).get('fecha'))
        P = database.PedidoReparto
        with database.get_db() as s:
            ps = (s.query(P).filter(P.ruta_id == rid, P.fecha == fecha,
                                    P.estado.in_(['pendiente', 'en_ruta'])).all())
            items = [{'id': p.id, 'lat': p.lat, 'lng': p.lng,
                      'prioridad': p.prioridad} for p in ps]
            orden = reparto.secuenciar(items)
            pos = {it['id']: i for i, it in enumerate(orden, start=1)}
            for p in ps:
                p.orden_en_ruta = pos.get(p.id, 0)
            s.commit()
        return jsonify({'ok': True})

    @app.route('/reparto/ruta/<int:rid>/export')
    @login_required
    def reparto_export(rid):
        if not _ok():
            return jsonify({'error': 'sin permiso'}), 403
        fecha = _fecha(request.args.get('fecha'))
        P = database.PedidoReparto
        with database.get_db() as s:
            cad = _mapa_cadetes(s)
            r = s.get(database.RutaReparto, rid)
            rutas_cad = {r.id: r.cadete_id} if (r and r.cadete_id) else {}
            ps = (s.query(P).filter(P.ruta_id == rid, P.fecha == fecha,
                                    P.estado.in_(['pendiente', 'en_ruta']))
                  .order_by(P.orden_en_ruta, P.id).all())
            paradas = [(p.lat, p.lng) for p in ps]
            return jsonify({'ruta': _ruta_dict(r, cad) if r else None,
                            'pedidos': [_pedido_dict(p, cad, rutas_cad) for p in ps],
                            'link': reparto.link_google_maps(paradas)})

    # ── Vista móvil del cadete (sin login, autorización por token) ───────────

    @app.route('/reparto/cadete/<token>')
    def cadete_vista(token):
        """Template mobile para el repartidor."""
        with database.get_db() as s:
            c = s.query(database.Cadete).filter(
                database.Cadete.token == token).first()
            if not c:
                return 'Cadete no encontrado', 404
            return render_template('vista_cadete.html',
                                   cadete={'id': c.id, 'nombre': c.nombre},
                                   token=token)

    @app.route('/reparto/cadete/<token>/api')
    def cadete_api(token):
        """JSON con pedidos del día para este cadete."""
        with database.get_db() as s:
            c = s.query(database.Cadete).filter(
                database.Cadete.token == token).first()
            if not c:
                return jsonify({'error': 'cadete no encontrado'}), 404
            # Generar token on-demand para cadetes viejos
            if not c.token:
                c.token = uuid.uuid4().hex[:12]
                s.commit()
            fecha = database.now_ar().date()
            P = database.PedidoReparto
            ps = (s.query(P).filter(
                P.cadete_id == c.id, P.fecha == fecha,
                P.estado.in_(['pendiente', 'en_ruta', 'entregado'])
            ).order_by(P.orden_en_ruta, P.id).all())
            paradas = [(p.lat, p.lng) for p in ps if p.lat and p.lng]
            return jsonify({
                'cadete': {'id': c.id, 'nombre': c.nombre},
                'fecha': fecha.strftime('%Y-%m-%d'),
                'pedidos': [_pedido_dict(p) for p in ps],
                'link_maps': reparto.link_google_maps(paradas),
            })

    @app.route('/reparto/cadete/<token>/pedido/<int:pid>/entregar',
               methods=['POST'])
    def cadete_entregar(token, pid):
        """Marcar pedido como entregado."""
        with database.get_db() as s:
            c = s.query(database.Cadete).filter(
                database.Cadete.token == token).first()
            if not c:
                return jsonify({'ok': False, 'error': 'cadete no encontrado'}), 404
            p = s.get(database.PedidoReparto, pid)
            if not p or p.cadete_id != c.id:
                return jsonify({'ok': False, 'error': 'no autorizado'}), 403
            p.estado = 'entregado'
            _notificar_cliente_pedido(s, p, 'entregado')
            s.commit()
            return jsonify({'ok': True})

    @app.route('/reparto/cadete/<token>/pedido/<int:pid>/cobrar',
               methods=['POST'])
    def cadete_cobrar(token, pid):
        """Marcar pedido como cobrado."""
        with database.get_db() as s:
            c = s.query(database.Cadete).filter(
                database.Cadete.token == token).first()
            if not c:
                return jsonify({'ok': False, 'error': 'cadete no encontrado'}), 404
            p = s.get(database.PedidoReparto, pid)
            if not p or p.cadete_id != c.id:
                return jsonify({'ok': False, 'error': 'no autorizado'}), 403
            p.pagado = True
            s.commit()
            return jsonify({'ok': True})

    # ── Planilla de monitoreo ─────────────────────────────────────────────

    @app.route('/reparto/planilla')
    @login_required
    def reparto_planilla():
        if not _ok():
            return 'Sin permiso', 403
        fecha = _fecha(request.args.get('fecha'))
        with database.get_db() as s:
            ps = (s.query(database.PedidoReparto)
                  .filter(database.PedidoReparto.fecha == fecha)
                  .order_by(database.PedidoReparto.turno,
                            database.PedidoReparto.id).all())
            cadetes = {c.id: c.nombre for c in
                       s.query(database.Cadete).all()}
            # Para mostrar el nombre (abreviado) en la barra de estado cuando el
            # pedido está 'esperando_drog'.
            droguerias = {p.id: p.razon_social
                          for p in s.query(database.Provider).all()}
            usuarios = s.query(database.Usuario).filter(
                database.Usuario.activo.is_(True)).order_by(
                database.Usuario.nombre_completo).all()
            usuarios_list = [{'id': u.id,
                              'nombre': u.nombre_completo or u.username}
                             for u in usuarios]
        sin_asignar = [p for p in ps if not p.turno]
        manana = [p for p in ps if p.turno == 'mañana']
        tarde = [p for p in ps if p.turno == 'tarde']
        # Pendientes del turno previo: pedidos de FECHAS ANTERIORES que quedaron
        # sin entregar (cadete no volvió, se reprogramó, etc.). Los traemos solo
        # cuando se mira la planilla de HOY (no para fechas pasadas, ahí ya están).
        pendientes_previo = []
        hoy_d = database.now_ar().date()
        if fecha >= hoy_d:
            estados_activos = ('pendiente', 'publicado', 'tomado', 'en_ruta',
                                'en_caja', 'en_planilla', 'esperando_drog')
            pendientes_previo = (s.query(database.PedidoReparto)
                                  .filter(database.PedidoReparto.fecha < hoy_d,
                                          database.PedidoReparto.estado.in_(estados_activos))
                                  .order_by(database.PedidoReparto.fecha.desc(),
                                            database.PedidoReparto.id).all())
        # Detectar zona externa por pedido (Funes/Roldán/Kentucky/Haras/...).
        # 1) Por coords si caen en polígono. 2) Fallback: nombre de zona en la
        # dirección. Si nada matchea → None (asumimos Rosario).
        import json as _json
        zonas_activas = s.query(database.EnvioZona).filter(
            database.EnvioZona.activa.is_(True)).order_by(database.EnvioZona.orden).all()
        zonas_poly = [(z.nombre, _json.loads(z.poligono)) for z in zonas_activas
                       if z.poligono]
        nombres_zona = [z.nombre for z in zonas_activas]

        def _zona_externa(p):
            if p.lat is not None and p.lng is not None:
                for nombre, poly in zonas_poly:
                    if reparto._punto_en_poligono(p.lat, p.lng, poly):
                        return nombre
            if p.direccion:
                d_low = p.direccion.lower()
                for nombre in nombres_zona:
                    if nombre.lower() in d_low:
                        return nombre
            return None

        for grupo in (sin_asignar, manana, tarde, pendientes_previo):
            for p in grupo:
                p.zona_externa = _zona_externa(p)
        # Config SLA del cron de reparto (configurable en /config/envio).
        from bot import envio as _envio_mod
        cfg = _envio_mod.get_config()
        sla = {
            'reaviso_min': cfg['sla_publicacion_reaviso_min'],
            'maximo_min':  cfg['sla_publicacion_maximo_min'],
            'retiro_max':  cfg['sla_retiro_maximo_min'],
            'urg_factor':  cfg['sla_factor_urgente'],
        }
        return render_template('reparto_planilla.html',
                               fecha=fecha, hoy=database.now_ar().date(),
                               ahora=database.now_ar(),     # para timers visuales (publicado→tomado)
                               pedidos_pendientes_previo=pendientes_previo,
                               pedidos_sin_asignar=sin_asignar,
                               pedidos_manana=manana,
                               pedidos_tarde=tarde,
                               cadetes=cadetes,
                               droguerias=droguerias,
                               usuarios=usuarios_list,
                               sla=sla)

    @app.route('/api/reparto/pedido/<int:pid>/actualizar', methods=['POST'])
    @login_required
    def reparto_actualizar_pedido(pid):
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        b = request.json or {}
        campo = (b.get('campo') or '').strip()
        valor = b.get('valor')
        EDITABLES = {'tomo', 'importe', 'forma_pago', 'vuelto', 'producto',
                     'observacion', 'pagado', 'requiere_receta',
                     'entregado_por', 'cadete_id', 'recibio', 'estado', 'turno',
                     'etiqueta', 'etiqueta_color',
                     'envio_costo', 'envio_sin_cargo'}
        if campo not in EDITABLES:
            return jsonify({'ok': False, 'error': f'campo no editable: {campo}'}), 400
        with database.get_db() as s:
            p = s.get(database.PedidoReparto, pid)
            if not p:
                return jsonify({'ok': False, 'error': 'no existe'}), 404
            # Tipos especiales
            if campo in ('pagado', 'requiere_receta', 'envio_sin_cargo'):
                valor = bool(valor)
            elif campo in ('importe', 'envio_costo') and valor is not None and valor != '':
                try:
                    valor = float(str(valor).replace(',', '.'))
                except (TypeError, ValueError):
                    return jsonify({'ok': False, 'error': f'{campo} inválido'}), 400
            elif campo == 'cadete_id':
                try:
                    valor = int(valor) if valor not in (None, '') else None
                except (TypeError, ValueError):
                    return jsonify({'ok': False, 'error': 'cadete_id inválido'}), 400
            else:
                valor = (str(valor).strip() if valor else None)
            setattr(p, campo, valor)
            # Avisar al cliente cuando el estado avanza a en_ruta o entregado.
            if campo == 'estado':
                _notificar_cliente_pedido(s, p, valor)
            s.commit()
            return jsonify({'ok': True})
