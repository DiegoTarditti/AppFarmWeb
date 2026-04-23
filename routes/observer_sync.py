"""Rutas admin para sincronizar el espejo local con ObServer."""
from flask import render_template, redirect, url_for, flash, request, jsonify
import database
import observer_source
import observer_matcher


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
        return render_template('admin_observer_sync.html',
                               cuentas=cuentas, ultimos=ultimos, disponible=disponible)

    @app.route('/admin/observer-sync/<entidad>', methods=['POST'])
    def observer_sync_run(entidad):
        """Dispara el sync de una entidad. entidad ∈ {laboratorios, rubros, subrubros,
        nombres_drogas, productos, stock, todo}."""
        if not observer_source.observer_disponible():
            flash('ObServer no está disponible.', 'error')
            return redirect(url_for('observer_sync_panel'))

        funcs_por_nombre = {
            'laboratorios':   observer_source.sync_laboratorios,
            'rubros':         observer_source.sync_rubros,
            'subrubros':      observer_source.sync_subrubros,
            'nombres_drogas': observer_source.sync_nombres_drogas,
            'productos':      observer_source.sync_productos,
            'stock':          observer_source.sync_stock,
        }

        # 'todo' corre en orden para respetar FKs
        orden = ['laboratorios', 'rubros', 'subrubros', 'nombres_drogas',
                 'productos', 'stock']

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
