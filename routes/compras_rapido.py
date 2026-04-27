"""Compra rápida multi-droguería — partir del informe de stock bajo,
ver mejor descuento por producto, agrupar por droguería y exportar.

Por ahora STUB: muestra el diseño aprobado y el progreso. La implementación
real está en el backlog AppSeguimiento (~6 días, 8 fases).
"""
from flask import render_template
from flask_login import login_required


def init_app(app):

    @app.route('/compras/rapido')
    @login_required
    def compras_rapido():
        return render_template('compras_rapido_stub.html')
