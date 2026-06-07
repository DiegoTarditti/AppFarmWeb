"""Rutas de reparto (v1): definir rutas (cuadrantes N/S/E/O) + armar el reparto
del día (cargar pedidos, auto-asignar por cuadrante, reasignar a mano, exportar).

Carga manual: el operador agrega cada pedido (cliente + domicilio/dirección + nota).
El motor de asignación vive en services/reparto.py.
"""
from datetime import datetime

from flask import jsonify, render_template, request
from flask_login import current_user, login_required

import database
from bot import store
from services import reparto

_ROLES_OK = ('admin', 'dev', 'farmacia')


def _ok():
    return getattr(current_user, 'rol', None) in _ROLES_OK


def _fecha(arg):
    try:
        return datetime.strptime((arg or '')[:10], '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return database.now_ar().date()


def _ruta_dict(r):
    return {'id': r.id, 'nombre': r.nombre, 'cuadrante': r.cuadrante,
            'color': r.color or '#1D9E75', 'cadete': r.cadete or '', 'activa': r.activa}


def _pedido_dict(p):
    return {'id': p.id, 'cliente_nombre': p.cliente_nombre or 's/cliente',
            'direccion': p.direccion or '', 'nota': p.nota or '',
            'cuadrante': p.cuadrante, 'ruta_id': p.ruta_id, 'estado': p.estado,
            'orden': p.orden_en_ruta or 0, 'lat': p.lat, 'lng': p.lng}


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
            rs = (s.query(database.RutaReparto)
                  .order_by(database.RutaReparto.orden, database.RutaReparto.id).all())
            return jsonify({'rutas': [_ruta_dict(r) for r in rs]})

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
            r.cadete = (b.get('cadete') or '').strip() or None
            if 'activa' in b:
                r.activa = bool(b['activa'])
            s.commit()
            return jsonify({'ok': True, 'id': r.id})

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

    # ── Armar reparto del día ────────────────────────────────────────────────

    @app.route('/reparto')
    @login_required
    def reparto_panel():
        if not _ok():
            return 'Sin permiso', 403
        return render_template('reparto.html')

    @app.route('/reparto/api')
    @login_required
    def reparto_api():
        if not _ok():
            return jsonify({'error': 'sin permiso'}), 403
        reparto.seed_rutas_si_vacio()
        fecha = _fecha(request.args.get('fecha'))
        P = database.PedidoReparto
        with database.get_db() as s:
            rs = (s.query(database.RutaReparto)
                  .order_by(database.RutaReparto.orden, database.RutaReparto.id).all())
            ps = (s.query(P).filter(P.fecha == fecha, P.estado != 'anulado')
                  .order_by(P.orden_en_ruta, P.id).all())
            cfg = reparto.envio.get_config()
            return jsonify({'fecha': fecha.strftime('%Y-%m-%d'),
                            'farmacia': {'lat': cfg['farmacia_lat'], 'lng': cfg['farmacia_lng']},
                            'rutas': [_ruta_dict(r) for r in rs],
                            'pedidos': [_pedido_dict(p) for p in ps]})

    @app.route('/reparto/api/buscar-cliente')
    @login_required
    def reparto_buscar_cliente():
        if not _ok():
            return jsonify({'error': 'sin permiso'}), 403
        return jsonify({'clientes': store.buscar_clientes(request.args.get('q', ''))})

    @app.route('/reparto/api/<int:oid>/domicilios')
    @login_required
    def reparto_domicilios(oid):
        if not _ok():
            return jsonify({'error': 'sin permiso'}), 403
        return jsonify({'domicilios': store.listar_domicilios_de_cliente(observer_id=oid)})

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
        coords = reparto.coords_de_pedido(domicilio_id, direccion)
        lat, lng = coords if coords else (None, None)
        cuad = reparto.cuadrante_de(lat, lng)
        with database.get_db() as s:
            ruta = reparto.ruta_para_cuadrante(s, cuad)
            p = database.PedidoReparto(
                fecha=database.now_ar().date(),
                cliente_observer_id=b.get('observer_id'),
                cliente_nombre=(b.get('cliente_nombre') or '').strip() or None,
                direccion=direccion or None, lat=lat, lng=lng,
                nota=(b.get('nota') or '').strip() or None,
                cuadrante=cuad, ruta_id=(ruta.id if ruta else None),
                estado='pendiente')
            s.add(p)
            s.commit()
            return jsonify({'ok': True, 'id': p.id, 'cuadrante': cuad,
                            'asignado': bool(ruta)})

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
            items = [{'id': p.id, 'lat': p.lat, 'lng': p.lng} for p in ps]
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
            r = s.get(database.RutaReparto, rid)
            ps = (s.query(P).filter(P.ruta_id == rid, P.fecha == fecha,
                                    P.estado.in_(['pendiente', 'en_ruta']))
                  .order_by(P.orden_en_ruta, P.id).all())
            paradas = [(p.lat, p.lng) for p in ps]
            return jsonify({'ruta': _ruta_dict(r) if r else None,
                            'pedidos': [_pedido_dict(p) for p in ps],
                            'link': reparto.link_google_maps(paradas)})
