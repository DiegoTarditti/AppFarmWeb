"""Caja: pantalla del cajero. Ve los tickets confirmados por los operadores,
los cobra (registrando el medio de pago) y marca entregado.

NO procesa pagos online (Meta lo prohíbe para farmacia): el cobro es presencial,
acá solo se registra. Rol 'cajero' acotado a /caja/* (ver auth_routes).
"""
from flask import jsonify, render_template, request
from flask_login import current_user, login_required

from bot import caja


def init_app(app):

    @app.route('/caja')
    @login_required
    def caja_panel():
        return render_template('caja.html')

    @app.route('/caja/api/tickets')
    @login_required
    def caja_tickets():
        cerrados = request.args.get('cerrados') == '1'
        return jsonify({'tickets': caja.listar_tickets(incluir_cerrados=cerrados)})

    @app.route('/caja/api/formas-pago')
    @login_required
    def caja_formas_pago():
        return jsonify({'formas': caja.listar_formas_pago()})

    @app.route('/caja/<int:ticket_id>/cobrar', methods=['POST'])
    @login_required
    def caja_cobrar(ticket_id):
        forma = (request.json or {}).get('forma_pago', '')
        return jsonify(caja.cobrar_ticket(ticket_id, forma, current_user.id))

    @app.route('/caja/<int:ticket_id>/entregar', methods=['POST'])
    @login_required
    def caja_entregar(ticket_id):
        return jsonify(caja.entregar_ticket(ticket_id))

    @app.route('/caja/<int:ticket_id>/anular', methods=['POST'])
    @login_required
    def caja_anular(ticket_id):
        return jsonify(caja.anular_ticket(ticket_id))

    # ── Catálogo de formas de pago (admin) ──────────────────────────────────

    @app.route('/caja/formas-pago', methods=['POST'])
    @login_required
    def caja_forma_crear():
        if current_user.rol not in ('admin', 'dev'):
            return jsonify({'ok': False, 'error': 'solo admin'}), 403
        return jsonify(caja.crear_forma_pago((request.json or {}).get('nombre')))

    @app.route('/caja/formas-pago/<int:forma_id>/delete', methods=['POST'])
    @login_required
    def caja_forma_eliminar(forma_id):
        if current_user.rol not in ('admin', 'dev'):
            return jsonify({'ok': False, 'error': 'solo admin'}), 403
        return jsonify(caja.eliminar_forma_pago(forma_id))
