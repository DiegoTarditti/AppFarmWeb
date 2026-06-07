"""Rutas de reparto (v1): definir rutas (cuadrantes N/S/E/O) + armar el reparto
del día (cargar pedidos, auto-asignar por cuadrante, reasignar a mano, exportar).

Carga manual: el operador agrega cada pedido (cliente + domicilio/dirección + nota).
El motor de asignación vive en services/reparto.py.
"""
import json
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
            'activo': c.activo}


def _mapa_cadetes(s):
    """{id: nombre} de todos los cadetes (para resolver el nombre en las rutas)."""
    return {c.id: c.nombre for c in s.query(database.Cadete).all()}


def _pedido_dict(p, cadetes=None):
    """Serializa un PedidoReparto + resuelve nombre del cadete."""
    nombre_cad = ''
    if p.cadete_id and cadetes is not None:
        nombre_cad = cadetes.get(p.cadete_id, '')
    elif not p.cadete_id and p.ruta_id and cadetes is not None:
        # heredar de la ruta si el pedido no tiene override
        pass  # la ruta se resuelve en el frontend con window._rutas
    return {
        'id': p.id, 'cliente_nombre': p.cliente_nombre or 's/cliente',
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
        'entregado_por': p.entregado_por or '',
        'recibio': p.recibio or '',
        'observacion': p.observacion or '',
        'producto': p.producto or '',
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
                c = database.Cadete(nombre=nombre)
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
            s.commit()
            return jsonify({'ok': True, 'id': c.id})

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
            cad = _mapa_cadetes(s)
            rs = (s.query(database.RutaReparto)
                  .order_by(database.RutaReparto.orden, database.RutaReparto.id).all())
            ps = (s.query(P).filter(P.fecha == fecha, P.estado != 'anulado')
                  .order_by(P.orden_en_ruta, P.id).all())
            cfg = reparto.envio.get_config()
            cs = (s.query(database.Cadete)
                  .filter(database.Cadete.activo.is_(True))
                  .order_by(database.Cadete.nombre).all())
            return jsonify({'fecha': fecha.strftime('%Y-%m-%d'),
                            'farmacia': {'lat': cfg['farmacia_lat'], 'lng': cfg['farmacia_lng']},
                            'ciudades': reparto.envio.listar_ciudades(),
                            'rutas': [_ruta_dict(r, cad) for r in rs],
                            'pedidos': [_pedido_dict(p, cad) for p in ps],
                            'cadetes': [_cadete_dict(c) for c in cs]})

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
        with database.get_db() as s:
            ruta = reparto.ruta_para_punto(s, lat, lng)   # zona (polígono) → sino cuadrante
            p = database.PedidoReparto(
                fecha=database.now_ar().date(),
                cliente_observer_id=b.get('observer_id'),
                cliente_nombre=(b.get('cliente_nombre') or '').strip() or None,
                direccion=direccion or None, lat=lat, lng=lng,
                nota=(b.get('nota') or '').strip() or None,
                cuadrante=cuad, ruta_id=(ruta.id if ruta else None),
                prioridad=(b.get('prioridad') if b.get('prioridad') in
                           ('urgente', 'normal', 'programado') else 'normal'),
                estado='pendiente',
                # Campos nuevos
                tomo=(b.get('tomo') or '').strip() or None,
                canal=(b.get('canal') or 'manual').strip(),
                importe=importe,
                forma_pago=(b.get('forma_pago') or '').strip() or None,
                vuelto=(b.get('vuelto') or '').strip() or None,
                requiere_receta=bool(b.get('requiere_receta')),
                pagado=bool(b.get('pagado')),
                turno=(b.get('turno') or '').strip() or None,
                cadete_id=b.get('cadete_id') or None,
                entregado_por=(b.get('entregado_por') or '').strip() or None,
                recibio=(b.get('recibio') or '').strip() or None,
                observacion=(b.get('observacion') or '').strip() or None,
                producto=(b.get('producto') or '').strip() or None,
            )
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
            ps = (s.query(P).filter(P.ruta_id == rid, P.fecha == fecha,
                                    P.estado.in_(['pendiente', 'en_ruta']))
                  .order_by(P.orden_en_ruta, P.id).all())
            paradas = [(p.lat, p.lng) for p in ps]
            return jsonify({'ruta': _ruta_dict(r, cad) if r else None,
                            'pedidos': [_pedido_dict(p) for p in ps],
                            'link': reparto.link_google_maps(paradas)})
