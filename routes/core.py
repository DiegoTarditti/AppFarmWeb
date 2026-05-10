"""Core routes: index, ingresos, settings, admin, health."""

import os

from flask import flash, make_response, redirect, render_template, request, url_for

import database
from helpers import get_config, get_providers


def init_app(app):

    @app.route('/')
    def index():
        from flask_login import current_user

        import home_cards as hc
        with database.get_db() as session:
            uid = current_user.id if current_user.is_authenticated else None
            cards, _modo = hc.resolve_cards_para_usuario(session, uid)
            # Counts para badges en cards. Una sola transacción.
            badges = {
                'pedidos_pendientes': session.query(database.Pedido)
                    .filter(database.Pedido.estado == 'PENDIENTE').count(),
                'reclamos_abiertos': session.query(database.Claim)
                    .filter(database.Claim.estado == 'ABIERTO').count(),
                'docs_pendientes': session.query(database.DocumentoPendiente)
                    .filter(database.DocumentoPendiente.estado == 'PENDIENTE').count(),
                'procesos_abiertos': session.query(database.ProcesoCompra)
                    .filter(database.ProcesoCompra.estado != 'COMPLETADO').count(),
                'productos_pendientes_revision': session.query(database.ProductoPendienteRevision)
                    .filter(database.ProductoPendienteRevision.estado == 'pendiente').count(),
            }
        cards = [c for c in cards if not c.get('oculto')]
        # Inyectar badge_count en cada card según su badge_key
        for c in cards:
            key = c.get('badge_key')
            c['badge_count'] = badges.get(key) if key else None
        # Agrupar cards por categoría preservando el orden auto/fijo dentro
        # de cada grupo. CATEGORIAS_HOME dicta el orden de los grupos.
        from collections import OrderedDict
        cards_por_cat = OrderedDict((k, []) for k, _label in hc.CATEGORIAS_HOME)
        for c in cards:
            cat = c.get('categoria') or 'operativo'
            cards_por_cat.setdefault(cat, []).append(c)
        # Lista [(key, label, [cards])] saltando categorías vacías.
        cat_labels = dict(hc.CATEGORIAS_HOME)
        grupos_acciones = [
            (k, cat_labels.get(k, k), cs)
            for k, cs in cards_por_cat.items() if cs
        ]
        return render_template('index.html',
                               config=get_config(),
                               acciones=cards,
                               grupos_acciones=grupos_acciones)

    @app.route('/ingresos')
    def ingresos():
        pdf_pendiente = request.args.get('pdf_pendiente', '')
        doc_pendiente_id = request.args.get('doc_pendiente_id', '', type=int)
        proceso_id = request.args.get('proceso_id', '', type=int)
        return render_template('ingresos.html', providers=get_providers(), config=get_config(),
                               pdf_pendiente=pdf_pendiente, doc_pendiente_id=doc_pendiente_id or '',
                               proceso_id=proceso_id or '')

    @app.route('/settings')
    def settings():
        return render_template('settings.html', config=get_config())

    @app.route('/settings', methods=['POST'])
    def settings_save():
        nombre = request.form.get('farmacia_nombre', '').strip() or 'Farmacia'
        ruta = request.form.get('ruta_facturas', '').strip()
        with database.get_db() as session:
            cfg = session.get(database.Config, 1)
            if not cfg:
                cfg = database.Config(id=1)
                session.add(cfg)
            cfg.farmacia_nombre = nombre
            cfg.ruta_facturas = ruta or None
            cfg.ruta_excels = (request.form.get('ruta_excels') or '').strip() or None
            cfg.ruta_descargas = (request.form.get('ruta_descargas') or '').strip() or None
            cfg.ruta_backups = (request.form.get('ruta_backups') or '').strip() or None
            cfg.ruta_plantillas_lab = (request.form.get('ruta_plantillas_lab') or '').strip() or None
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
            cfg.keep_alive_enabled = request.form.get('keep_alive_enabled') == '1'
            try:
                cfg.keep_alive_interval_min = max(1, min(60, int(request.form.get('keep_alive_interval_min', 10))))
            except (ValueError, TypeError):
                pass
            dockerpanel_ruta = (request.form.get('dockerpanel_ruta') or '').strip()
            cfg.dockerpanel_ruta = dockerpanel_ruta or None
            session.commit()
        flash('Configuración guardada.')
        return redirect(url_for('settings'))

    @app.route('/admin/dashboard')
    @app.route('/admin/console')  # alias retrocompatible
    def admin_console():
        """Dashboard antiguo con stats de la DB. Movido a /admin/dashboard
        porque /admin lo usa ahora la pantalla de admin_index.html con utilidades.
        Mantenemos el endpoint admin_console para que url_for siga funcionando."""
        with database.get_db() as session:
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
    @app.route('/health_web')
    def health():
        """Healthcheck usado por Render. Hace SELECT 1 para mantener viva la conexión DB."""
        try:
            with database.get_db() as session:
                session.execute(database.text('SELECT 1'))
            return 'OK', 200
        except Exception as e:
            return f'DB ERROR: {e}', 503
