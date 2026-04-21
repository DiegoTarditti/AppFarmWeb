"""Rutas de administración: seed y cleanup desde la UI, protegidas por rol admin.

Útil cuando no tenés acceso al shell del servidor (ej. Render free tier).
"""

import os
import sys
from flask import render_template, request, redirect, url_for, flash
from auth import requiere_permiso

# Path hack para poder importar scripts/
_SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts')
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


def init_app(app):

    @app.route('/admin')
    @requiere_permiso('usuarios', 'admin')
    def admin_index():
        return render_template('admin_index.html')

    @app.route('/admin/seed-proveedores', methods=['GET', 'POST'])
    @requiere_permiso('usuarios', 'admin')
    def admin_seed_proveedores():
        from seed_proveedores import seed_proveedores
        ejecutar = request.method == 'POST' and request.form.get('ejecutar') == '1'
        try:
            resultado = seed_proveedores(ejecutar=ejecutar)
        except Exception as e:
            flash(f'Error: {e}', 'error')
            return redirect(url_for('admin_index'))
        if ejecutar:
            flash(f'Seed aplicado: {len(resultado["crear"])} creados, '
                  f'{len(resultado["actualizar"])} actualizados.', 'success')
        return render_template('admin_seed_proveedores.html',
                               resultado=resultado, ejecutado=ejecutar)

    @app.route('/admin/cleanup-inactivos', methods=['GET', 'POST'])
    @requiere_permiso('usuarios', 'admin')
    def admin_cleanup_inactivos():
        from cleanup_inactivos import cleanup_inactivos
        ejecutar = request.method == 'POST' and request.form.get('ejecutar') == '1'
        try:
            resultado = cleanup_inactivos(ejecutar=ejecutar)
        except Exception as e:
            flash(f'Error: {e}', 'error')
            return redirect(url_for('admin_index'))
        if ejecutar:
            borrados_p = len((resultado.get('proveedores') or {}).get('sin_movimiento') or [])
            borrados_l = len((resultado.get('laboratorios') or {}).get('sin_movimiento') or [])
            flash(f'Borrados {borrados_p} proveedores y {borrados_l} laboratorios.', 'success')
        return render_template('admin_cleanup_inactivos.html',
                               resultado=resultado, ejecutado=ejecutar)
