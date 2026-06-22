"""Panel de tarifas de envío (Fase 1): cotizador para el operador + config de la
grilla (tramos por cuadras + zonas con tarifa fija). Solo lectura/edición; el
cálculo automático desde la ubicación (pin) es Fase 2.

Rutas:
  GET  /config/envio                  → panel (cotizador + config)
  GET  /config/envio/api/tarifas      → JSON {tramos, zonas}
  GET  /config/envio/api/cotizar      → JSON cotización (localidad / cuadras)
  POST /config/envio/save             → guardar config de farmacia
  POST /config/envio/tramo            → crear/editar tramo
  POST /config/envio/tramo/<id>/delete
  POST /config/envio/zona             → crear/editar zona
  POST /config/envio/zona/<id>/delete
"""
from flask import jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from auth import tiene_perfil
from bot import envio

_ROLES_OK = ('admin', 'dev', 'farmacia')
# Perfiles que usan /config/envio (sobre todo el cotizador del cliente_picker).
# Las POST de edición de tarifas no se exponen en el sidebar a estos perfiles.
_PERFILES_OK = ('pedido_manual', 'chat_clientes', 'planilla_envios')


def _ok():
    if getattr(current_user, 'rol', None) in _ROLES_OK:
        return True
    return any(tiene_perfil(current_user, p) for p in _PERFILES_OK)


def init_app(app):

    @app.route('/config/envio')
    @login_required
    def envio_panel():
        if not _ok():
            return 'Sin permiso', 403
        return render_template('envio.html')

    @app.route('/config/envio/api/tarifas')
    @login_required
    def envio_tarifas():
        if not _ok():
            return jsonify({'error': 'sin permiso'}), 403
        return jsonify(envio.listar_tarifas())

    @app.route('/config/envio/api/cotizar')
    @login_required
    def envio_cotizar():
        if not _ok():
            return jsonify({'error': 'sin permiso'}), 403
        lat, lng = request.args.get('lat'), request.args.get('lng')
        direccion = request.args.get('direccion')
        if lat and lng:                                  # pin / coordenadas
            return jsonify(envio.cotizar_por_coords(
                lat, lng, localidad_hint=request.args.get('localidad')))
        if direccion:                                    # dirección escrita (geocoder)
            return jsonify(envio.cotizar_por_direccion(
                direccion, localidad=request.args.get('localidad')))
        return jsonify(envio.cotizar(localidad=request.args.get('localidad'),
                                     cuadras=request.args.get('cuadras')))

    @app.route('/config/envio/save', methods=['POST'])
    @login_required
    def envio_config_guardar():
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        b = request.json or {}
        return jsonify(envio.guardar_config(
            farmacia_lat=b.get('farmacia_lat'), farmacia_lng=b.get('farmacia_lng'),
            factor_cuadras=b.get('factor_cuadras'),
            metros_por_cuadra=b.get('metros_por_cuadra'),
            alias_transferencia=b.get('alias_transferencia'),
            sla_publicacion_reaviso_min=b.get('sla_publicacion_reaviso_min'),
            sla_publicacion_maximo_min=b.get('sla_publicacion_maximo_min'),
            sla_retiro_maximo_min=b.get('sla_retiro_maximo_min'),
            sla_factor_urgente=b.get('sla_factor_urgente'),
            sla_respuesta_cadete_aviso_min=b.get('sla_respuesta_cadete_aviso_min'),
            sla_respuesta_cadete_modal_min=b.get('sla_respuesta_cadete_modal_min')))

    @app.route('/config/envio/geolocalizar', methods=['POST'])
    @login_required
    def envio_config_geo():
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        b = request.json or {}
        return jsonify(envio.geolocalizar_farmacia(
            b.get('direccion', ''), localidad=b.get('localidad') or 'Rosario'))

    @app.route('/config/envio/zona/<int:zid>/geolocalizar', methods=['POST'])
    @login_required
    def envio_zona_geo(zid):
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        return jsonify(envio.geolocalizar_zona(zid))

    @app.route('/config/envio/tramo', methods=['POST'])
    @login_required
    def envio_tramo_guardar():
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        b = request.json or {}
        return jsonify(envio.guardar_tramo(b.get('id'), b.get('hasta_cuadras'), b.get('monto')))

    @app.route('/config/envio/tramo/<int:tid>/delete', methods=['POST'])
    @login_required
    def envio_tramo_eliminar(tid):
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        return jsonify(envio.eliminar_tramo(tid))

    @app.route('/config/envio/zona', methods=['POST'])
    @login_required
    def envio_zona_guardar():
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        b = request.json or {}
        return jsonify(envio.guardar_zona(
            b.get('id'), b.get('nombre'), b.get('monto'),
            lat=b.get('lat'), lng=b.get('lng'), radio_km=b.get('radio_km'),
            poligono_texto=b.get('poligono_texto'),
            activa=b.get('activa')))

    @app.route('/config/envio/zona/<int:zid>/delete', methods=['POST'])
    @login_required
    def envio_zona_eliminar(zid):
        if not _ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        return jsonify(envio.eliminar_zona(zid))

    # ── Legacy redirects 301 ──────────────────────────────────────────────
    @app.route('/envio')
    @login_required
    def envio_legacy_panel_redirect():
        return redirect('/config/envio', code=301)
