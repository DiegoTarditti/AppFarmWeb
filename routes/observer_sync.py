"""Rutas admin para sincronizar el espejo local con ObServer."""
import json
import os
from datetime import datetime, timedelta

from flask import flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import text as _text
from sqlalchemy.orm import joinedload

import cron_log
import database
import observer_matcher
import observer_source

# El sync se coordina vía la tabla `sync_lock` (singleton id=1) — un
# `threading.Lock` en memoria no funciona con `gunicorn --preload --workers 2`
# porque cada worker forkea su copia y dos workers pueden disparar en paralelo.
_SYNC_TIMEOUT_MIN = 60  # si pasaron > 60 min sin liberar → lock abandonado


def _sync_lock_acquire():
    """Atomic acquire del lock de sync. True si se tomó; False si ya está
    tomado por otro worker dentro del timeout."""
    with database.get_db() as session:
        # Asegurar que la fila singleton exista.
        if session.query(database.SyncLock).filter_by(id=1).first() is None:
            session.add(database.SyncLock(id=1, en_curso=False))
            session.commit()
        umbral = datetime.now() - timedelta(minutes=_SYNC_TIMEOUT_MIN)
        # UPDATE atómico: solo lo toma si está libre o si quedó zombie hace rato.
        # rowcount=1 = lo agarramos; rowcount=0 = otro lo tiene.
        result = session.execute(_text(
            "UPDATE sync_lock SET en_curso = :on, iniciado_en = :now, "
            "finalizado_en = NULL, paso_actual = NULL, ultimo_resultado = NULL "
            "WHERE id = 1 AND (en_curso = :off OR iniciado_en IS NULL OR iniciado_en < :umbral)"
        ), {'on': True, 'off': False, 'now': datetime.now(), 'umbral': umbral})
        session.commit()
        return result.rowcount == 1


def _sync_lock_release(resultado=None):
    with database.get_db() as session:
        session.execute(_text(
            "UPDATE sync_lock SET en_curso = :off, finalizado_en = :now, "
            "paso_actual = NULL, ultimo_resultado = :res WHERE id = 1"
        ), {'off': False, 'now': datetime.now(),
            'res': json.dumps(resultado, default=str) if resultado else None})
        session.commit()


def _sync_lock_set_paso(paso):
    with database.get_db() as session:
        session.execute(_text(
            "UPDATE sync_lock SET paso_actual = :paso WHERE id = 1"
        ), {'paso': paso})
        session.commit()


def _sync_lock_estado():
    with database.get_db() as session:
        row = session.query(database.SyncLock).filter_by(id=1).first()
        if row is None:
            return {'en_curso': False, 'paso_actual': None,
                    'ultimo_inicio': None, 'ultimo_fin': None,
                    'ultimo_resultado': None}
        try:
            ult = json.loads(row.ultimo_resultado) if row.ultimo_resultado else None
        except (ValueError, TypeError):
            ult = None
        return {
            'en_curso': bool(row.en_curso),
            'paso_actual': row.paso_actual,
            'ultimo_inicio': row.iniciado_en.isoformat() if row.iniciado_en else None,
            'ultimo_fin': row.finalizado_en.isoformat() if row.finalizado_en else None,
            'ultimo_resultado': ult,
        }


def _contar_tablas_locales(tablas):
    """Cuenta filas por tabla en la DB local. Devuelve {tabla: int|None}."""
    out = {}
    with database.get_db() as session:
        for t in tablas:
            try:
                row = session.execute(_text(f'SELECT COUNT(*) FROM {t}')).fetchone()
                out[t] = int(row[0]) if row else 0
            except Exception:
                out[t] = None  # tabla no existe en local
    return out


def _contar_tablas_render(tablas):
    """Cuenta filas por tabla en la DB de Render. Devuelve {tabla: int|None}.

    Si RENDER_DATABASE_URL no está configurada, devuelve {} (la UI muestra '—').
    """
    render_url = os.environ.get('RENDER_DATABASE_URL', '').strip()
    if not render_url:
        return {}
    if render_url.startswith('postgres://'):
        render_url = render_url.replace('postgres://', 'postgresql://', 1)

    import psycopg2
    out = {}
    try:
        with psycopg2.connect(render_url, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                for t in tablas:
                    try:
                        cur.execute(f'SELECT COUNT(*) FROM {t}')
                        row = cur.fetchone()
                        out[t] = int(row[0]) if row else 0
                    except Exception:
                        out[t] = None  # tabla no existe en Render
                    finally:
                        conn.rollback()  # evitar quedar en tx abortada
    except Exception:
        return {}
    return out


def init_app(app):

    @app.route('/admin/sync-audit')
    def sync_audit_panel():
        """Auditoría de sincronización: matriz local vs Render por tabla.

        Lee la matriz declarada en sync_registry.REGISTRY y consulta los
        conteos en vivo. Si RENDER_DATABASE_URL no está, columna Render
        queda con guion.
        """
        from collections import defaultdict

        import sync_registry
        tablas = [t for t, _, _ in sync_registry.REGISTRY]
        counts_local  = _contar_tablas_locales(tablas)
        counts_render = _contar_tablas_render(tablas)
        render_disponible = bool(counts_render)

        rows = []
        resumen = defaultdict(lambda: {'tablas': 0, 'filas_local': 0, 'filas_render': 0})
        for tabla, categoria, descripcion in sync_registry.REGISTRY:
            cl = counts_local.get(tabla)
            cr = counts_render.get(tabla) if render_disponible else None
            # Diff = "alarma" solo si la tabla debería estar sincronizada y los conteos difieren > 5%.
            diff_pct = None
            alarma = False
            if categoria in ('push_obs', 'push_master') and render_disponible:
                if cl is None or cr is None:
                    alarma = True
                elif cl == 0 and cr == 0:
                    alarma = False
                elif cl == 0 or cr == 0:
                    alarma = True
                    diff_pct = 100
                else:
                    diff_pct = abs(cl - cr) / max(cl, cr) * 100
                    alarma = diff_pct > 5
            rows.append({
                'tabla': tabla,
                'categoria': categoria,
                'descripcion': descripcion,
                'count_local':  cl,
                'count_render': cr,
                'diff_pct': diff_pct,
                'alarma': alarma,
            })
            resumen[categoria]['tablas'] += 1
            if cl is not None:
                resumen[categoria]['filas_local'] += cl
            if cr is not None:
                resumen[categoria]['filas_render'] += cr

        return render_template('admin_sync_audit.html',
                               rows=rows,
                               resumen=dict(resumen),
                               labels=sync_registry.CATEGORIA_LABELS,
                               render_disponible=render_disponible)

    @app.route('/admin/observer-sync')
    def observer_sync_panel():
        """Dashboard de sync: cuenta de filas por entidad + última corrida."""
        with database.get_db() as session:
            cuentas = {
                'laboratorios':       session.query(database.ObsLaboratorio).count(),
                'rubros':             session.query(database.ObsRubro).count(),
                'subrubros':          session.query(database.ObsSubrubro).count(),
                'nombres_drogas':     session.query(database.ObsNombreDroga).count(),
                'productos':          session.query(database.ObsProducto).count(),
                'stock':              session.query(database.ObsStock).count(),
                'ventas_mensuales':   session.query(database.ObsVentaMensual).count(),
                'obras_sociales':     session.query(database.ObsObraSocial).count(),
                'convenios':          session.query(database.ObsConvenio).count(),
                'planes':             session.query(database.ObsPlan).count(),
                'colegios_medicos':   session.query(database.ObsColegioMedico).count(),
                'medicos':            session.query(database.ObsMedico).count(),
                'medicos_matriculas': session.query(database.ObsMedicoMatricula).count(),
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
            'laboratorios':         observer_source.sync_laboratorios,
            'rubros':               observer_source.sync_rubros,
            'subrubros':            observer_source.sync_subrubros,
            'nombres_drogas':       observer_source.sync_nombres_drogas,
            'productos':            observer_source.sync_productos,
            'stock':                observer_source.sync_stock,
            'ventas_mensuales':     observer_source.sync_ventas_mensuales,
            'grupos_clientes':      observer_source.sync_grupos_clientes,
            'categorias_clientes':  observer_source.sync_categorias_clientes,
            'obras_sociales':       observer_source.sync_obras_sociales,
            'convenios':            observer_source.sync_convenios,
            'planes':               observer_source.sync_planes,
            'clientes':             observer_source.sync_clientes,
            'colegios_medicos':     observer_source.sync_colegios_medicos,
            'medicos':              observer_source.sync_medicos,
            'medicos_matriculas':   observer_source.sync_medicos_matriculas,
            'ventas_detalle':       observer_source.sync_ventas_detalle,
        }

        # 'todo' corre en orden para respetar FKs
        orden = ['laboratorios', 'rubros', 'subrubros', 'nombres_drogas',
                 'productos', 'stock', 'ventas_mensuales',
                 'grupos_clientes', 'categorias_clientes',
                 'obras_sociales', 'convenios', 'planes', 'clientes',
                 'colegios_medicos', 'medicos', 'medicos_matriculas',
                 'ventas_detalle']

        if entidad == 'todo':
            ents = orden
        elif entidad in funcs_por_nombre:
            ents = [entidad]
        else:
            flash(f'Entidad desconocida: {entidad}', 'error')
            return redirect(url_for('observer_sync_panel'))

        resultados = []
        for ent in ents:
            try:
                with cron_log.registrar(f'sync_{ent}', origen='web') as log:
                    with database.get_db() as session:
                        stats = funcs_por_nombre[ent](session)
                        session.commit()
                    log.set_mensaje(f'{stats["upsert"]} filas en {stats["duracion_ms"]} ms')
                    resultados.append(
                        f'{ent}: {stats["upsert"]} filas ({stats["duracion_ms"]} ms)'
                    )
            except Exception as e:
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
              f'{stats["linked_exact"]} exactos · '
              f'{stats.get("linked_superset", 0)} superset · '
              f'{stats["linked_fuzzy"]} fuzzy · '
              f'{stats["ambiguos"]} ambiguos · {stats["sin_match"]} sin match · '
              f'{stats["sin_lab"]} sin laboratorio.', 'success')
        return redirect(url_for('observer_sync_panel'))

    @app.route('/productos/sin-vincular')
    def productos_sin_vincular():
        """Pantalla para vincular manualmente productos locales con obs_productos."""
        with database.get_db() as session:
            # joinedload evita N+1 sobre p.laboratorio (200 productos × 1 SELECT
            # cada uno al renderizar la tabla).
            productos = (session.query(database.Producto)
                         .options(joinedload(database.Producto.laboratorio))
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

    @app.route('/admin/push-productos-master', methods=['POST'])
    def push_productos_master():
        """Replica las tablas master (laboratorios + productos) local → Render.

        Distinto de observer_push_render: aquel sincroniza el espejo de ObServer
        (obs_*). Este sincroniza el catalogo MASTER de la app (productos que
        muestra /productos). Upsert por codigo_barra. NO toca tablas que
        dependan de productos.id.

        Diseñado para ser llamado desde el DockerPanel via panel remoto o por
        boton dedicado. Acepta header X-Auto-Sync-Token (mismo del auto-sync).
        Devuelve JSON con el resumen.
        """
        # Auth simple (reusa token del auto-sync).
        expected = os.environ.get('AUTO_SYNC_TOKEN', '').strip()
        if expected:
            sent = request.headers.get('X-Auto-Sync-Token', '').strip()
            if sent != expected:
                return jsonify({'ok': False, 'error': 'token invalido'}), 401

        render_url = os.environ.get('RENDER_DATABASE_URL', '').strip()
        if not render_url:
            return jsonify({
                'ok': False,
                'error': 'Falta RENDER_DATABASE_URL en el .env',
            }), 400

        from scripts.push_productos_master_to_render import push
        try:
            res = push(render_url=render_url)
            return jsonify({'ok': True, 'resultados': res})
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)}), 500

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
            with cron_log.registrar('push_render', origen='web') as log:
                res = push(render_url=render_url)
                partes = []
                total_filas = 0
                for k, v in res.items():
                    if isinstance(v, dict):
                        partes.append(f"{k}: {v['filas']} ({v['ms']} ms)")
                        total_filas += v.get('filas', 0)
                partes.append(f"total {res['TOTAL_MS']} ms")
                log.set_mensaje(f'{total_filas} filas en {res["TOTAL_MS"]} ms')
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

        # Lock atómico en DB — coordina entre workers de gunicorn.
        if not _sync_lock_acquire():
            estado = _sync_lock_estado()
            return jsonify({
                'ok': False, 'error': 'sync en curso',
                'ultimo_inicio': estado.get('ultimo_inicio'),
            }), 409

        resultado = {'pasos': []}
        try:
            inicio = datetime.now()

            skip_push  = request.args.get('skip_push') == '1'
            skip_match = request.args.get('skip_match') == '1'

            resultado['inicio'] = inicio.isoformat()

            # Paso 1: verificar que ObServer esté disponible
            if not observer_source.observer_disponible():
                resultado['pasos'].append({'paso': 'observer_ping',
                                            'ok': False, 'error': 'ObServer no disponible'})
                resultado['ok'] = False
                return jsonify(resultado), 503

            # Paso 2: sync entidades ObServer → DB local
            orden = ['laboratorios', 'rubros', 'subrubros', 'nombres_drogas',
                     'productos', 'stock', 'ventas_mensuales',
                     'grupos_clientes', 'categorias_clientes',
                     'obras_sociales', 'convenios', 'planes', 'clientes',
                     'colegios_medicos', 'medicos', 'medicos_matriculas',
                     'ventas_detalle']
            funcs = {
                'laboratorios':         observer_source.sync_laboratorios,
                'rubros':               observer_source.sync_rubros,
                'subrubros':            observer_source.sync_subrubros,
                'nombres_drogas':       observer_source.sync_nombres_drogas,
                'productos':            observer_source.sync_productos,
                'stock':                observer_source.sync_stock,
                'ventas_mensuales':     observer_source.sync_ventas_mensuales,
                'grupos_clientes':      observer_source.sync_grupos_clientes,
                'categorias_clientes':  observer_source.sync_categorias_clientes,
                'obras_sociales':       observer_source.sync_obras_sociales,
                'convenios':            observer_source.sync_convenios,
                'planes':               observer_source.sync_planes,
                'clientes':             observer_source.sync_clientes,
                'colegios_medicos':     observer_source.sync_colegios_medicos,
                'medicos':              observer_source.sync_medicos,
                'medicos_matriculas':   observer_source.sync_medicos_matriculas,
                'ventas_detalle':       observer_source.sync_ventas_detalle,
            }
            for ent in orden:
                _sync_lock_set_paso(ent)
                try:
                    with cron_log.registrar(f'sync_{ent}', origen='dockerpanel') as clog:
                        with database.get_db() as session:
                            stats = funcs[ent](session)
                            session.commit()
                        clog.set_mensaje(f'{stats.get("upsert", 0)} filas en {stats.get("duracion_ms", 0)} ms')
                    resultado['pasos'].append({
                        'paso': ent, 'ok': True,
                        'upsert': stats.get('upsert', 0),
                        'ms': stats.get('duracion_ms', 0),
                    })
                except Exception as e:
                    resultado['pasos'].append({'paso': ent, 'ok': False, 'error': str(e)})
                    resultado['ok'] = False
                    return jsonify(resultado), 500

            # Paso 3: auto-match productos (opcional)
            if not skip_match:
                _sync_lock_set_paso('match_productos')
                try:
                    with cron_log.registrar('match_productos', origen='dockerpanel') as clog:
                        with database.get_db() as session:
                            stats = observer_matcher.match_productos(session, threshold=0.80)
                            session.commit()
                        clog.set_mensaje(
                            f'exact={stats.get("linked_exact", 0)} '
                            f'super={stats.get("linked_superset", 0)} '
                            f'fuzzy={stats.get("linked_fuzzy", 0)} '
                            f'sin={stats.get("sin_match", 0)}'
                        )
                    resultado['pasos'].append({
                        'paso': 'match_productos', 'ok': True,
                        'linked_exact':    stats.get('linked_exact', 0),
                        'linked_superset': stats.get('linked_superset', 0),
                        'linked_fuzzy':    stats.get('linked_fuzzy', 0),
                        'sin_match':       stats.get('sin_match', 0),
                    })
                except Exception as e:
                    resultado['pasos'].append({'paso': 'match_productos', 'ok': False, 'error': str(e)})
                    # No abortamos si el match falla, seguimos con el push

            # Paso 4: push a Render (opcional)
            if not skip_push:
                _sync_lock_set_paso('push_render')
                render_url = os.environ.get('RENDER_DATABASE_URL', '').strip()
                if not render_url:
                    resultado['pasos'].append({
                        'paso': 'push_render', 'ok': False,
                        'error': 'RENDER_DATABASE_URL no configurada',
                    })
                else:
                    try:
                        with cron_log.registrar('push_render', origen='dockerpanel') as clog:
                            from scripts.push_obs_to_render import push
                            res = push(render_url=render_url)
                            total_filas = sum(v['filas'] for v in res.values() if isinstance(v, dict))
                            clog.set_mensaje(f'{total_filas} filas en {res.get("TOTAL_MS", 0)} ms')
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
            return jsonify(resultado)

        finally:
            _sync_lock_release(resultado=resultado)

    @app.route('/api/auto-sync/status')
    def api_auto_sync_status():
        """Devuelve el estado del lock y última corrida (sin ejecutar nada)."""
        return jsonify(_sync_lock_estado())
