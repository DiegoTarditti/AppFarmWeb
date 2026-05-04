"""Rutas que consumen directamente la DB de ObServer (modo online).

Habilitadas solo para roles/usuarios con acceso online. No requieren subir archivos.
"""

import json
import os
import uuid
from datetime import datetime

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

import database
import observer_source
from auth import tiene_permiso
from database import AnalisisSesion, Pedido, PedidoItem
from helpers import PURCHASE_FOLDER, _upsert_producto, get_config, now_ar
from purchase_engine import analyze_purchase


def _user_tiene_observer(user):
    """Decide si el usuario accede a ObServer. Por ahora: rol farmacia, dev o admin."""
    if not user or not user.is_authenticated:
        return False
    return user.rol in ('farmacia', 'dev', 'admin')


def init_app(app):

    @app.route('/obs/producto/<int:observer_id>/descripcion', methods=['POST'])
    @login_required
    def obs_producto_descripcion(observer_id):
        """Edita la descripción local de un producto del catálogo Observer.
        Guarda en `obs_productos.descripcion_custom`. Si el valor coincide con
        la descripción original o queda vacío, limpia el campo (vuelve a
        mostrar la de Observer)."""
        nueva = (request.json.get('descripcion') or '').strip() if request.is_json \
                else (request.form.get('descripcion') or '').strip()
        with database.get_db() as session:
            p = session.get(database.ObsProducto, observer_id)
            if not p:
                return jsonify({'ok': False, 'error': 'Producto no encontrado'}), 404
            if not nueva or nueva == (p.descripcion or '').strip():
                p.descripcion_custom = None
            else:
                p.descripcion_custom = nueva[:200]
            session.commit()
            return jsonify({
                'ok': True,
                'descripcion': p.descripcion_custom or p.descripcion,
                'tiene_desc_custom': bool(p.descripcion_custom),
            })

    @app.route('/obs/productos')
    @login_required
    def obs_productos():
        """Catálogo completo ObServer (122k) con ventas + stock + laboratorio + monodroga.
        Solo lectura, paginado, sin tocar la tabla `productos` local."""
        from datetime import datetime

        from sqlalchemy import func as _f
        from sqlalchemy import or_ as _or
        q = (request.args.get('q') or '').strip()
        lab_id = request.args.get('lab_id', type=int)
        # Default: incluir TODOS (incluso los con fecha_baja). El usuario puede activar
        # "solo activos" si quiere filtrar discontinuados.
        solo_activos = request.args.get('solo_activos') == '1'
        # Filtro por tipo de venta y control: 'libre'=L, 'receta'=R/A, '' = todos.
        venta_tipo = (request.args.get('venta_tipo') or '').strip()
        try:
            page = max(1, int(request.args.get('page', '1')))
        except ValueError:
            page = 1
        per_page = 50
        offset = (page - 1) * per_page

        id_farmacia = int(os.environ.get('OBSERVER_ID_FARMACIA', '10525'))

        with database.get_db() as session:
            base = session.query(database.ObsProducto)
            if solo_activos:
                base = base.filter(database.ObsProducto.fecha_baja.is_(None))
            if q:
                from helpers import multi_token_filter
                clausula = multi_token_filter(q,
                    database.ObsProducto.descripcion,
                    database.ObsProducto.descripcion_custom,
                    database.ObsProducto.codigo_alfabeta)
                if clausula is not None:
                    base = base.filter(clausula)
            if lab_id:
                base = base.filter(database.ObsProducto.laboratorio_observer == lab_id)
            if venta_tipo == 'libre':
                base = base.filter(database.ObsProducto.id_tipo_venta_control == 'L')
            elif venta_tipo == 'receta':
                base = base.filter(database.ObsProducto.id_tipo_venta_control.in_(['R', 'A']))
            elif venta_tipo == 'controlado':
                base = base.filter(database.ObsProducto.id_tipo_venta_control.in_(['1','2','3','4','5','6','7','8']))

            total = base.count()
            productos = (base.order_by(database.ObsProducto.descripcion)
                         .offset(offset).limit(per_page).all())

            obs_ids = [p.observer_id for p in productos]

            # Resolver laboratorios y drogas en batch
            labs_map = dict(
                session.query(database.ObsLaboratorio.observer_id,
                              database.ObsLaboratorio.descripcion).all()
            )
            drogas_map = dict(
                session.query(database.ObsNombreDroga.observer_id,
                              database.ObsNombreDroga.descripcion).all()
            )

            # Stock actual + mínimo configurado
            stock_map = {}
            minimo_map = {}
            if obs_ids:
                for po, sa, mi in (session.query(
                        database.ObsStock.producto_observer,
                        database.ObsStock.stock_actual,
                        database.ObsStock.minimo)
                        .filter(database.ObsStock.id_farmacia == id_farmacia,
                                database.ObsStock.producto_observer.in_(obs_ids)).all()):
                    stock_map[po] = int(sa or 0)
                    if mi is not None:
                        minimo_map[po] = int(mi)

            # Agregados de ventas: total 3m y 12m
            ventas_3m = ventas_12m = {}
            if obs_ids:
                hoy = datetime.now()
                # Mes actual y 2 atrás = 3m; 11 atrás = 12m (ym como int yyyymm)
                def _ym_hace(n):
                    y, m = hoy.year, hoy.month - n
                    while m <= 0:
                        m += 12
                        y -= 1
                    return y * 100 + m
                desde_3m = _ym_hace(2)
                desde_12m = _ym_hace(11)
                hasta = hoy.year * 100 + hoy.month

                rows = (session.query(
                        database.ObsVentaMensual.producto_observer,
                        _f.sum(database.ObsVentaMensual.unidades).label('u'))
                        .filter(database.ObsVentaMensual.id_farmacia == id_farmacia,
                                database.ObsVentaMensual.producto_observer.in_(obs_ids),
                                (database.ObsVentaMensual.anio * 100 +
                                 database.ObsVentaMensual.mes).between(desde_3m, hasta))
                        .group_by(database.ObsVentaMensual.producto_observer).all())
                ventas_3m = {po: float(u or 0) for po, u in rows}

                rows = (session.query(
                        database.ObsVentaMensual.producto_observer,
                        _f.sum(database.ObsVentaMensual.unidades).label('u'))
                        .filter(database.ObsVentaMensual.id_farmacia == id_farmacia,
                                database.ObsVentaMensual.producto_observer.in_(obs_ids),
                                (database.ObsVentaMensual.anio * 100 +
                                 database.ObsVentaMensual.mes).between(desde_12m, hasta))
                        .group_by(database.ObsVentaMensual.producto_observer).all())
                ventas_12m = {po: float(u or 0) for po, u in rows}

            # EAN del bridge: preferimos Orden=1 vivo, después cualquier
            # otro Orden vivo, después fallback a productos local.
            # (Antes solo aceptábamos Orden=1 → quedaban "sin vincular"
            # productos que sí tienen EAN registrado pero con Orden=2+
            # o con fecha_baja en su Orden=1.)
            ean_map = {}
            if obs_ids:
                # Traemos todos los EANs vivos, ordenados por Orden asc.
                # Tomamos el primero (Orden más bajo) que aparezca por producto.
                rows_ean = (session.query(
                                database.ObsCodigoBarras.producto_observer,
                                database.ObsCodigoBarras.codigo_barras,
                                database.ObsCodigoBarras.orden,
                            )
                            .filter(database.ObsCodigoBarras.producto_observer.in_(obs_ids),
                                    database.ObsCodigoBarras.fecha_baja.is_(None))
                            .order_by(database.ObsCodigoBarras.orden.asc()).all())
                for (oid, cb, _orden) in rows_ean:
                    if oid not in ean_map and cb:
                        ean_map[oid] = cb

                # Fallback: agregar los que faltan vía bridge productos local
                ids_sin = [i for i in obs_ids if i not in ean_map]
                if ids_sin:
                    for (oid, cb) in session.query(
                        database.Producto.observer_id, database.Producto.codigo_barra
                    ).filter(database.Producto.observer_id.in_(ids_sin),
                             database.Producto.codigo_barra.isnot(None),
                             ~database.Producto.codigo_barra.like('OBS:%')).all():
                        if cb:
                            ean_map[oid] = cb

            data = []
            for p in productos:
                v3 = ventas_3m.get(p.observer_id, 0)
                v12 = ventas_12m.get(p.observer_id, 0)
                tvc = (p.id_tipo_venta_control or '').strip()
                desc_custom = (getattr(p, 'descripcion_custom', None) or '').strip()
                desc_mostrada = desc_custom or p.descripcion
                data.append({
                    'observer_id':  p.observer_id,
                    'ean':          ean_map.get(p.observer_id) or '',
                    'codigo_alfabeta': p.codigo_alfabeta or '',
                    'descripcion':         desc_mostrada,
                    'descripcion_obs':     p.descripcion,
                    'tiene_desc_custom':   bool(desc_custom),
                    'laboratorio':  labs_map.get(p.laboratorio_observer) or '—',
                    'monodroga':    drogas_map.get(p.nombre_droga_observer) or '',
                    'stock':        stock_map.get(p.observer_id, 0),
                    'minimo':       minimo_map.get(p.observer_id, 0),
                    'prom_3m':      round(v3 / 3, 1),
                    'prom_12m':     round(v12 / 12, 1),
                    'total_3m':     v3,
                    'total_12m':    v12,
                    'baja':         p.fecha_baja is not None,
                    'tvc':          tvc,
                    'es_libre':     tvc == 'L',
                    'es_receta':    tvc in ('R', 'A'),
                    'es_controlado': tvc in ('1','2','3','4','5','6','7','8'),
                })

            # Labs para el dropdown
            labs = (session.query(database.ObsLaboratorio)
                    .filter(database.ObsLaboratorio.fecha_baja.is_(None))
                    .order_by(database.ObsLaboratorio.descripcion).all())

        # Cuántos del page actual no tienen EAN. Útil para que el user
        # entienda por qué tantos dicen 'sin EAN' — sin contar todo el
        # catálogo (sería caro), solo lo visible.
        total_sin_ean = sum(1 for d in data if not d.get('ean'))

        last_page = max(1, (total + per_page - 1) // per_page)
        return render_template('obs_productos.html',
                               productos=data, total=total,
                               total_sin_ean=total_sin_ean,
                               page=page, last_page=last_page,
                               q=q, lab_id=lab_id, labs=labs,
                               solo_activos=solo_activos,
                               venta_tipo=venta_tipo,
                               per_page=per_page)

    @app.route('/estadisticas/drogas')
    @login_required
    def estadisticas_drogas():
        """Estadísticas de ventas agregadas por monodroga.

        Para cada droga muestra: #laboratorios que la ofrecen, #productos distintos,
        unidades 3m, unidades 12m y monto 12m. Paginado y con buscador por nombre."""
        from sqlalchemy import text as _text

        import matviews
        q = (request.args.get('q') or '').strip()
        try:
            page = max(1, int(request.args.get('page', '1')))
        except ValueError:
            page = 1
        per_page = 40
        offset = (page - 1) * per_page

        id_farmacia = int(os.environ.get('OBSERVER_ID_FARMACIA', '10525'))

        with database.get_db() as session:
            mv_estado = matviews.estado_matview(session, 'mv_stats_drogas')

            # Si la vista nunca se refrescó, hacer fallback al JOIN en vivo
            # (más lento pero garantiza datos correctos en el primer uso).
            if mv_estado['estado'] == 'nunca':
                return _estadisticas_drogas_live(session, q, page, per_page, offset,
                                                  id_farmacia, mv_estado)

            # Filtro de nombre: resolver IDs de drogas que matchean (multi-token AND).
            droga_ids_filtro = None
            if q:
                from helpers import multi_token_filter
                clausula = multi_token_filter(q, database.ObsNombreDroga.descripcion)
                if clausula is not None:
                    droga_ids_filtro = [r[0] for r in session.query(database.ObsNombreDroga.observer_id)
                                        .filter(clausula).all()]
                if droga_ids_filtro is not None and not droga_ids_filtro:
                    return render_template('estadisticas_drogas.html',
                                           drogas=[], total=0, page=1, last_page=1,
                                           q=q, per_page=per_page,
                                           mv_estado=mv_estado)

            params = {'id_farmacia': id_farmacia}
            where_extra = ''
            if droga_ids_filtro is not None:
                where_extra = 'AND droga_id = ANY(:droga_ids)'
                params['droga_ids'] = droga_ids_filtro

            total = session.execute(_text(f"""
                SELECT COUNT(*) FROM mv_stats_drogas
                WHERE id_farmacia = :id_farmacia {where_extra}
            """), params).scalar() or 0

            params['lim'] = per_page
            params['off'] = offset
            rows = session.execute(_text(f"""
                SELECT droga_id, labs, prods, u3m, u12m, m12m
                FROM mv_stats_drogas
                WHERE id_farmacia = :id_farmacia {where_extra}
                ORDER BY u12m DESC
                LIMIT :lim OFFSET :off
            """), params).fetchall()

            droga_ids = [r.droga_id for r in rows]
            nombres = dict(session.query(database.ObsNombreDroga.observer_id,
                                         database.ObsNombreDroga.descripcion)
                           .filter(database.ObsNombreDroga.observer_id.in_(droga_ids)).all()) if droga_ids else {}

            drogas = [{
                'id':    r.droga_id,
                'nombre': nombres.get(r.droga_id) or f'#{r.droga_id}',
                'labs':  int(r.labs or 0),
                'prods': int(r.prods or 0),
                'u3m':   float(r.u3m or 0),
                'u12m':  float(r.u12m or 0),
                'm12m':  float(r.m12m or 0),
            } for r in rows]

        last_page = max(1, (total + per_page - 1) // per_page)
        return render_template('estadisticas_drogas.html',
                               drogas=drogas, total=total,
                               page=page, last_page=last_page,
                               q=q, per_page=per_page,
                               mv_estado=mv_estado)

    def _estadisticas_drogas_live(session, q, page, per_page, offset, id_farmacia, mv_estado):
        """Fallback: query en vivo si la vista materializada nunca corrió.
        Más lento pero correcto."""
        from datetime import datetime as _dt

        from sqlalchemy import case as _case
        from sqlalchemy import func as _f
        hoy = _dt.now()

        def _ym(n):
            y, m = hoy.year, hoy.month - n
            while m <= 0:
                m += 12
                y -= 1
            return y * 100 + m
        desde_3m, desde_12m = _ym(2), _ym(11)
        hasta = hoy.year * 100 + hoy.month
        ym_expr = database.ObsVentaMensual.anio * 100 + database.ObsVentaMensual.mes

        base_q = (session.query(
                    database.ObsProducto.nombre_droga_observer.label('droga_id'),
                    _f.count(_f.distinct(database.ObsProducto.laboratorio_observer)).label('labs'),
                    _f.count(_f.distinct(database.ObsProducto.observer_id)).label('prods'),
                    _f.sum(_case(
                        (ym_expr.between(desde_3m, hasta), database.ObsVentaMensual.unidades),
                        else_=0,
                    )).label('u3m'),
                    _f.sum(database.ObsVentaMensual.unidades).label('u12m'),
                    _f.sum(database.ObsVentaMensual.monto).label('m12m'))
                .join(database.ObsVentaMensual,
                      database.ObsVentaMensual.producto_observer == database.ObsProducto.observer_id)
                .filter(database.ObsProducto.nombre_droga_observer.isnot(None),
                        database.ObsProducto.fecha_baja.is_(None),
                        database.ObsVentaMensual.id_farmacia == id_farmacia,
                        ym_expr.between(desde_12m, hasta))
                .group_by(database.ObsProducto.nombre_droga_observer))
        if q:
            from helpers import multi_token_filter
            clausula = multi_token_filter(q, database.ObsNombreDroga.descripcion)
            matching_ids = []
            if clausula is not None:
                matching_ids = [r[0] for r in session.query(database.ObsNombreDroga.observer_id)
                                .filter(clausula).all()]
            if not matching_ids:
                return render_template('estadisticas_drogas.html',
                                       drogas=[], total=0, page=1, last_page=1,
                                       q=q, per_page=per_page, mv_estado=mv_estado)
            base_q = base_q.filter(database.ObsProducto.nombre_droga_observer.in_(matching_ids))
        sub = base_q.subquery()
        total = session.query(_f.count()).select_from(sub).scalar() or 0
        rows = (base_q.order_by(_f.sum(database.ObsVentaMensual.unidades).desc())
                .offset(offset).limit(per_page).all())
        droga_ids = [r.droga_id for r in rows]
        nombres = dict(session.query(database.ObsNombreDroga.observer_id,
                                     database.ObsNombreDroga.descripcion)
                       .filter(database.ObsNombreDroga.observer_id.in_(droga_ids)).all()) if droga_ids else {}
        drogas = [{
            'id': r.droga_id, 'nombre': nombres.get(r.droga_id) or f'#{r.droga_id}',
            'labs': int(r.labs or 0), 'prods': int(r.prods or 0),
            'u3m': float(r.u3m or 0), 'u12m': float(r.u12m or 0),
            'm12m': float(r.m12m or 0),
        } for r in rows]
        last_page = max(1, (total + per_page - 1) // per_page)
        return render_template('estadisticas_drogas.html',
                               drogas=drogas, total=total,
                               page=page, last_page=last_page,
                               q=q, per_page=per_page, mv_estado=mv_estado)

    @app.route('/api/droga/<int:droga_id>/ventas-mensuales')
    @login_required
    def api_droga_ventas_mensuales(droga_id):
        """Devuelve totales mensuales (unidades, monto) de los últimos 12 meses
        agregando todos los productos de la droga."""
        from datetime import datetime

        from sqlalchemy import func as _f

        id_farmacia = int(os.environ.get('OBSERVER_ID_FARMACIA', '10525'))
        hoy = datetime.now()

        # Generar (year, month) de los últimos 12 meses hasta el actual
        meses = []
        y, m = hoy.year, hoy.month
        for _ in range(12):
            meses.append((y, m))
            m -= 1
            if m == 0:
                m = 12
                y -= 1
        meses.reverse()
        desde_ym = meses[0][0] * 100 + meses[0][1]
        hasta_ym = meses[-1][0] * 100 + meses[-1][1]

        with database.get_db() as session:
            nombre_row = session.get(database.ObsNombreDroga, droga_id)
            nombre = nombre_row.descripcion if nombre_row else f'#{droga_id}'

            ym_expr = database.ObsVentaMensual.anio * 100 + database.ObsVentaMensual.mes
            rows = (session.query(
                        database.ObsVentaMensual.anio,
                        database.ObsVentaMensual.mes,
                        _f.sum(database.ObsVentaMensual.unidades).label('u'),
                        _f.sum(database.ObsVentaMensual.monto).label('m'))
                    .join(database.ObsProducto,
                          database.ObsProducto.observer_id == database.ObsVentaMensual.producto_observer)
                    .filter(database.ObsProducto.nombre_droga_observer == droga_id,
                            database.ObsVentaMensual.id_farmacia == id_farmacia,
                            ym_expr.between(desde_ym, hasta_ym))
                    .group_by(database.ObsVentaMensual.anio, database.ObsVentaMensual.mes).all())

            datos = {(r.anio, r.mes): (float(r.u or 0), float(r.m or 0)) for r in rows}

        labels = [f'{m:02d}/{y}' for (y, m) in meses]
        unidades = [datos.get((y, m), (0, 0))[0] for (y, m) in meses]
        monto = [datos.get((y, m), (0, 0))[1] for (y, m) in meses]
        return jsonify({'nombre': nombre, 'labels': labels,
                        'unidades': unidades, 'monto': monto})

    @app.route('/api/droga/<int:droga_id>/productos')
    @login_required
    def api_droga_productos(droga_id):
        """Devuelve los productos de la droga, agrupados por laboratorio,
        con stock actual y unidades 3m/12m."""
        from datetime import datetime

        from sqlalchemy import func as _f

        id_farmacia = int(os.environ.get('OBSERVER_ID_FARMACIA', '10525'))
        hoy = datetime.now()

        def _ym_hace(n):
            y, m = hoy.year, hoy.month - n
            while m <= 0:
                m += 12
                y -= 1
            return y * 100 + m
        desde_3m = _ym_hace(2)
        desde_12m = _ym_hace(11)
        hasta = hoy.year * 100 + hoy.month

        with database.get_db() as session:
            # Incluye también las bajas — se marcan con flag para que el front las muestre.
            productos = (session.query(database.ObsProducto)
                         .filter(database.ObsProducto.nombre_droga_observer == droga_id)
                         .order_by(database.ObsProducto.descripcion).all())

            obs_ids = [p.observer_id for p in productos]
            if not obs_ids:
                return jsonify({'grupos': []})

            labs_map = dict(session.query(database.ObsLaboratorio.observer_id,
                                          database.ObsLaboratorio.descripcion).all())

            stock_map = dict(session.query(database.ObsStock.producto_observer,
                                           database.ObsStock.stock_actual)
                             .filter(database.ObsStock.id_farmacia == id_farmacia,
                                     database.ObsStock.producto_observer.in_(obs_ids)).all())

            ym_expr = database.ObsVentaMensual.anio * 100 + database.ObsVentaMensual.mes
            # Trae unidades + monto en una sola pasada por ventana
            rows_3m = {}
            for (pid, u, mt) in (session.query(
                    database.ObsVentaMensual.producto_observer,
                    _f.sum(database.ObsVentaMensual.unidades),
                    _f.sum(database.ObsVentaMensual.monto))
                .filter(database.ObsVentaMensual.id_farmacia == id_farmacia,
                        database.ObsVentaMensual.producto_observer.in_(obs_ids),
                        ym_expr.between(desde_3m, hasta))
                .group_by(database.ObsVentaMensual.producto_observer).all()):
                rows_3m[pid] = (float(u or 0), float(mt or 0))

            rows_12m = {}
            for (pid, u, mt) in (session.query(
                    database.ObsVentaMensual.producto_observer,
                    _f.sum(database.ObsVentaMensual.unidades),
                    _f.sum(database.ObsVentaMensual.monto))
                .filter(database.ObsVentaMensual.id_farmacia == id_farmacia,
                        database.ObsVentaMensual.producto_observer.in_(obs_ids),
                        ym_expr.between(desde_12m, hasta))
                .group_by(database.ObsVentaMensual.producto_observer).all()):
                rows_12m[pid] = (float(u or 0), float(mt or 0))

            # Agrupar por lab
            por_lab = {}
            for p in productos:
                lab_id = p.laboratorio_observer or 0
                lab_nombre = labs_map.get(lab_id) or '— sin lab —'
                por_lab.setdefault(lab_id, {'lab_id': lab_id, 'lab': lab_nombre, 'productos': []})
                u3m, _ = rows_3m.get(p.observer_id, (0.0, 0.0))
                u12m, m12m = rows_12m.get(p.observer_id, (0.0, 0.0))
                stock = int(stock_map.get(p.observer_id, 0) or 0)
                # Días de stock = stock / (u3m / 90). None si u3m=0.
                if u3m > 0:
                    dias_stock = stock / (u3m / 90)
                else:
                    dias_stock = None
                # Momentum % = (u3m*4 - u12m) / u12m * 100. None si u12m=0.
                if u12m > 0:
                    momentum_pct = (u3m * 4 - u12m) / u12m * 100
                else:
                    momentum_pct = None
                por_lab[lab_id]['productos'].append({
                    'observer_id':    p.observer_id,
                    'descripcion':    p.descripcion,
                    'baja':           p.fecha_baja is not None,
                    'stock':          stock,
                    'u3m':            u3m,
                    'u12m':           u12m,
                    'm12m':           m12m,
                    'precio_envase':  (m12m / u12m) if u12m > 0 else 0,
                    'dias_stock':     dias_stock,
                    'momentum_pct':   momentum_pct,
                })

            # Orden: labs con más productos arriba
            grupos = sorted(por_lab.values(), key=lambda g: -len(g['productos']))

        return jsonify({'grupos': grupos})

    @app.route('/api/droga/<int:droga_id>/comparar-labs')
    @login_required
    def api_droga_comparar_labs(droga_id):
        """Comparación detallada entre 2 (o más) laboratorios para una droga.

        Query params: labs=ID1,ID2[,ID3...]
        Devuelve por cada lab: nombre, n_productos, stock_total, uni/monto 3m y 12m,
        precio_promedio, serie mensual 12m, top 5 productos por unidades."""
        from datetime import datetime

        from sqlalchemy import func as _f

        labs_raw = (request.args.get('labs') or '').strip()
        try:
            lab_ids = [int(x) for x in labs_raw.split(',') if x.strip()]
        except ValueError:
            return jsonify({'error': 'labs inválidos'}), 400
        if len(lab_ids) < 2:
            return jsonify({'error': 'Se requieren al menos 2 laboratorios'}), 400

        id_farmacia = int(os.environ.get('OBSERVER_ID_FARMACIA', '10525'))
        hoy = datetime.now()

        # Meses de los últimos 12
        meses = []
        y, m = hoy.year, hoy.month
        for _ in range(12):
            meses.append((y, m))
            m -= 1
            if m == 0:
                m = 12
                y -= 1
        meses.reverse()
        desde_12m = meses[0][0] * 100 + meses[0][1]
        hasta = meses[-1][0] * 100 + meses[-1][1]

        def _ym_hace(n):
            yy, mm = hoy.year, hoy.month - n
            while mm <= 0:
                mm += 12
                yy -= 1
            return yy * 100 + mm
        desde_3m = _ym_hace(2)

        with database.get_db() as session:
            nombre_droga = session.get(database.ObsNombreDroga, droga_id)
            nombre_droga = nombre_droga.descripcion if nombre_droga else f'#{droga_id}'

            labs_map = dict(session.query(database.ObsLaboratorio.observer_id,
                                          database.ObsLaboratorio.descripcion)
                            .filter(database.ObsLaboratorio.observer_id.in_(lab_ids)).all())

            resultados = []
            ym_expr = database.ObsVentaMensual.anio * 100 + database.ObsVentaMensual.mes

            for lab_id in lab_ids:
                # Productos del lab para esta droga
                prods = (session.query(database.ObsProducto)
                         .filter(database.ObsProducto.nombre_droga_observer == droga_id,
                                 database.ObsProducto.laboratorio_observer == lab_id,
                                 database.ObsProducto.fecha_baja.is_(None))
                         .order_by(database.ObsProducto.descripcion).all())
                prod_ids = [p.observer_id for p in prods]

                if not prod_ids:
                    resultados.append({
                        'lab_id': lab_id,
                        'lab_nombre': labs_map.get(lab_id) or f'#{lab_id}',
                        'n_productos': 0, 'stock_total': 0,
                        'u3m': 0, 'u12m': 0, 'm3m': 0, 'm12m': 0,
                        'precio_prom': 0,
                        'serie_labels': [f'{m:02d}/{y}' for (y, m) in meses],
                        'serie_unidades': [0] * 12, 'serie_monto': [0] * 12,
                        'top_productos': [],
                    })
                    continue

                # Stock
                stock_total = (session.query(_f.coalesce(_f.sum(database.ObsStock.stock_actual), 0))
                               .filter(database.ObsStock.id_farmacia == id_farmacia,
                                       database.ObsStock.producto_observer.in_(prod_ids)).scalar()) or 0

                # Agregados 3m / 12m
                agg_12m = session.query(
                    _f.coalesce(_f.sum(database.ObsVentaMensual.unidades), 0),
                    _f.coalesce(_f.sum(database.ObsVentaMensual.monto), 0)
                ).filter(
                    database.ObsVentaMensual.id_farmacia == id_farmacia,
                    database.ObsVentaMensual.producto_observer.in_(prod_ids),
                    ym_expr.between(desde_12m, hasta)
                ).first()
                u12m, m12m = float(agg_12m[0] or 0), float(agg_12m[1] or 0)

                agg_3m = session.query(
                    _f.coalesce(_f.sum(database.ObsVentaMensual.unidades), 0),
                    _f.coalesce(_f.sum(database.ObsVentaMensual.monto), 0)
                ).filter(
                    database.ObsVentaMensual.id_farmacia == id_farmacia,
                    database.ObsVentaMensual.producto_observer.in_(prod_ids),
                    ym_expr.between(desde_3m, hasta)
                ).first()
                u3m, m3m = float(agg_3m[0] or 0), float(agg_3m[1] or 0)

                # Serie mensual 12m
                rows = (session.query(
                            database.ObsVentaMensual.anio,
                            database.ObsVentaMensual.mes,
                            _f.sum(database.ObsVentaMensual.unidades),
                            _f.sum(database.ObsVentaMensual.monto))
                        .filter(database.ObsVentaMensual.id_farmacia == id_farmacia,
                                database.ObsVentaMensual.producto_observer.in_(prod_ids),
                                ym_expr.between(desde_12m, hasta))
                        .group_by(database.ObsVentaMensual.anio, database.ObsVentaMensual.mes).all())
                serie = {(a, mm): (float(u or 0), float(mt or 0)) for (a, mm, u, mt) in rows}

                # Top 5 productos por unidades 12m (incluye monto para precio)
                top_rows = (session.query(
                                database.ObsVentaMensual.producto_observer,
                                _f.sum(database.ObsVentaMensual.unidades),
                                _f.sum(database.ObsVentaMensual.monto))
                            .filter(database.ObsVentaMensual.id_farmacia == id_farmacia,
                                    database.ObsVentaMensual.producto_observer.in_(prod_ids),
                                    ym_expr.between(desde_12m, hasta))
                            .group_by(database.ObsVentaMensual.producto_observer)
                            .order_by(_f.sum(database.ObsVentaMensual.unidades).desc())
                            .limit(5).all())
                desc_map = {p.observer_id: p.descripcion for p in prods}
                cant_envase_map = {p.observer_id: (float(p.cantidad_envase) if p.cantidad_envase else None)
                                   for p in prods}
                top_productos = []
                for (pid, u, mt) in top_rows:
                    u_f = float(u or 0)
                    m_f = float(mt or 0)
                    ce = cant_envase_map.get(pid)
                    precio_envase = (m_f / u_f) if u_f > 0 else 0
                    precio_unidad = (m_f / (u_f * ce)) if (u_f > 0 and ce and ce > 0) else 0
                    top_productos.append({
                        'descripcion': desc_map.get(pid, f'#{pid}'),
                        'u12m': u_f,
                        'm12m': m_f,
                        'cantidad_envase': ce,
                        'precio_envase': precio_envase,
                        'precio_unidad': precio_unidad,
                    })

                # Precio promedio por unidad de contenido: suma(monto)/suma(unidades*cantidad_envase)
                # Solo contamos productos con cantidad_envase > 0 para evitar sesgo.
                # Pre-2026-05: este bloque hacía 1 query por producto (loop sobre prod_ids)
                # → con 5 labs × 20 productos = 100 queries solo acá. Optimizado a 1 query
                # group by que devuelve sum(unidades), sum(monto) por producto, y la
                # ponderación con cantidad_envase se hace en Python.
                u_contenido_total = 0.0
                m_contenido_total = 0.0
                tot_rows = (session.query(
                                database.ObsVentaMensual.producto_observer,
                                _f.coalesce(_f.sum(database.ObsVentaMensual.unidades), 0),
                                _f.coalesce(_f.sum(database.ObsVentaMensual.monto), 0))
                            .filter(database.ObsVentaMensual.id_farmacia == id_farmacia,
                                    database.ObsVentaMensual.producto_observer.in_(prod_ids),
                                    ym_expr.between(desde_12m, hasta))
                            .group_by(database.ObsVentaMensual.producto_observer).all())
                for (pid, u_p, m_p) in tot_rows:
                    ce = cant_envase_map.get(pid)
                    if ce and ce > 0 and u_p:
                        u_contenido_total += float(u_p) * ce
                        m_contenido_total += float(m_p)
                precio_unidad = (m_contenido_total / u_contenido_total) if u_contenido_total > 0 else 0

                resultados.append({
                    'lab_id': lab_id,
                    'lab_nombre': labs_map.get(lab_id) or f'#{lab_id}',
                    'n_productos': len(prods),
                    'stock_total': int(stock_total),
                    'u3m': u3m, 'u12m': u12m,
                    'm3m': m3m, 'm12m': m12m,
                    'precio_prom_envase': (m12m / u12m) if u12m > 0 else 0,
                    'precio_prom_unidad': precio_unidad,
                    'serie_labels': [f'{m:02d}/{y}' for (y, m) in meses],
                    'serie_unidades': [serie.get((y, m), (0, 0))[0] for (y, m) in meses],
                    'serie_monto':    [serie.get((y, m), (0, 0))[1] for (y, m) in meses],
                    'top_productos': top_productos,
                })

        return jsonify({'droga_id': droga_id, 'droga_nombre': nombre_droga,
                        'labs': resultados})

    @app.route('/api/mv/refresh/<view_name>', methods=['POST'])
    @login_required
    def api_mv_refresh(view_name):
        """Refresca una vista materializada manualmente. Solo admin/dev."""
        if current_user.rol not in ('admin', 'dev'):
            return jsonify({'ok': False, 'error': 'sin permisos'}), 403
        import cron_log
        import matviews
        if view_name not in matviews.MATVIEWS:
            return jsonify({'ok': False, 'error': 'vista desconocida'}), 404
        try:
            with cron_log.registrar(f'mv_refresh:{view_name}', origen='web') as log:
                with database.get_db() as session:
                    r = matviews.refrescar_matview(session, view_name)
                log.set_mensaje(f'{r.get("filas", 0)} filas en {r.get("duracion_ms", 0)}ms')
                if not r.get('ok'):
                    log.set_mensaje(f'ERROR: {r.get("error", "refresh falló")}')
            # Devolvemos siempre JSON — incluso si el refresh falló (con ok=False).
            return jsonify(r), (200 if r.get('ok') else 500)
        except Exception as e:
            return jsonify({'ok': False, 'error': f'{type(e).__name__}: {e}'}), 500

    @app.route('/api/mv/status')
    @login_required
    def api_mv_status():
        """Estado de cada vista materializada (último refresh, edad, filas)."""
        import matviews
        with database.get_db() as session:
            return jsonify(matviews.estado_todas_matviews(session))

    @app.route('/api/sync-status')
    @login_required
    def api_sync_status():
        """Estado de frescura de cada sync de ObServer.

        Devuelve: { entidades: {...}, peor_estado: 'ok|warning|error|nunca|externo',
                    cualquier_atrasado: bool }
        """
        with database.get_db() as session:
            estados = observer_source.estado_syncs(session)

        # Peor estado para el badge global (orden de severidad)
        prioridad = {'error': 4, 'nunca': 3, 'warning': 2, 'externo': 1, 'ok': 0}
        peor = 'ok'
        for e in estados.values():
            if prioridad.get(e['estado'], 0) > prioridad.get(peor, 0):
                peor = e['estado']

        cualquier_atrasado = any(e['estado'] in ('error', 'warning', 'nunca')
                                  for e in estados.values())
        return jsonify({
            'entidades':           estados,
            'peor_estado':         peor,
            'cualquier_atrasado':  cualquier_atrasado,
        })

    @app.route('/api/diagnose-eans')
    @login_required
    def api_diagnose_eans():
        """Diagnóstico: cuántos productos del catálogo Observer tienen EAN
        registrado en obs_codigos_barras, agrupado por estado del Orden.

        Útil para entender por qué un producto X aparece como 'sin EAN'.
        Pasá ?nombre=AMOXIDAL para ver la situación específica de esos.
        """
        from sqlalchemy import func as _f
        nombre = (request.args.get('nombre') or '').strip()
        with database.get_db() as session:
            # Total productos activos
            base = session.query(database.ObsProducto).filter(
                database.ObsProducto.fecha_baja.is_(None))
            if nombre:
                base = base.filter(database.ObsProducto.descripcion.ilike(f'%{nombre}%'))
            total = base.count()
            obs_ids = [p[0] for p in base.with_entities(database.ObsProducto.observer_id).all()]
            if not obs_ids:
                return jsonify({'ok': True, 'total': 0, 'mensaje': 'Sin productos'})

            # Contar por estado en obs_codigos_barras
            con_ean_orden1_vivo = session.query(
                _f.count(_f.distinct(database.ObsCodigoBarras.producto_observer))
            ).filter(
                database.ObsCodigoBarras.producto_observer.in_(obs_ids),
                database.ObsCodigoBarras.orden == 1,
                database.ObsCodigoBarras.fecha_baja.is_(None),
            ).scalar() or 0

            con_ean_cualquier_orden_vivo = session.query(
                _f.count(_f.distinct(database.ObsCodigoBarras.producto_observer))
            ).filter(
                database.ObsCodigoBarras.producto_observer.in_(obs_ids),
                database.ObsCodigoBarras.fecha_baja.is_(None),
            ).scalar() or 0

            con_ean_baja = session.query(
                _f.count(_f.distinct(database.ObsCodigoBarras.producto_observer))
            ).filter(
                database.ObsCodigoBarras.producto_observer.in_(obs_ids),
                database.ObsCodigoBarras.fecha_baja.isnot(None),
            ).scalar() or 0

            # Total de filas en obs_codigos_barras (cobertura general).
            total_codigos_barras = session.query(database.ObsCodigoBarras).count()

            # Muestra: 5 productos del filtro y qué tienen.
            muestra = []
            for p in base.limit(5).all():
                rows = session.query(
                    database.ObsCodigoBarras.codigo_barras,
                    database.ObsCodigoBarras.orden,
                    database.ObsCodigoBarras.fecha_baja,
                ).filter(
                    database.ObsCodigoBarras.producto_observer == p.observer_id
                ).order_by(database.ObsCodigoBarras.orden).all()
                muestra.append({
                    'observer_id': p.observer_id,
                    'descripcion': p.descripcion,
                    'codigo_alfabeta': p.codigo_alfabeta,
                    'codigos_barras': [
                        {'codigo': r[0], 'orden': r[1],
                         'baja': bool(r[2])}
                        for r in rows
                    ],
                })

            return jsonify({
                'ok': True,
                'filtro_nombre': nombre or '(todos)',
                'total_productos': total,
                'con_ean_orden1_vivo': con_ean_orden1_vivo,
                'con_ean_cualquier_orden_vivo': con_ean_cualquier_orden_vivo,
                'con_ean_solo_baja': con_ean_baja,
                'sin_ningun_ean': total - con_ean_cualquier_orden_vivo,
                'pct_con_ean_vivo': round(con_ean_cualquier_orden_vivo / total * 100, 1) if total else 0,
                'total_filas_obs_codigos_barras_global': total_codigos_barras,
                'muestra_primeros_5': muestra,
            })

    @app.route('/observer/schema')
    @login_required
    def observer_schema():
        """Pantalla read-only que lista todas las tablas/views del schema DW
        de Observer con sus columnas y 5 filas de muestra. Sirve para
        descubrir qué hay disponible antes de armar nuevos syncs.
        """
        schema = (request.args.get('schema') or 'DW').strip()
        sample = request.args.get('sample', type=int) or 5
        focus = (request.args.get('table') or '').strip() or None
        error = None
        data = {'tables': [], 'errors': []}

        if not _user_tiene_observer(current_user):
            error = 'Tu usuario no tiene acceso a ObServer (necesita rol farmacia, dev o admin).'
        elif not observer_source.observer_disponible():
            error = (
                'ObServer no está disponible desde este servidor. '
                'Esto pasa cuando: (a) corrés en Render y la red no llega a 192.168.x — '
                'normal, ObServer es local de la farmacia; (b) las env vars '
                'OBSERVER_HOST/USER/PASS/DB no están configuradas; '
                '(c) la VPN o el firewall están bloqueando.'
            )
        else:
            try:
                data = observer_source.explorar_schema(schema=schema, sample_rows=sample, table=focus)
            except Exception as e:
                error = f'Error explorando schema: {e}'

        return render_template('observer_schema.html',
                               data=data, schema=schema, sample=sample,
                               focus=focus, error=error)

    @app.route('/observer/status')
    @login_required
    def observer_status():
        """Health check de la DB de ObServer."""
        return jsonify({
            'disponible': observer_source.observer_disponible(),
            'url_configurada': bool(os.environ.get('OBSERVER_DATABASE_URL')),
            'usuario_habilitado': _user_tiene_observer(current_user),
        })

    @app.route('/observer/analizar', methods=['GET', 'POST'])
    @login_required
    def observer_analizar():
        """Análisis de ventas consultando directo a ObServer (sin subir archivos)."""
        if not _user_tiene_observer(current_user):
            flash('Tu usuario no tiene acceso a ObServer.', 'error')
            return redirect(url_for('index'))
        if not observer_source.observer_analisis_disponible():
            flash('No hay datos de ventas para analizar. Corré el sync desde la PC de la farmacia.', 'error')
            return redirect(url_for('purchase_index'))

        if request.method == 'GET':
            labs = observer_source.get_laboratorios_disponibles()
            lab_preseleccionado = (request.args.get('lab') or '').strip()
            proceso_id = request.args.get('proceso', type=int)
            return render_template('observer_analizar.html', laboratorios=labs,
                                   n_days_default=35,
                                   lab_preseleccionado=lab_preseleccionado,
                                   proceso_id=proceso_id,
                                   now=datetime.now())

        # POST: ejecutar análisis
        laboratorio = (request.form.get('laboratorio') or '').strip()
        try:
            n_days = max(1, min(365, int(request.form.get('n_days', 35))))
        except (ValueError, TypeError):
            n_days = 35
        try:
            anio = int(request.form.get('anio_hasta') or datetime.now().year)
            mes = int(request.form.get('mes_hasta') or datetime.now().month)
        except (ValueError, TypeError):
            hoy = datetime.now()
            anio, mes = hoy.year, hoy.month

        if not laboratorio:
            flash('Elegí un laboratorio.', 'error')
            return redirect(url_for('observer_analizar'))

        productos = observer_source.get_ventas_laboratorio(laboratorio, anio, mes)
        if not productos:
            flash(f'Sin datos de ventas para "{laboratorio}" en ese período.', 'warning')
            return redirect(url_for('observer_analizar'))

        # Calcular start_month (el primero de los 12 meses hacia atrás)
        start_m = mes - 11
        start_y = anio
        while start_m <= 0:
            start_m += 12
            start_y -= 1
        end_m = mes

        cfg = get_config()
        results = analyze_purchase(
            productos, n_days, start_m, end_m,
            umbral_pico=cfg['umbral_pico'],
            umbral_baja=cfg['umbral_baja'],
            umbral_tendencia=cfg['umbral_tendencia'],
            rot_alta_min=cfg['rot_alta_min'],
            rot_media_min=cfg['rot_media_min'],
        )

        uid = str(uuid.uuid4())
        periodo_str = f'{start_m:02d}/{start_y} - {end_m:02d}/{anio}'
        data = {
            'uid': uid,
            'farmacia': current_user.nombre_completo or 'Farmacia',
            'laboratorio': laboratorio,
            'periodo': periodo_str,
            'start_month': start_m,
            'n_days': n_days,
            'umbral_tendencia': cfg['umbral_tendencia'],
            'rot_alta_min': cfg['rot_alta_min'],
            'rot_alta_tol': cfg['rot_alta_tol'],
            'rot_media_min': cfg['rot_media_min'],
            'rot_media_tol': cfg['rot_media_tol'],
            'rot_baja_tol': cfg['rot_baja_tol'],
            'products': results,
            'proceso_id': request.form.get('proceso_id', type=int),
        }

        # Registrar sesión con fuente='observer'
        with database.get_db() as session:
            sesion = AnalisisSesion(
                laboratorio_nombre=laboratorio,
                periodo=periodo_str,
                farmacia=data['farmacia'],
                n_days=n_days,
                fuente='observer',
                n_productos=len(results),
            )
            session.add(sesion)
            session.commit()
            data['sesion_id'] = sesion.id

        json_path = os.path.join(PURCHASE_FOLDER, f'{uid}.json')
        with open(json_path, 'w', encoding='utf-8') as jf:
            json.dump(data, jf, ensure_ascii=False)

        flash(f'Análisis de {laboratorio} completado desde ObServer.', 'success')
        return redirect(url_for('purchase_results', uid=uid))

    @app.route('/observer/factura/<int:invoice_id>/recepciones')
    @login_required
    def observer_recepciones_factura(invoice_id):
        """Devuelve las recepciones de una factura según ObServer para el cruce."""
        if not _user_tiene_observer(current_user):
            return jsonify({'ok': False, 'error': 'Sin acceso a ObServer'}), 403
        if not observer_source.observer_disponible():
            return jsonify({'ok': False, 'error': 'ObServer no disponible'}), 503
        with database.get_db() as session:
            inv = session.get(database.Invoice, invoice_id)
            if not inv:
                return jsonify({'ok': False, 'error': 'Factura no encontrada'}), 404
            items = observer_source.get_recepciones_factura(
                inv.numero_factura, inv.proveedor_cuit
            )
            return jsonify({'ok': True, 'items': items, 'count': len(items)})

    @app.route('/observer/factura/<int:invoice_id>/sync', methods=['POST'])
    @login_required
    def observer_sync_factura(invoice_id):
        """Trae las recepciones de ObServer usando el nro de comprobante indicado por el usuario.

        Por ahora el nro de comprobante de ObServer se pide manualmente al usuario
        (parámetro `comprobante`). Más adelante se resolverá de dónde sale automáticamente.
        """
        from data_extract import compare_invoice_vs_erp, save_differences, save_erp_to_db
        if not _user_tiene_observer(current_user):
            flash('Tu usuario no tiene acceso a ObServer.', 'error')
            return redirect(url_for('compare_view', invoice_id=invoice_id))
        if not observer_source.observer_disponible():
            flash('ObServer no está disponible en este momento.', 'error')
            return redirect(url_for('compare_view', invoice_id=invoice_id))

        comprobante = (request.form.get('comprobante') or '').strip()
        if not comprobante:
            flash('Ingresá el número de comprobante de recepción de ObServer.', 'error')
            return redirect(url_for('compare_view', invoice_id=invoice_id))

        with database.get_db() as session:
            inv = session.get(database.Invoice, invoice_id)
            if not inv:
                flash('Factura no encontrada.', 'error')
                return redirect(url_for('index'))
            recepciones = observer_source.get_recepciones_factura(
                comprobante, inv.proveedor_cuit
            )
            if not recepciones:
                flash(f'ObServer: sin recepciones para el comprobante "{comprobante}" '
                      f'(proveedor {inv.proveedor_cuit or "—"}).', 'warning')
                return redirect(url_for('compare_view', invoice_id=invoice_id))

            # Convertir recepciones de ObServer al formato erp_items
            erp_items = [{
                'codigo_barra': r['codigo_barra'],
                'descripcion': r['descripcion'],
                'cantidad': r['cantidad'],
                'precio_unitario': r['precio_unitario'] * r['cantidad']
                                    if r.get('precio_unitario') and r.get('cantidad') else 0,
            } for r in recepciones]

            save_erp_to_db(session, erp_items)
            differences = compare_invoice_vs_erp(session, invoice_id)
            save_differences(session, invoice_id, differences)

            flash(f'Sincronizado con ObServer (comprobante {comprobante}): '
                  f'{len(recepciones)} ítems cargados.', 'success')
        return redirect(url_for('compare_view', invoice_id=invoice_id))
