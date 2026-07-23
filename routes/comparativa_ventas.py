"""Comparativa de ventas semanales Pieri vs Badia (admin, no listada en informes)."""
from flask import jsonify, render_template, request

from auth import requiere_permiso
from services import comparativa_ventas as svc


def init_app(app):
    @app.route('/admin/comparativa-ventas')
    @requiere_permiso('usuarios', 'admin')
    def comparativa_ventas():
        return render_template('comparativa_ventas.html', meses=svc.meses_disponibles())

    @app.route('/admin/comparativa-ventas/data')
    @requiere_permiso('usuarios', 'admin')
    def comparativa_ventas_data():
        try:
            mes = int(request.args.get('mes', ''))
        except (ValueError, TypeError):
            mes = None
        if not mes:
            from database import now_ar
            n = now_ar()
            mes = n.year * 100 + n.month
        data = svc.analizar_semanal(mes)
        return jsonify(data)
