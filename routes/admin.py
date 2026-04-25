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

    @app.route('/admin/cron-log')
    @requiere_permiso('usuarios', 'admin')
    def admin_cron_log():
        """Vista unificada de procesos automáticos."""
        from database import CronLog, get_db, now_ar
        from sqlalchemy import desc, func as _func
        from datetime import timedelta
        proceso_filter = (request.args.get('proceso') or '').strip()
        estado_filter = (request.args.get('estado') or '').strip()
        try:
            limit = min(500, int(request.args.get('limit', '100')))
        except ValueError:
            limit = 100

        with get_db() as session:
            base = session.query(CronLog).order_by(desc(CronLog.inicio))
            if proceso_filter:
                base = base.filter(CronLog.proceso.ilike(f'%{proceso_filter}%'))
            if estado_filter:
                base = base.filter(CronLog.estado == estado_filter)
            entries = base.limit(limit).all()
            # Stats últimas 24h
            corte = now_ar() - timedelta(hours=24)
            stats_24h = session.query(CronLog.estado, _func.count(CronLog.id)).filter(
                CronLog.inicio >= corte
            ).group_by(CronLog.estado).all()
            stats = {e: int(n) for e, n in stats_24h}
            # Procesos distintos para el dropdown
            procesos = sorted({e.proceso for e in session.query(CronLog.proceso).distinct().all()})

        return render_template('admin_cron_log.html',
                               entries=entries, stats=stats,
                               proceso_filter=proceso_filter,
                               estado_filter=estado_filter,
                               procesos_distintos=procesos,
                               limit=limit)

    @app.route('/api/cron-log', methods=['POST'])
    def api_cron_log_externo():
        """Recibe reporte de un proceso externo (ej. DockerPanel) y lo registra.
        Body JSON: { proceso, estado, duracion_ms?, mensaje?, error?, origen? }
        """
        import cron_log
        import re
        data = request.get_json(silent=True) or {}
        proceso = (data.get('proceso') or '').strip()
        estado = (data.get('estado') or '').strip()
        # Acepta nombres tipo: sync_productos, mv_refresh:mv_stats_drogas,
        # vincular_observer:pedido_12, agente_pendientes, etc.
        if not re.match(r'^[a-z][a-z0-9_:-]{1,79}$', proceso):
            return jsonify({'error': 'proceso inválido (formato esperado: minúsculas, _ : -)'}), 400
        if estado not in ('ok', 'error'):
            return jsonify({'error': 'estado inválido'}), 400
        log_id = cron_log.registrar_externo(
            proceso=proceso,
            estado=estado,
            duracion_ms=data.get('duracion_ms'),
            mensaje=data.get('mensaje'),
            error=data.get('error'),
            origen=data.get('origen', 'dockerpanel'),
        )
        return jsonify({'ok': log_id is not None, 'id': log_id})

    @app.route('/api/cron-log/purgar', methods=['POST'])
    @requiere_permiso('usuarios', 'admin')
    def api_cron_log_purgar():
        """Elimina filas > 7 días. Lo dispara el cron del DockerPanel
        o se puede llamar manualmente desde la UI."""
        import cron_log
        try:
            dias = int(request.args.get('dias', '7'))
        except ValueError:
            dias = 7
        n = cron_log.purgar_viejos(dias=dias)
        return jsonify({'ok': True, 'eliminadas': n, 'dias': dias})

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
