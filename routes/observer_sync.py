"""Rutas admin para sincronizar el espejo local con ObServer."""
import os
import threading
from datetime import datetime
from flask import render_template, redirect, url_for, flash, request, jsonify
import database
import observer_source
import observer_matcher

# Lock para evitar que 2 syncs corran en paralelo (auto + manual que se pisan)
_SYNC_LOCK = threading.Lock()
_SYNC_ESTADO = {'en_curso': False, 'ultimo_inicio': None, 'ultimo_fin': None,
                'ultimo_resultado': None}


def init_app(app):

    @app.route('/admin/observer-sync')
    def observer_sync_panel():
        """Dashboard de sync: cuenta de filas por entidad + última corrida."""
        with database.get_db() as session:
            cuentas = {
                'laboratorios': session.query(database.ObsLaboratorio).count(),
                'rubros':       session.query(database.ObsRubro).count(),
                'subrubros':    session.query(database.ObsSubrubro).count(),
                'nombres_drogas': session.query(database.ObsNombreDroga).count(),
                'productos':    session.query(database.ObsProducto).count(),
                'stock':        session.query(database.ObsStock).count(),
                'ventas_mensuales': session.query(database.ObsVentaMensual).count(),
            }
            # Última ejecución por entidad
            ultimos = {}
            for ent in cuentas:
                log = (session.query(database.ObsSyncLog)
                       .filter(database.ObsSyncLog.entidad == ent)
                       .order_by(database.ObsSyncLog.ejecutado_en.desc())
                       .first())
                ultimos[ent] = log
            disponible = observer_source.observer_disponible()
            cfg = session.query(database.Config).first()
            ventas_meses = cfg.observer_ventas_meses if cfg else 16
        return render_template('admin_observer_sync.html',
                               cuentas=cuentas, ultimos=ultimos, disponible=disponible,
                               ventas_meses=ventas_meses)

    @app.route('/admin/observer-sync/<entidad>', methods=['POST'])
    def observer_sync_run(entidad):
        """Dispara el sync de una entidad. entidad ∈ {laboratorios, rubros, subrubros,
        nombres_drogas, productos, stock, todo}."""
        if not observer_source.observer_disponible():
            flash('ObServer no está disponible.', 'error')
            return redirect(url_for('observer_sync_panel'))

        funcs_por_nombre = {
            'laboratorios':     observer_source.sync_laboratorios,
            'rubros':           observer_source.sync_rubros,
            'subrubros':        observer_source.sync_subrubros,
            'nombres_drogas':   observer_source.sync_nombres_drogas,
            'productos':        observer_source.sync_productos,
            'stock':            observer_source.sync_stock,
            'ventas_mensuales': observer_source.sync_ventas_mensuales,
        }

        # 'todo' corre en orden para respetar FKs
        orden = ['laboratorios', 'rubros', 'subrubros', 'nombres_drogas',
                 'productos', 'stock', 'ventas_mensuales']

        if entidad == 'todo':
            ents = orden
        elif entidad in funcs_por_nombre:
            ents = [entidad]
        else:
            flash(f'Entidad desconocida: {entidad}', 'error')
            return redirect(url_for('observer_sync_panel'))

        resultados = []
        with database.get_db() as session:
            for ent in ents:
                try:
                    stats = funcs_por_nombre[ent](session)
                    session.commit()
                    resultados.append(
                        f'{ent}: {stats["upsert"]} filas ({stats["duracion_ms"]} ms)'
                    )
                except Exception as e:
                    session.rollback()
                    resultados.append(f'{ent}: ERROR — {e}')
                    break  # si falla una, no seguir con las que dependen

        flash('Sync ObServer — ' + ' · '.join(resultados), 'success')
        next_url = (request.form.get('next') or '').strip()
        if next_url and next_url.startswith('/'):
            return redirect(next_url)
        return redirect(url_for('observer_sync_panel'))

    @app.route('/admin/observer-match-productos', methods=['POST'])
    def observer_match_productos():
        """Corre el auto-matcher EAN ↔ IdProducto. Upsertea productos.observer_id
        para los que tienen match exacto o fuzzy fuerte."""
        try:
            threshold = float(request.form.get('threshold', '0.80'))
        except ValueError:
            threshold = 0.80
        with database.get_db() as session:
            stats = observer_matcher.match_productos(session, threshold=threshold)
            session.commit()
        flash(f'Auto-match completo — {stats["procesados"]} procesados · '
              f'{stats["linked_exact"]} exactos · {stats["linked_fuzzy"]} fuzzy · '
              f'{stats["ambiguos"]} ambiguos · {stats["sin_match"]} sin match · '
              f'{stats["sin_lab"]} sin laboratorio.', 'success')
        return redirect(url_for('observer_sync_panel'))

    @app.route('/productos/sin-vincular')
    def productos_sin_vincular():
        """Pantalla para vincular manualmente productos locales con obs_productos."""
        with database.get_db() as session:
            productos = (session.query(database.Producto)
                         .filter(database.Producto.observer_id.is_(None))
                         .order_by(database.Producto.descripcion)
                         .limit(200).all())
            total_sin = (session.query(database.Producto)
                         .filter(database.Producto.observer_id.is_(None)).count())
            total_con = (session.query(database.Producto)
                         .filter(database.Producto.observer_id.isnot(None)).count())

            data = []
            for p in productos:
                lab_nombre = p.laboratorio.nombre if p.laboratorio else None
                data.append({
                    'id': p.id, 'codigo_barra': p.codigo_barra,
                    'descripcion': p.descripcion or '', 'laboratorio': lab_nombre,
                    'lab_observer': p.laboratorio.observer_id if p.laboratorio else None,
                    'candidatos': observer_matcher.candidatos_para_producto(session, p.id, top_n=5),
                })
        return render_template('productos_sin_vincular.html',
                               productos=data, total_sin=total_sin, total_con=total_con)

    @app.route('/producto/<int:producto_id>/vincular/<int:observer_id>', methods=['POST'])
    def producto_vincular(producto_id, observer_id):
        """Setea manualmente el observer_id de un producto."""
        with database.get_db() as session:
            p = session.get(database.Producto, producto_id)
            obs = session.get(database.ObsProducto, observer_id)
            if not p or not obs:
                flash('Producto u obs_producto no encontrado.', 'error')
            else:
                p.observer_id = observer_id
                session.commit()
                flash(f'Vinculado: {p.descripcion[:50]} → {obs.descripcion[:50]}', 'success')
        return redirect(url_for('productos_sin_vincular'))

    @app.route('/admin/observer-config', methods=['POST'])
    def observer_config_save():
        """Guarda Config.observer_ventas_meses."""
        try:
            meses = max(1, min(120, int(request.form.get('meses', '16'))))
        except ValueError:
            meses = 16
        with database.get_db() as session:
            cfg = session.query(database.Config).first()
            if cfg is None:
                cfg = database.Config(id=1, farmacia_nombre='Farmacia')
                session.add(cfg)
            cfg.observer_ventas_meses = meses
            session.commit()
        flash(f'Config: sync_ventas_mensuales traerá {meses} meses.', 'success')
        return redirect(url_for('observer_sync_panel'))

    @app.route('/admin/observer-push-render', methods=['POST'])
    def observer_push_render():
        """Replica las tablas obs_* + productos.observer_id a la DB de Render."""
        import os
        from scripts.push_obs_to_render import push
        render_url = os.environ.get('RENDER_DATABASE_URL', '').strip()
        if not render_url:
            flash('Falta RENDER_DATABASE_URL en el .env (External URL de Render).', 'error')
            return redirect(url_for('observer_sync_panel'))
        try:
            res = push(render_url=render_url)
            partes = []
            for k, v in res.items():
                if isinstance(v, dict):
                    partes.append(f"{k}: {v['filas']} ({v['ms']} ms)")
            partes.append(f"total {res['TOTAL_MS']} ms")
            flash('Push a Render — ' + ' · '.join(partes), 'success')
        except Exception as e:
            flash(f'Error al pushear: {e}', 'error')
        return redirect(url_for('observer_sync_panel'))

    @app.route('/producto/<int:producto_id>/desvincular', methods=['POST'])
    def producto_desvincular(producto_id):
        """Remueve el observer_id de un producto (lo vuelve a mandar al bucket sin vincular)."""
        with database.get_db() as session:
            p = session.get(database.Producto, producto_id)
            if p:
                p.observer_id = None
                session.commit()
                flash('Desvinculado.', 'success')
        return redirect(url_for('productos_sin_vincular'))

    @app.route('/api/auto-sync', methods=['POST'])
    def api_auto_sync():
        """Endpoint JSON para el DockerPanel (cron automático).

        Ejecuta en cascada: sync ObServer → productos local → push a Render.
        Devuelve JSON con el detalle de cada paso. Protegido por lock para
        evitar ejecuciones paralelas.

        Query params opcionales:
          - skip_push=1    → no pushear a Render (solo sync local)
          - skip_match=1   → no correr auto-matcher

        Header opcional de autenticación:
          - X-Auto-Sync-Token    → si está seteada AUTO_SYNC_TOKEN env var
        """
        # Autenticación simple por token (evita que cualquiera invoque el sync)
        expected = os.environ.get('AUTO_SYNC_TOKEN', '').strip()
        if expected:
            sent = request.headers.get('X-Auto-Sync-Token', '').strip()
            if sent != expected:
                return jsonify({'ok': False, 'error': 'token inválido'}), 401

        # Lock: si hay otro sync corriendo, rechazar
        if not _SYNC_LOCK.acquire(blocking=False):
            return jsonify({
                'ok': False, 'error': 'sync en curso',
                'ultimo_inicio': _SYNC_ESTADO['ultimo_inicio'].isoformat()
                                  if _SYNC_ESTADO['ultimo_inicio'] else None,
            }), 409

        try:
            _SYNC_ESTADO['en_curso'] = True
            _SYNC_ESTADO['ultimo_inicio'] = datetime.now()

            skip_push  = request.args.get('skip_push') == '1'
            skip_match = request.args.get('skip_match') == '1'

            resultado = {'pasos': [], 'inicio': _SYNC_ESTADO['ultimo_inicio'].isoformat()}

            # Paso 1: verificar que ObServer esté disponible
            if not observer_source.observer_disponible():
                resultado['pasos'].append({'paso': 'observer_ping',
                                            'ok': False, 'error': 'ObServer no disponible'})
                resultado['ok'] = False
                return jsonify(resultado), 503

            # Paso 2: sync entidades ObServer → DB local
            orden = ['laboratorios', 'rubros', 'subrubros', 'nombres_drogas',
                     'productos', 'stock', 'ventas_mensuales']
            funcs = {
                'laboratorios':     observer_source.sync_laboratorios,
                'rubros':           observer_source.sync_rubros,
                'subrubros':        observer_source.sync_subrubros,
                'nombres_drogas':   observer_source.sync_nombres_drogas,
                'productos':        observer_source.sync_productos,
                'stock':            observer_source.sync_stock,
                'ventas_mensuales': observer_source.sync_ventas_mensuales,
            }
            with database.get_db() as session:
                for ent in orden:
                    try:
                        stats = funcs[ent](session)
                        session.commit()
                        resultado['pasos'].append({
                            'paso': ent, 'ok': True,
                            'upsert': stats.get('upsert', 0),
                            'ms': stats.get('duracion_ms', 0),
                        })
                    except Exception as e:
                        session.rollback()
                        resultado['pasos'].append({'paso': ent, 'ok': False, 'error': str(e)})
                        resultado['ok'] = False
                        return jsonify(resultado), 500

            # Paso 3: auto-match productos (opcional)
            if not skip_match:
                try:
                    with database.get_db() as session:
                        stats = observer_matcher.match_productos(session, threshold=0.80)
                        session.commit()
                    resultado['pasos'].append({
                        'paso': 'match_productos', 'ok': True,
                        'linked_exact': stats.get('linked_exact', 0),
                        'linked_fuzzy': stats.get('linked_fuzzy', 0),
                        'sin_match':    stats.get('sin_match', 0),
                    })
                except Exception as e:
                    resultado['pasos'].append({'paso': 'match_productos', 'ok': False, 'error': str(e)})
                    # No abortamos si el match falla, seguimos con el push

            # Paso 4: push a Render (opcional)
            if not skip_push:
                render_url = os.environ.get('RENDER_DATABASE_URL', '').strip()
                if not render_url:
                    resultado['pasos'].append({
                        'paso': 'push_render', 'ok': False,
                        'error': 'RENDER_DATABASE_URL no configurada',
                    })
                else:
                    try:
                        from scripts.push_obs_to_render import push
                        res = push(render_url=render_url)
                        total_filas = sum(v['filas'] for v in res.values() if isinstance(v, dict))
                        resultado['pasos'].append({
                            'paso': 'push_render', 'ok': True,
                            'total_filas': total_filas,
                            'ms': res.get('TOTAL_MS', 0),
                        })
                    except Exception as e:
                        resultado['pasos'].append({'paso': 'push_render', 'ok': False, 'error': str(e)})
                        resultado['ok'] = False
                        return jsonify(resultado), 500

            resultado['ok'] = True
            resultado['fin'] = datetime.now().isoformat()
            _SYNC_ESTADO['ultimo_fin'] = datetime.now()
            _SYNC_ESTADO['ultimo_resultado'] = resultado
            return jsonify(resultado)

        finally:
            _SYNC_ESTADO['en_curso'] = False
            _SYNC_LOCK.release()

    @app.route('/api/auto-sync/status')
    def api_auto_sync_status():
        """Devuelve el estado del lock y última corrida (sin ejecutar nada)."""
        return jsonify({
            'en_curso': _SYNC_ESTADO['en_curso'],
            'ultimo_inicio': _SYNC_ESTADO['ultimo_inicio'].isoformat()
                              if _SYNC_ESTADO['ultimo_inicio'] else None,
            'ultimo_fin': _SYNC_ESTADO['ultimo_fin'].isoformat()
                           if _SYNC_ESTADO['ultimo_fin'] else None,
            'ultimo_resultado': _SYNC_ESTADO.get('ultimo_resultado'),
        })
