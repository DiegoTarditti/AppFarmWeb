"""Caja: pantalla del cajero. Ve los tickets confirmados por los operadores,
los cobra (registrando el medio de pago) y marca entregado.

NO procesa pagos online (Meta lo prohíbe para farmacia): el cobro es presencial,
acá solo se registra. Rol 'cajero' acotado a /caja/* (ver auth_routes).
"""
import csv
import io
from datetime import date, datetime, timedelta

from flask import Response, jsonify, render_template, request
from flask_login import current_user, login_required

import database
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

    @app.route('/caja/api/bandeja/<name>')
    @login_required
    def caja_bandeja(name):
        """3 bandejas de la nueva caja (sobre PedidoReparto):
        por_cobrar | cadetes | drogueria."""
        return jsonify({'pedidos': caja.listar_bandeja(name)})

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

    @app.route('/caja/<int:ticket_id>/enviar-reparto', methods=['POST'])
    @login_required
    def caja_enviar_reparto(ticket_id):
        return jsonify(caja.enviar_a_reparto(ticket_id))

    # ── Catálogo de formas de pago (admin) ──────────────────────────────────

    @app.route('/caja/export')
    @login_required
    def caja_export():
        fecha_str = request.args.get('fecha') or date.today().isoformat()
        try:
            fecha = date.fromisoformat(fecha_str)
        except ValueError:
            return jsonify({'error': 'fecha inválida'}), 400

        desde = datetime(fecha.year, fecha.month, fecha.day, 0, 0, 0)
        hasta = desde + timedelta(days=1)
        with database.get_db() as s:
            T = database.TicketCaja
            tickets = (
                s.query(
                    T.creado_en, T.cliente_nombre, T.forma_pago,
                    T.total, T.estado,
                    database.Usuario.username.label('operador'),
                )
                .outerjoin(database.Usuario, database.Usuario.id == T.operador_id)
                .filter(
                    T.creado_en >= desde,
                    T.creado_en < hasta,
                    T.estado.notin_(['anulado']),
                )
                .order_by(T.creado_en)
                .all()
            )

        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(['fecha', 'hora', 'cliente', 'forma_pago', 'total', 'estado', 'operador'])
        for t in tickets:
            w.writerow([
                t.creado_en.strftime('%Y-%m-%d'),
                t.creado_en.strftime('%H:%M'),
                t.cliente_nombre or '',
                t.forma_pago or '',
                f'{float(t.total):.2f}',
                t.estado,
                t.operador or '',
            ])

        filename = f'bot_caja_{fecha_str}.csv'
        return Response(
            buf.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )

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
