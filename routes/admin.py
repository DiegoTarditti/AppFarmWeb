"""Rutas de administración: seed y cleanup desde la UI, protegidas por rol admin.

Útil cuando no tenés acceso al shell del servidor (ej. Render free tier).
"""

import os
import sys
from flask import render_template, request, redirect, url_for, flash, jsonify
from auth import requiere_permiso
import database

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

    @app.route('/api/dockerpanel-info')
    def api_dockerpanel_info():
        """Devuelve la ruta configurada del DockerPanel para que el widget sepa qué abrir.
        No requiere permiso admin — cualquier usuario logueado puede leer solo la ruta."""
        with database.get_db() as session:
            cfg = session.get(database.Config, 1)
            ruta = (cfg.dockerpanel_ruta or '').strip() if cfg else ''
        return jsonify({'ruta': ruta or None})

    @app.route('/admin/reset-datos', methods=['GET', 'POST'])
    @requiere_permiso('usuarios', 'admin')
    def admin_reset_datos():
        """Reset de datos operativos agrupados por módulo (dry-run + ejecución con checkboxes)."""
        from reset_datos import calcular_dry_run, ejecutar_reset, GRUPOS
        logs = None
        if request.method == 'POST':
            seleccion = request.form.getlist('grupo')
            confirmacion = (request.form.get('confirmacion') or '').strip()
            if confirmacion != 'BORRAR':
                flash('Escribí BORRAR en el campo de confirmación para ejecutar.', 'error')
            elif not seleccion:
                flash('No seleccionaste ningún grupo.', 'warning')
            else:
                try:
                    logs = ejecutar_reset(seleccion)
                    flash(f'Reset ejecutado. {len(logs)} operaciones completadas.', 'success')
                except Exception as e:
                    flash(f'Error: {e}', 'error')
                    return redirect(url_for('admin_reset_datos'))
        conteos = calcular_dry_run()
        # Orden estable de grupos para la UI
        grupos_lista = [
            (key, GRUPOS[key], conteos.get(key, {'total': 0, 'detalle': []}))
            for key in GRUPOS
        ]
        return render_template('admin_reset_datos.html',
                               grupos=grupos_lista, logs=logs)
