"""Mis Informes — pantallas de cruce/análisis sobre el catálogo y las ventas.

Cada informe vive en su propia ruta + template. La pantalla `/informes` es
el índice con tarjetas para cada informe. Pensado para crecer agregando
más cruces sin romper la organización.

Informes implementados:
1. Labs por droga — "¿Qué labs fabrican esta droga y cuál vendo más?"

Pendientes (próximas iteraciones):
2. Drogas con un solo proveedor — alerta de dependencia.
4. Presentaciones por droga — qué tamaños venden más.
"""
from datetime import date

from flask import jsonify, render_template, request
from flask_login import login_required
from sqlalchemy import distinct, func

import database
from database import ObsLaboratorio, ObsNombreDroga, ObsProducto, ObsVentaMensual, Producto


def _ventana_12m():
    """Devuelve (desde_ym, hasta_ym) como ints YYYYMM para los últimos 12 meses."""
    hoy = date.today()
    hasta = hoy.year * 100 + hoy.month
    desde_y = hoy.year - 1
    desde_m = hoy.month + 1
    if desde_m > 12:
        desde_m -= 12
        desde_y += 1
    desde = desde_y * 100 + desde_m
    return desde, hasta


def init_app(app):

    @app.route('/informes')
    @login_required
    def informes_index():
        """Índice con tarjetas para cada informe disponible."""
        return render_template('informes_index.html')

    @app.route('/informes/labs-por-droga')
    @login_required
    def informe_labs_por_droga():
        """Informe #1: dada una droga, muestra todos los labs que la fabrican
        con sus productos y ventas 12m.

        Sin droga seleccionada, solo renderiza la pantalla con el buscador.
        """
        droga_id = request.args.get('droga_id', type=int)
        droga_nombre = None
        rows = []
        stats = {}

        if droga_id:
            with database.get_db() as session:
                droga = session.get(ObsNombreDroga, droga_id)
                if droga:
                    droga_nombre = droga.descripcion
                    desde, hasta = _ventana_12m()
                    # Para cada producto de la droga: lab + ventas 12m.
                    q = (session.query(
                            ObsLaboratorio.observer_id.label('lab_id'),
                            ObsLaboratorio.descripcion.label('lab_nombre'),
                            ObsProducto.observer_id.label('prod_id'),
                            ObsProducto.descripcion.label('prod_descripcion'),
                            ObsProducto.codigo_alfabeta,
                            ObsProducto.fecha_baja,
                            func.coalesce(func.sum(
                                ObsVentaMensual.unidades), 0).label('u12m'),
                            func.coalesce(func.sum(
                                ObsVentaMensual.monto), 0).label('m12m'),
                         )
                         .join(ObsLaboratorio,
                               ObsLaboratorio.observer_id == ObsProducto.laboratorio_observer)
                         .outerjoin(ObsVentaMensual,
                                    (ObsVentaMensual.producto_observer == ObsProducto.observer_id) &
                                    (ObsVentaMensual.anio * 100 + ObsVentaMensual.mes >= desde) &
                                    (ObsVentaMensual.anio * 100 + ObsVentaMensual.mes <= hasta))
                         .filter(ObsProducto.nombre_droga_observer == droga_id)
                         .group_by(
                            ObsLaboratorio.observer_id,
                            ObsLaboratorio.descripcion,
                            ObsProducto.observer_id,
                            ObsProducto.descripcion,
                            ObsProducto.codigo_alfabeta,
                            ObsProducto.fecha_baja,
                         )
                         .order_by(func.coalesce(
                            func.sum(ObsVentaMensual.unidades), 0).desc())
                    )
                    obs_prod_ids = []
                    for r in q.all():
                        rows.append({
                            'lab_id': r.lab_id,
                            'lab_nombre': r.lab_nombre,
                            'producto_id': r.prod_id,
                            'descripcion': r.prod_descripcion,
                            'codigo_alfabeta': r.codigo_alfabeta,
                            'baja': r.fecha_baja is not None,
                            'u12m': int(r.u12m or 0),
                            'm12m': float(r.m12m or 0),
                            'ean': None,    # se llena abajo si hay producto local
                        })
                        obs_prod_ids.append(r.prod_id)

                    # Mapear obs_producto → EAN del producto local (si existe).
                    # El endpoint /api/product/<ean>/chart espera EAN, no observer_id.
                    if obs_prod_ids:
                        ean_by_obs = dict(
                            session.query(Producto.observer_id, Producto.codigo_barra)
                            .filter(Producto.observer_id.in_(obs_prod_ids))
                            .all()
                        )
                        for r in rows:
                            r['ean'] = ean_by_obs.get(r['producto_id'])
                    # Agregados
                    total_u = sum(r['u12m'] for r in rows)
                    total_m = sum(r['m12m'] for r in rows)
                    labs = {r['lab_id']: r['lab_nombre'] for r in rows}
                    stats = {
                        'productos': len(rows),
                        'labs': len(labs),
                        'unidades_12m': total_u,
                        'monto_12m': total_m,
                    }
                    # Datos para gráficos
                    # 1. Donut: agregar por lab.
                    por_lab = {}
                    for r in rows:
                        por_lab.setdefault(r['lab_nombre'], 0)
                        por_lab[r['lab_nombre']] += r['u12m']
                    chart_donut = sorted(por_lab.items(), key=lambda kv: -kv[1])
                    # 2. Barras top 10 productos (por unidades).
                    top10 = sorted(rows, key=lambda r: -r['u12m'])[:10]
                    chart_top = [{
                        'label': r['descripcion'],
                        'lab': r['lab_nombre'],
                        'u12m': r['u12m'],
                    } for r in top10 if r['u12m'] > 0]

        return render_template('informes_labs_por_droga.html',
                               droga_id=droga_id,
                               droga_nombre=droga_nombre,
                               rows=rows,
                               stats=stats,
                               chart_donut=chart_donut if droga_nombre else [],
                               chart_top=chart_top if droga_nombre else [])

    @app.route('/informes/drogas-sin-alternativa')
    @login_required
    def informe_drogas_sin_alternativa():
        """Informe #2: drogas críticas (con pocos labs proveedores).

        Filtra solo drogas que tuvieron ventas en los últimos 12 meses
        (las que NO vendo no son urgentes). Ordena por unidades desc para
        que las críticas-y-vendidas estén arriba.

        Query param `max_labs` (default 1): cuántos labs como máximo se
        consideran 'pocos'. Si 1, son monopolios; si 2, también pares.
        """
        try:
            max_labs = max(1, min(5, int(request.args.get('max_labs', 1))))
        except (TypeError, ValueError):
            max_labs = 1

        desde, hasta = _ventana_12m()
        rows = []
        with database.get_db() as session:
            # Subquery: drogas con ventas 12m + total unidades.
            ventas_por_droga = (
                session.query(
                    ObsProducto.nombre_droga_observer.label('droga_id'),
                    func.sum(ObsVentaMensual.unidades).label('u12m'),
                    func.sum(ObsVentaMensual.monto).label('m12m'),
                )
                .join(ObsVentaMensual,
                      ObsVentaMensual.producto_observer == ObsProducto.observer_id)
                .filter(ObsProducto.nombre_droga_observer.isnot(None))
                .filter(ObsVentaMensual.anio * 100 + ObsVentaMensual.mes >= desde)
                .filter(ObsVentaMensual.anio * 100 + ObsVentaMensual.mes <= hasta)
                .group_by(ObsProducto.nombre_droga_observer)
                .subquery()
            )

            # Para cada droga con ventas: count(distinct lab) + nombre droga.
            q = (session.query(
                    ObsNombreDroga.observer_id.label('droga_id'),
                    ObsNombreDroga.descripcion.label('droga_nombre'),
                    func.count(distinct(ObsProducto.laboratorio_observer)).label('n_labs'),
                    func.count(distinct(ObsProducto.observer_id)).label('n_productos'),
                    ventas_por_droga.c.u12m,
                    ventas_por_droga.c.m12m,
                 )
                 .join(ObsProducto,
                       ObsProducto.nombre_droga_observer == ObsNombreDroga.observer_id)
                 .join(ventas_por_droga,
                       ventas_por_droga.c.droga_id == ObsNombreDroga.observer_id)
                 .filter(ObsProducto.fecha_baja.is_(None))   # solo activos
                 .group_by(
                    ObsNombreDroga.observer_id,
                    ObsNombreDroga.descripcion,
                    ventas_por_droga.c.u12m,
                    ventas_por_droga.c.m12m,
                 )
                 .having(func.count(distinct(ObsProducto.laboratorio_observer)) <= max_labs)
                 .order_by(ventas_por_droga.c.u12m.desc())
            )

            for r in q.all():
                # Si es monopolio (1 lab), traer el nombre del único proveedor.
                lab_unico = None
                if r.n_labs == 1:
                    lab = (session.query(ObsLaboratorio.descripcion)
                           .join(ObsProducto,
                                 ObsProducto.laboratorio_observer == ObsLaboratorio.observer_id)
                           .filter(ObsProducto.nombre_droga_observer == r.droga_id)
                           .filter(ObsProducto.fecha_baja.is_(None))
                           .first())
                    if lab:
                        lab_unico = lab[0]
                rows.append({
                    'droga_id': r.droga_id,
                    'droga_nombre': r.droga_nombre,
                    'n_labs': r.n_labs,
                    'n_productos': r.n_productos,
                    'lab_unico': lab_unico,
                    'u12m': int(r.u12m or 0),
                    'm12m': float(r.m12m or 0),
                })

        # Stats agregados
        total_drogas_criticas = len(rows)
        total_u = sum(r['u12m'] for r in rows)
        total_m = sum(r['m12m'] for r in rows)
        monopolios = sum(1 for r in rows if r['n_labs'] == 1)

        return render_template('informes_drogas_sin_alternativa.html',
                               max_labs=max_labs,
                               rows=rows,
                               stats={
                                   'criticas': total_drogas_criticas,
                                   'monopolios': monopolios,
                                   'unidades_12m': total_u,
                                   'monto_12m': total_m,
                               })

    @app.route('/api/observer-product/<int:observer_id>/chart')
    @login_required
    def api_observer_product_chart(observer_id):
        """Datos para el gráfico histórico, leídos desde obs_ventas_mensuales.

        No requiere que el producto esté en el catálogo local — solo necesita
        el observer_id. Útil para informes/listados que cruzan con ObServer
        directo y no pasan por Producto local.
        """
        from datetime import date as _date
        with database.get_db() as session:
            obs = session.get(ObsProducto, observer_id)
            if not obs:
                return jsonify({'ok': False, 'error': 'Producto ObServer no encontrado'}), 404

            # Construir array de 12 valores mensuales (mes -11 al mes actual).
            hoy = _date.today()
            ventas_arr = [0] * 12
            start_y = hoy.year
            start_m = hoy.month - 11
            while start_m <= 0:
                start_m += 12
                start_y -= 1
            ventas_rows = (session.query(ObsVentaMensual)
                           .filter(ObsVentaMensual.producto_observer == observer_id)
                           .all())
            for v in ventas_rows:
                # Calcular el slot 0..11 del array.
                offset = (v.anio - start_y) * 12 + (v.mes - start_m)
                if 0 <= offset <= 11:
                    ventas_arr[offset] += int(v.unidades or 0)

            # Stock: sumamos sobre todas las farmacias si hay datos.
            from database import ObsStock
            stock_total = (session.query(func.coalesce(func.sum(ObsStock.stock_actual), 0))
                           .filter(ObsStock.producto_observer == observer_id).scalar() or 0)

            no_cero = [v for v in ventas_arr if v > 0]
            avg = sum(no_cero) / len(no_cero) if no_cero else 0
            # Tendencia simple: pendiente lineal sobre los 12 meses.
            n = len(ventas_arr)
            xs = list(range(n))
            mean_x = sum(xs) / n
            mean_y = sum(ventas_arr) / n
            num = sum((xs[i] - mean_x) * (ventas_arr[i] - mean_y) for i in range(n))
            den = sum((x - mean_x) ** 2 for x in xs)
            slope = num / den if den else 0
            # Rotación rough: A si avg>=20, M si >=5, B si menos.
            if avg >= 20:
                rot = 'A'
            elif avg >= 5:
                rot = 'M'
            else:
                rot = 'B'

            return jsonify({
                'ok': True,
                'nombre': obs.descripcion or '',
                'codigo_barra': obs.codigo_alfabeta or str(observer_id),
                'ventas': ventas_arr,
                'avg_monthly': float(avg),
                'slope': float(slope),
                'stock': int(stock_total),
                'rotacion': rot,
                'tipo': 'N',
                'start_month': start_m,
                'n_days': 35,
                'sin_historial': not no_cero,
                'analizado_en': None,
            })

    @app.route('/api/informes/buscar-droga')
    @login_required
    def api_buscar_droga():
        """Autocomplete para el buscador de drogas. Devuelve top 20 que
        contengan el texto en la descripción, ordenadas alfabéticamente."""
        q = (request.args.get('q') or '').strip()
        if len(q) < 2:
            return jsonify({'items': []})
        with database.get_db() as session:
            results = (session.query(ObsNombreDroga)
                       .filter(ObsNombreDroga.descripcion.ilike(f'%{q}%'))
                       .order_by(ObsNombreDroga.descripcion)
                       .limit(20).all())
            items = [{'id': r.observer_id, 'descripcion': r.descripcion}
                     for r in results]
        return jsonify({'items': items})
