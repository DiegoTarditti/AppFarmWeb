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
        if not cadete and wa_id_emisor and push_name:
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
            # DM con un cadete particular.
            if not wa_id_emisor:
                return None
            conv = (s.query(database.BotConversacion)
                    .filter_by(canal='whatsapp', canal_user_id=wa_id_emisor).first())
            if not conv:
                conv = database.BotConversacion(
                    canal='whatsapp', canal_user_id=wa_id_emisor,
                    nombre_cliente=(cadete.nombre if cadete else push_name) or 'Cadete',
                    estado_atencion='humano', nodo='reparto',
                    cadete_id=(cadete.id if cadete else None))
                s.add(conv); s.flush()
            elif cadete and not conv.cadete_id:
                conv.cadete_id = cadete.id
        # Insertar el mensaje. fromMe ⇒ lo mandó el operador; else lo mandó un cadete.
        # En el grupo, fromMe también puede ser el bot publicando un pedido — lo
        # marcamos como 'operador' igualmente porque sale desde la farmacia.
        origen = 'operador' if from_me else 'cliente'
        s.add(database.BotMensaje(conversacion_id=conv.id, origen=origen, texto=body))
        conv.ultimo_en = database.now_ar()
        s.commit()
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
            'activo': c.activo, 'token': c.token or ''}


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

        Sin login: WAHA hace POST desde la red docker."""
        from bot import whatsapp_grupo
        payload = request.json or {}
        # Log temporal para diagnóstico
        import json as _json
        print('[WHATSAPP-WEBHOOK]', _json.dumps(payload, ensure_ascii=False)[:1500], flush=True)
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
        es_grupo = chat_id.endswith('@g.us') or chat_id == whatsapp_grupo.WAHA_GRUPO_ENVIOS
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

    # ── Chat de reparto (panel en /reparto/planilla) ────────────────────────
    # 5 endpoints que sirven la lista de conversaciones (grupo + DMs) y permiten
    # mandar mensajes desde el panel sin salir de la planilla.

    def _conv_grupo_or_none(s):
        from bot import whatsapp_grupo as _wg
        if not _wg.WAHA_GRUPO_ENVIOS:
            return None
        return (s.query(database.BotConversacion)
                .filter_by(canal='whatsapp_grupo', canal_user_id=_wg.WAHA_GRUPO_ENVIOS)
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
            # DMs: conversaciones con cadete_id NO NULL (los autovinculados o
            # vinculados a mano). Limitamos a últimas 50 para que el polling
            # no se infle si hay muchos cadetes inactivos.
            convs = (s.query(database.BotConversacion)
                     .filter(database.BotConversacion.cadete_id.isnot(None))
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
                    'nombre': cad.nombre if cad else (c.nombre_cliente or 'Cadete'),
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

    @app.route('/api/reparto/chat/cadete/<int:cadete_id>/mensajes')
    @login_required
    def api_reparto_chat_cadete_mensajes(cadete_id):
        """DM con un cadete puntual. Igual semántica que el de grupo."""
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        try:
            desde_id = int(request.args.get('desde_id') or 0)
        except (TypeError, ValueError):
            desde_id = 0
        with database.get_db() as s:
            conv = (s.query(database.BotConversacion)
                    .filter_by(cadete_id=cadete_id, canal='whatsapp')
                    .order_by(database.BotConversacion.ultimo_en.desc())
                    .first())
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
        from bot import whatsapp_grupo
        r = whatsapp_grupo.publicar_en_grupo(texto)
        if not r.get('ok'):
            return jsonify({'ok': False, 'error': r.get('error') or 'fallo WAHA'}), 502
        # Persistir el mensaje saliente.
        with database.get_db() as s:
            grupo = _conv_grupo_or_none(s)
            if not grupo:
                # Si el grupo aún no tiene conv (1er mensaje saliente nunca), crearla.
                grupo = database.BotConversacion(
                    canal='whatsapp_grupo',
                    canal_user_id=whatsapp_grupo.WAHA_GRUPO_ENVIOS,
                    nombre_cliente='Grupo de cadetes',
                    estado_atencion='humano', nodo='reparto')
                s.add(grupo); s.flush()
            m = database.BotMensaje(conversacion_id=grupo.id, origen='operador', texto=texto)
            s.add(m)
            grupo.ultimo_en = database.now_ar()
            grupo.operador_user_id = current_user.id
            s.commit()
            return jsonify({'ok': True, 'mensaje': _msg_to_dict(m)})

    @app.route('/api/reparto/chat/cadete/<int:cadete_id>/responder', methods=['POST'])
    @login_required
    def api_reparto_chat_cadete_responder(cadete_id):
        """El operador escribe a un cadete (DM). Manda por WAHA al wa_id del
        cadete y persiste."""
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        texto = ((request.json or {}).get('texto') or '').strip()
        if not texto:
            return jsonify({'ok': False, 'error': 'texto vacío'}), 400
        from bot import whatsapp_grupo
        with database.get_db() as s:
            cadete = s.get(database.Cadete, cadete_id)
            if not cadete:
                return jsonify({'ok': False, 'error': 'cadete no existe'}), 404
            if not cadete.wa_id:
                return jsonify({'ok': False, 'error': 'cadete sin wa_id (todavía no escribió o no se vinculó)'}), 400
            r = whatsapp_grupo.enviar_dm(cadete.wa_id, texto)
            if not r.get('ok'):
                return jsonify({'ok': False, 'error': r.get('error') or 'fallo WAHA'}), 502
            conv = (s.query(database.BotConversacion)
                    .filter_by(cadete_id=cadete_id, canal='whatsapp')
                    .order_by(database.BotConversacion.ultimo_en.desc())
                    .first())
            if not conv:
                conv = database.BotConversacion(
                    canal='whatsapp', canal_user_id=cadete.wa_id,
                    nombre_cliente=cadete.nombre,
                    estado_atencion='humano', nodo='reparto',
                    cadete_id=cadete.id)
                s.add(conv); s.flush()
            m = database.BotMensaje(conversacion_id=conv.id, origen='operador', texto=texto)
            s.add(m)
            conv.ultimo_en = database.now_ar()
            conv.operador_user_id = current_user.id
            s.commit()
            return jsonify({'ok': True, 'mensaje': _msg_to_dict(m)})

    @app.route('/reparto/pedido/<int:pid>/publicar', methods=['POST'])
    @login_required
    def reparto_pedido_publicar(pid):
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        from bot import whatsapp_grupo
        with database.get_db() as s:
            p = s.get(database.PedidoReparto, pid)
            if not p:
                return jsonify({'ok': False, 'error': 'no existe'}), 404
            # Armar texto del mensaje. ⚠️ PRIVACIDAD: el grupo de cadetes solo
            # necesita ubicación para decidir si lo toma. NO mandar nombre, teléfono,
            # producto, total, forma de pago, vuelto, observación ni receta — todo
            # ese detalle se le pasa al cadete por chat 1:1 cuando lo tome.
            partes = [f'🚚 *Pedido #{p.id}*']
            if p.direccion:
                partes.append(f'📍 {p.direccion}')
            if p.lat is not None and p.lng is not None:
                partes.append(f'🗺️ https://www.google.com/maps?q={p.lat},{p.lng}')
            meta = []
            if p.turno:
                meta.append({'mañana': '🌅 Mañana', 'tarde': '🌆 Tarde'}.get(p.turno, p.turno))
            if p.prioridad == 'urgente':
                meta.append('🚨 URGENTE')
            if meta:
                partes.append(' · '.join(meta))
            partes.append('')
            partes.append('Responder *tomo* o *yo* para tomarlo.')
            texto = '\n'.join(partes)
            r = whatsapp_grupo.publicar_en_grupo(texto)
            if not r.get('ok'):
                return jsonify({'ok': False, 'error': r.get('error') or 'sin respuesta WAHA'}), 502
            p.waha_msg_id = r.get('waha_msg_id')
            p.publicado_en = database.now_ar()
            # Persistir el publish en la conv del grupo para que aparezca en el
            # panel de /reparto/planilla (sino la timeline del grupo solo
            # tendría lo que llega del webhook).
            grupo = (s.query(database.BotConversacion)
                     .filter_by(canal='whatsapp_grupo',
                                canal_user_id=whatsapp_grupo.WAHA_GRUPO_ENVIOS)
                     .first()) if whatsapp_grupo.WAHA_GRUPO_ENVIOS else None
            if not grupo and whatsapp_grupo.WAHA_GRUPO_ENVIOS:
                grupo = database.BotConversacion(
                    canal='whatsapp_grupo',
                    canal_user_id=whatsapp_grupo.WAHA_GRUPO_ENVIOS,
                    nombre_cliente='Grupo de cadetes',
                    estado_atencion='humano', nodo='reparto')
                s.add(grupo); s.flush()
            if grupo:
                s.add(database.BotMensaje(conversacion_id=grupo.id,
                                          origen='operador', texto=texto))
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
            if p:
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

    @app.route('/reparto/pedido/<int:pid>/delete', methods=['POST'])
    @login_required
    def reparto_eliminar(pid):
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        with database.get_db() as s:
            p = s.get(database.PedidoReparto, pid)
            if p:
                s.delete(p)
                s.commit()
        return jsonify({'ok': True})

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
            producto_monto = float(p.total_paciente) if p.total_paciente is not None else None
            envio = float(p.envio_costo) if p.envio_costo is not None else None
            # Cobrar = producto + envío (mismo criterio que el vuelto de atención).
            # Si ya está pagado, el cadete no cobra nada.
            cobrar = None if p.pagado else round((producto_monto or 0) + (envio or 0), 2)
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
        return render_template('reparto_planilla.html',
                               fecha=fecha, hoy=database.now_ar().date(),
                               ahora=database.now_ar(),     # para timers visuales (publicado→tomado)
                               pedidos_pendientes_previo=pendientes_previo,
                               pedidos_sin_asignar=sin_asignar,
                               pedidos_manana=manana,
                               pedidos_tarde=tarde,
                               cadetes=cadetes,
                               usuarios=usuarios_list)

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
                     'entregado_por', 'cadete_id', 'recibio', 'estado', 'turno'}
        if campo not in EDITABLES:
            return jsonify({'ok': False, 'error': f'campo no editable: {campo}'}), 400
        with database.get_db() as s:
            p = s.get(database.PedidoReparto, pid)
            if not p:
                return jsonify({'ok': False, 'error': 'no existe'}), 404
            # Tipos especiales
            if campo in ('pagado', 'requiere_receta'):
                valor = bool(valor)
            elif campo == 'importe' and valor is not None and valor != '':
                try:
                    valor = float(str(valor).replace(',', '.'))
                except (TypeError, ValueError):
                    return jsonify({'ok': False, 'error': 'importe inválido'}), 400
            elif campo == 'cadete_id':
                try:
                    valor = int(valor) if valor not in (None, '') else None
                except (TypeError, ValueError):
                    return jsonify({'ok': False, 'error': 'cadete_id inválido'}), 400
            else:
                valor = (str(valor).strip() if valor else None)
            setattr(p, campo, valor)
            s.commit()
            return jsonify({'ok': True})
