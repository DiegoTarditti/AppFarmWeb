"""Core routes: index, ingresos, settings, admin, health."""

import os
from flask import render_template, request, redirect, url_for, flash, make_response
import database
from helpers import get_config, get_providers


def init_app(app):

    @app.route('/')
    def index():
        return render_template('index.html', config=get_config())

    @app.route('/ingresos')
    def ingresos():
        pdf_pendiente = request.args.get('pdf_pendiente', '')
        doc_pendiente_id = request.args.get('doc_pendiente_id', '', type=int)
        return render_template('ingresos.html', providers=get_providers(), config=get_config(),
                               pdf_pendiente=pdf_pendiente, doc_pendiente_id=doc_pendiente_id or '')

    @app.route('/settings')
    def settings():
        return render_template('settings.html', config=get_config())

    @app.route('/settings', methods=['POST'])
    def settings_save():
        nombre = request.form.get('farmacia_nombre', '').strip() or 'Farmacia'
        ruta = request.form.get('ruta_facturas', '').strip()
        session = database.SessionLocal()
        cfg = session.get(database.Config, 1)
        if not cfg:
            cfg = database.Config(id=1)
            session.add(cfg)
        cfg.farmacia_nombre = nombre
        cfg.ruta_facturas = ruta or None
        try:
            cfg.umbral_pico = max(1.01, min(3.0, float(request.form.get('umbral_pico', 1.30))))
            cfg.umbral_baja = max(0.01, min(0.99, float(request.form.get('umbral_baja', 0.70))))
            cfg.umbral_tendencia = max(0.0, min(5.0, float(request.form.get('umbral_tendencia', 0.20))))
            cfg.rot_alta_min = max(0.0, float(request.form.get('rot_alta_min', 20.0)))
            cfg.rot_alta_tol = max(0.0, float(request.form.get('rot_alta_tol', 0.0)))
            cfg.rot_media_min = max(0.0, float(request.form.get('rot_media_min', 5.0)))
            cfg.rot_media_tol = max(0.0, float(request.form.get('rot_media_tol', 0.0)))
            cfg.rot_baja_tol = max(0.0, float(request.form.get('rot_baja_tol', 0.0)))
        except (ValueError, TypeError):
            pass
        session.commit()
        session.close()
        flash('Configuración guardada.')
        return redirect(url_for('settings'))

    @app.route('/admin')
    def admin_console():
        session = database.SessionLocal()
        try:
            stats = {
                'proveedores': session.query(database.Provider).count(),
                'facturas': session.query(database.Invoice).count(),
                'factura_items': session.query(database.InvoiceItem).count(),
                'reclamos': session.query(database.Claim).count(),
                'productos': session.query(database.Producto).count(),
                'pedidos': session.query(database.Pedido).count(),
                'erp_stock': session.query(database.ErpStock).count(),
                'barcode_mappings': session.query(database.BarcodeMapping).count(),
                'modulos': session.query(database.Modulo).count(),
                'modulo_packs': session.query(database.ModuloPack).count(),
            }
        finally:
            session.close()

        deploy = {
            'commit':  os.environ.get('RENDER_GIT_COMMIT', '')[:7] or 'local',
            'branch':  os.environ.get('RENDER_GIT_BRANCH', 'local'),
            'service': os.environ.get('RENDER_SERVICE_NAME', 'local'),
            'url':     os.environ.get('RENDER_EXTERNAL_URL', ''),
        }
        return render_template('admin.html', stats=stats, deploy=deploy)

    @app.route('/admin/backup', methods=['POST'])
    def admin_backup():
        import subprocess
        from datetime import datetime as _dt

        db_url = (request.form.get('db_url') or '').strip()
        if not db_url:
            flash('Falta la URL de la base de datos.')
            return redirect(url_for('admin_console'))
        if not db_url.startswith(('postgres://', 'postgresql://')):
            flash('URL inválida (debe empezar con postgres:// o postgresql://).')
            return redirect(url_for('admin_console'))

        cmd = ['pg_dump', '--no-owner', '--no-privileges', '--clean',
               '--if-exists', db_url]
        result = subprocess.run(cmd, capture_output=True)

        if result.returncode != 0:
            err = result.stderr.decode('utf-8', errors='replace').strip()
            flash(f'pg_dump falló (exit {result.returncode}): {err[:500]}')
            return redirect(url_for('admin_console'))

        ts = _dt.now().strftime('%Y%m%d_%H%M%S')
        filename = f'farmacia_backup_{ts}.sql'
        resp = make_response(result.stdout)
        resp.headers['Content-Type'] = 'application/sql'
        resp.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
        return resp

    @app.route('/health')
    def health():
        return 'OK', 200
