"""Rutas de administración: seed y cleanup desde la UI, protegidas por rol admin.

Útil cuando no tenés acceso al shell del servidor (ej. Render free tier).
"""

import os
import sys

from flask import flash, jsonify, redirect, render_template, request, url_for

import database
from auth import requiere_permiso

# Path hack para poder importar scripts/
_SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts')
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


def init_app(app):

    @app.route('/admin')
    @requiere_permiso('usuarios', 'admin')
    def admin_index():
        # Conteo rápido de alarmas activas para mostrar badge en el header.
        n_alarmas = 0
        n_criticas = 0
        try:
            import alarmas as _alarmas
            with database.get_db() as _s:
                lista = _alarmas.evaluar_todas(_s)
                n_alarmas = len(lista)
                n_criticas = sum(1 for a in lista if a.severidad == 'critica')
        except Exception:
            pass
        return render_template('admin_index.html',
                                n_alarmas=n_alarmas,
                                n_criticas=n_criticas)

    @app.route('/admin/health')
    @requiere_permiso('usuarios', 'admin')
    def admin_health():
        """Diagnóstico rápido del sistema. Una sola pantalla con todo lo
        que un dev necesita para chequear rápido si algo se rompió:
        - DB: SELECT 1, version, conteos por tabla principal.
        - Sync ObServer: estado por entidad (ventas/stock/productos/labs/clientes).
        - Última actividad de crons (3 más recientes).
        - Versión deployada (commit SHA si está disponible).
        - Hora server (UTC + AR).
        """
        from datetime import datetime

        from sqlalchemy import desc as _desc
        from sqlalchemy import text as _text

        from database import (
            Claim,
            CronLog,
            Invoice,
            ObsProducto,
            ObsStock,
            ObsVentaDetalle,
            Pedido,
            Producto,
            now_ar,
        )

        info = {}

        # Versión deployada — Render expone RENDER_GIT_COMMIT, sino fallback
        # a leer .git/HEAD localmente.
        sha = (os.environ.get('RENDER_GIT_COMMIT')
               or os.environ.get('BUILD_SHA')
               or '')
        if not sha:
            try:
                head_path = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    '.git', 'HEAD',
                )
                if os.path.exists(head_path):
                    with open(head_path, encoding='utf-8') as f:
                        ref = f.read().strip()
                    if ref.startswith('ref: '):
                        ref_path = os.path.join(
                            os.path.dirname(head_path), ref[5:],
                        )
                        if os.path.exists(ref_path):
                            with open(ref_path, encoding='utf-8') as f:
                                sha = f.read().strip()
                    else:
                        sha = ref
            except Exception:
                pass
        info['version_sha'] = sha[:12] if sha else '—'
        info['version_full'] = sha or '—'

        # Hora server
        ahora_utc = datetime.utcnow()
        info['hora_utc'] = ahora_utc.strftime('%Y-%m-%d %H:%M:%S UTC')
        info['hora_ar'] = now_ar().strftime('%Y-%m-%d %H:%M:%S')

        # DB
        db_ok = False
        db_error = None
        db_version = None
        try:
            with database.get_db() as s:
                s.execute(_text('SELECT 1'))
                db_ok = True
                try:
                    row = s.execute(_text('SELECT version()')).fetchone()
                    if row:
                        # Acortamos: "PostgreSQL 18.x on x86_64..." → "PostgreSQL 18.x"
                        full = str(row[0])
                        info['db_version'] = full.split(' on ')[0] if ' on ' in full else full[:60]
                        db_version = info['db_version']
                except Exception:
                    info['db_version'] = '—'
        except Exception as e:
            db_error = str(e)[:200]
        info['db_ok'] = db_ok
        info['db_error'] = db_error

        # Conteos tablas principales
        conteos = []
        if db_ok:
            with database.get_db() as s:
                for nombre, modelo in [
                    ('Productos (catálogo local)', Producto),
                    ('ObServer Productos', ObsProducto),
                    ('ObServer Ventas detalle', ObsVentaDetalle),
                    ('ObServer Stock', ObsStock),
                    ('Facturas', Invoice),
                    ('Pedidos', Pedido),
                    ('Reclamos', Claim),
                ]:
                    try:
                        n = s.query(modelo).count()
                        conteos.append({'nombre': nombre, 'cantidad': n})
                    except Exception as e:
                        conteos.append({'nombre': nombre, 'cantidad': None,
                                        'error': str(e)[:80]})
        info['conteos'] = conteos

        # Total de tablas en metadata SQLAlchemy
        info['n_tablas_metadata'] = len(database.Base.metadata.sorted_tables)

        # Sync ObServer
        sync_estados = {}
        try:
            import observer_source
            with database.get_db() as s:
                sync_estados = observer_source.estado_syncs(s)
        except Exception as e:
            info['sync_error'] = str(e)[:200]
        info['sync_estados'] = sync_estados

        # Últimos 5 runs de cron
        cron_recientes = []
        try:
            with database.get_db() as s:
                rows = (s.query(CronLog)
                        .order_by(_desc(CronLog.inicio))
                        .limit(5).all())
                for r in rows:
                    cron_recientes.append({
                        'proceso': r.proceso,
                        'estado': r.estado,
                        'inicio': r.inicio.strftime('%Y-%m-%d %H:%M') if r.inicio else '—',
                        'duracion_ms': r.duracion_ms,
                        'origen': r.origen or '—',
                    })
        except Exception:
            pass
        info['cron_recientes'] = cron_recientes

        # Python + worker
        info['python_version'] = sys.version.split()[0]
        info['worker_pid'] = os.getpid()

        return render_template('admin_health.html', info=info)

    @app.route('/admin/alarmas')
    @requiere_permiso('usuarios', 'admin')
    def admin_alarmas():
        """Pantalla unificada de alarmas — chequeos automáticos del sistema.
        Spec: c:/AppSeguimiento/mantenimiento-y-alarmas.md
        """
        import alarmas as _alarmas
        with database.get_db() as session:
            lista = _alarmas.evaluar_todas(session)
            por_sev = _alarmas.contar_por_severidad(lista)
        return render_template('admin_alarmas.html',
                                alarmas=lista,
                                por_severidad=por_sev,
                                total=len(lista))

    @app.route('/mock/pedidos-nuevo')
    @requiere_permiso('usuarios', 'admin')
    def mock_pedidos_nuevo():
        """Mock estático del pedido de reposición grupal multi-farmacia.
        Sirve para mostrar a Esteban el concepto antes de implementar.
        Spec: c:/AppSeguimiento/sistema-pedidos-nuevo.md
        """
        return render_template('mock_pedidos_nuevo.html')

    @app.route('/api/admin/alarmas')
    @requiere_permiso('usuarios', 'admin')
    def api_admin_alarmas():
        """Versión JSON para polling / integración externa."""
        import alarmas as _alarmas
        with database.get_db() as session:
            lista = _alarmas.evaluar_todas(session)
            por_sev = _alarmas.contar_por_severidad(lista)
        return jsonify({
            'alarmas': [a.to_dict() for a in lista],
            'por_severidad': por_sev,
            'total': len(lista),
        })

    @app.route('/admin/cron-log')
    @requiere_permiso('usuarios', 'admin')
    def admin_cron_log():
        """Vista unificada de procesos automáticos."""
        from datetime import timedelta

        from sqlalchemy import desc
        from sqlalchemy import func as _func

        from database import CronLog, get_db, now_ar
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
        import re

        import cron_log
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

    # ── Notificaciones de alarmas a Telegram ──────────────────────────
    @app.route('/api/admin/alarmas/probar-telegram', methods=['POST'])
    @requiere_permiso('usuarios', 'admin')
    def api_alarmas_probar_telegram():
        """Manda un mensaje de prueba al bot configurado para verificar setup."""
        from datetime import datetime as _dt

        import notificaciones
        msg = (
            f'✅ <b>Test desde /admin/alarmas</b>\n'
            f'Si ves esto, el bot está bien configurado.\n'
            f'Hora server: {_dt.now().strftime("%Y-%m-%d %H:%M:%S")}'
        )
        ok, err = notificaciones.enviar_telegram(msg)
        return jsonify({'ok': ok, 'error': err})

    @app.route('/api/cron/notificar-alarmas', methods=['POST'])
    def api_cron_notificar_alarmas():
        """Endpoint disparado por GitHub Actions cada 15 min.
        Auth: header X-Cron-Secret (mismo patrón que recalcular_os_clientes).
        """
        import hmac as _hmac
        expected = os.environ.get('CRON_SECRET', '').strip()
        if not expected:
            return jsonify({'ok': False, 'error': 'CRON_SECRET no configurado'}), 503
        provided = (request.headers.get('X-Cron-Secret') or '').strip()
        if not provided or not _hmac.compare_digest(provided, expected):
            return jsonify({'ok': False, 'error': 'Secret inválido'}), 401

        import cron_log
        import notificaciones
        # Si no hay TOKEN/CHAT_ID, salir 503 con explicación (no se loguea como error).
        if not notificaciones._telegram_config():
            return jsonify({
                'ok': False,
                'error': 'TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID no configurados en el server',
            }), 503

        # App URL para construir links absolutos en los mensajes
        app_url = (os.environ.get('APP_URL') or '').rstrip('/')

        # Severidades a notificar — configurable vía env var (separadas por coma).
        # Default: critica + alta. Para incluir media: ALARMAS_SEVERIDADES=critica,alta,media.
        sev_raw = os.environ.get('ALARMAS_SEVERIDADES', 'critica,alta')
        severidades = tuple(s.strip() for s in sev_raw.split(',') if s.strip())

        try:
            with cron_log.registrar('notificar_alarmas', origen='cron') as log:
                with database.get_db() as session:
                    res = notificaciones.evaluar_y_notificar(
                        session, severidades=severidades, app_url=app_url,
                    )
                log.metadata = res
                if res.get('errores'):
                    log.error = ' | '.join(res['errores'][:3])
            return jsonify({'ok': True, **res})
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)}), 500

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

    @app.route('/api/obs/recalcular-os-clientes', methods=['POST'])
    @requiere_permiso('usuarios', 'admin')
    def api_recalcular_os_clientes():
        """Dispara `scripts.recalcular_os_por_cliente.recalcular()` y registra
        el resultado en cron_log. Ideal para botón admin o cron diario.
        """
        from recalcular_os_por_cliente import recalcular

        import cron_log
        try:
            with cron_log.registrar('recalcular_os_clientes', origen='web') as log:
                res = recalcular()
                log.metadata = {
                    'procesados': res['procesados'],
                    'con_os': res['con_os'],
                    'sin_os': res['sin_os'],
                }
            return jsonify({'ok': True, **res})
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)}), 500

    @app.route('/api/cron/recalcular-os-clientes', methods=['POST'])
    def api_cron_recalcular_os_clientes():
        """Variante sin auth web del recálculo de OS principal por cliente,
        protegida por header `X-Cron-Secret`. Pensada para ser llamada por
        un cron externo (GitHub Actions, Render Cron, DockerPanel, etc.).

        Configurar la env var `CRON_SECRET` en el server (Render dashboard
        o docker-compose) y pasarla en el header de la request:

            curl -X POST https://app.example.com/api/cron/recalcular-os-clientes \\
                 -H "X-Cron-Secret: <secret>"

        Si `CRON_SECRET` NO está set en el server, el endpoint devuelve 503
        (deshabilitado por seguridad). No hay default — fail-safe.
        """
        import hmac as _hmac
        import os as _os

        from recalcular_os_por_cliente import recalcular

        import cron_log
        expected = _os.environ.get('CRON_SECRET', '').strip()
        if not expected:
            return jsonify({'ok': False, 'error': 'CRON_SECRET no configurado en el server'}), 503
        provided = (request.headers.get('X-Cron-Secret') or '').strip()
        if not provided or not _hmac.compare_digest(provided, expected):
            return jsonify({'ok': False, 'error': 'Secret inválido'}), 401

        try:
            with cron_log.registrar('recalcular_os_clientes', origen='cron') as log:
                res = recalcular()
                log.metadata = {
                    'procesados': res['procesados'],
                    'con_os': res['con_os'],
                    'sin_os': res['sin_os'],
                }
            return jsonify({'ok': True, **res})
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)}), 500

    @app.route('/api/cron/limpiar-home-card-clicks', methods=['POST'])
    def api_cron_limpiar_home_card_clicks():
        """Borra entries de `home_card_clicks` de más de 90 días.

        La tabla solo se usa para rankear cards en el home (uso reciente). Datos
        viejos no aportan y crecen sin parar. Sin esto la tabla pasa de 100k
        filas con uso intensivo y empieza a notarse en el page load.

        Auth: header `X-Cron-Secret` (mismo patrón que recalcular-os-clientes).
        """
        import hmac as _hmac
        from datetime import timedelta

        from sqlalchemy import text as _text

        import cron_log
        from database import now_ar

        expected = os.environ.get('CRON_SECRET', '').strip()
        if not expected:
            return jsonify({'ok': False, 'error': 'CRON_SECRET no configurado en el server'}), 503
        provided = (request.headers.get('X-Cron-Secret') or '').strip()
        if not provided or not _hmac.compare_digest(provided, expected):
            return jsonify({'ok': False, 'error': 'Secret inválido'}), 401

        # Permitir override del horizonte vía query param `dias` (default 90).
        try:
            dias = max(7, int(request.args.get('dias', '90')))
        except ValueError:
            dias = 90

        corte = now_ar() - timedelta(days=dias)

        try:
            with cron_log.registrar('limpiar_home_card_clicks', origen='cron') as log:
                with database.get_db() as session:
                    res = session.execute(
                        _text('DELETE FROM home_card_clicks WHERE clicked_at < :corte'),
                        {'corte': corte},
                    )
                    borrados = res.rowcount or 0
                    session.commit()
                log.metadata = {'borrados': borrados, 'dias': dias}
            return jsonify({'ok': True, 'borrados': borrados, 'dias': dias})
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)}), 500

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
        from reset_datos import GRUPOS, calcular_dry_run, ejecutar_reset
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

    # ── Panel de comandos remotos ──────────────────────────────────────
    # Buzón de comandos en Render que la PC farmacia (DockerPanel) consume
    # vía polling outbound. Permite deployar / ejecutar comandos desde
    # cualquier device sin necesidad de exponer la red de la farmacia.
    PANEL_COMANDOS_WHITELIST = {
        'pull_restart': 'Pull código + Restart Web',
        'restart': 'Restart Web',
        'restart_full': 'Down + Up (recreate)',
        'logs': 'Logs Web (50 líneas)',
        'status': 'Estado contenedores',
        'version': 'Versión deployada (git rev)',
        'sync_now': 'Sync ObServer ahora',
        'dedupe_labs_dry': 'Dedupe labs/proveedores (DRY-RUN)',
        'dedupe_labs_apply': 'Dedupe labs/proveedores (APLICAR)',
        'purgar_cron_log': 'Purgar cron_log >7 días',
        'health': 'Health check completo',
    }

    @app.route('/admin/panel')
    @requiere_permiso('usuarios', 'admin')
    def admin_panel():
        """UI del buzón de comandos: dropdown + historial."""
        with database.get_db() as session:
            recientes = (session.query(database.PanelComando)
                         .order_by(database.PanelComando.solicitado_en.desc())
                         .limit(30).all())
        return render_template('admin_panel.html',
                               whitelist=PANEL_COMANDOS_WHITELIST,
                               recientes=recientes)

    @app.route('/admin/panel/comandos', methods=['POST'])
    @requiere_permiso('usuarios', 'admin')
    def admin_panel_encolar():
        """Encola un comando para que DockerPanel lo agarre en su próximo polling."""
        from flask_login import current_user
        if request.is_json:
            comando = ((request.get_json(silent=True) or {}).get('comando') or '').strip()
        else:
            comando = (request.form.get('comando') or '').strip()
        if comando not in PANEL_COMANDOS_WHITELIST:
            if request.is_json:
                return jsonify({'ok': False, 'error': 'Comando no permitido'}), 400
            flash('Comando no permitido.', 'error')
            return redirect(url_for('admin_panel'))
        username = getattr(current_user, 'username', None) or 'admin'
        with database.get_db() as session:
            cmd = database.PanelComando(
                comando=comando, estado='pendiente', solicitado_por=username,
            )
            session.add(cmd)
            session.commit()
            cmd_id = cmd.id
        if request.is_json:
            return jsonify({'ok': True, 'id': cmd_id, 'comando': comando})
        flash(f'Comando "{PANEL_COMANDOS_WHITELIST[comando]}" encolado (#{cmd_id}). DockerPanel lo levantará en el próximo poll.', 'success')
        return redirect(url_for('admin_panel'))

    @app.route('/admin/panel/comandos/recientes')
    @requiere_permiso('usuarios', 'admin')
    def admin_panel_recientes():
        """JSON con últimos N comandos para auto-refresh de la UI."""
        try:
            limit = min(50, max(5, int(request.args.get('limit', '30'))))
        except ValueError:
            limit = 30
        with database.get_db() as session:
            rows = (session.query(database.PanelComando)
                    .order_by(database.PanelComando.solicitado_en.desc())
                    .limit(limit).all())
            recientes = [{
                'id': r.id, 'comando': r.comando, 'estado': r.estado,
                'solicitado_en': r.solicitado_en.isoformat() if r.solicitado_en else None,
                'solicitado_por': r.solicitado_por,
                'tomado_en': r.tomado_en.isoformat() if r.tomado_en else None,
                'ejecutado_en': r.ejecutado_en.isoformat() if r.ejecutado_en else None,
                'duracion_ms': r.duracion_ms,
                'resultado': (r.resultado[:8000] if r.resultado else None),
                'origen': r.origen,
            } for r in rows]
        return jsonify({'ok': True, 'comandos': recientes})

    def _check_panel_token():
        """Valida el header X-Panel-Token contra la env var PANEL_REMOTO_TOKEN.
        Si la env var no está set en el server, el endpoint queda deshabilitado (503)."""
        import hmac
        expected = os.environ.get('PANEL_REMOTO_TOKEN', '').strip()
        if not expected:
            return False, ('PANEL_REMOTO_TOKEN no configurado en el server', 503)
        provided = (request.headers.get('X-Panel-Token') or '').strip()
        # compare_digest evita timing attacks (la comparación con != de strings
        # puede leakear el token byte a byte por el tiempo de respuesta).
        if not provided or not hmac.compare_digest(provided, expected):
            return False, ('Token inválido', 401)
        return True, None

    @app.route('/api/panel/comandos/proximo', methods=['GET'])
    def api_panel_proximo():
        """DockerPanel polea acá. Devuelve el próximo comando pendiente y lo
        marca como en_proceso atómicamente. Si no hay nada, devuelve null.
        """
        from sqlalchemy import text as _text

        from database import now_ar
        ok, err = _check_panel_token()
        if not ok:
            return jsonify({'ok': False, 'error': err[0]}), err[1]
        origen = (request.args.get('origen') or 'dockerpanel').strip()[:40]
        with database.get_db() as session:
            # SELECT FOR UPDATE SKIP LOCKED para que múltiples workers no agarren el mismo
            row = session.execute(_text(
                "SELECT id FROM panel_comandos WHERE estado = 'pendiente' "
                "ORDER BY solicitado_en ASC LIMIT 1 FOR UPDATE SKIP LOCKED"
            )).first()
            if not row:
                return jsonify({'ok': True, 'comando': None})
            cmd = session.get(database.PanelComando, row[0])
            cmd.estado = 'en_proceso'
            cmd.tomado_en = now_ar()
            cmd.origen = origen
            session.commit()
            return jsonify({'ok': True, 'comando': {
                'id': cmd.id, 'comando': cmd.comando,
                'solicitado_en': cmd.solicitado_en.isoformat(),
                'solicitado_por': cmd.solicitado_por,
            }})

    @app.route('/api/panel/comandos/<int:cmd_id>/resultado', methods=['POST'])
    def api_panel_resultado(cmd_id):
        """DockerPanel reporta acá. Body JSON: {estado: ok|error, resultado: str, duracion_ms?}"""
        from database import now_ar
        ok, err = _check_panel_token()
        if not ok:
            return jsonify({'ok': False, 'error': err[0]}), err[1]
        data = request.get_json(silent=True) or {}
        estado = (data.get('estado') or '').strip()
        if estado not in ('ok', 'error'):
            return jsonify({'ok': False, 'error': 'estado inválido'}), 400
        resultado = (data.get('resultado') or '')[:32000]
        duracion = data.get('duracion_ms')
        with database.get_db() as session:
            cmd = session.get(database.PanelComando, cmd_id)
            if not cmd:
                return jsonify({'ok': False, 'error': 'Comando no existe'}), 404
            if cmd.estado not in ('en_proceso', 'pendiente'):
                return jsonify({'ok': False, 'error': f'Comando ya está en estado {cmd.estado}'}), 409
            cmd.estado = estado
            cmd.resultado = resultado
            cmd.duracion_ms = int(duracion) if isinstance(duracion, (int, float)) else None
            cmd.ejecutado_en = now_ar()
            session.commit()
        return jsonify({'ok': True, 'id': cmd_id, 'estado': estado})
