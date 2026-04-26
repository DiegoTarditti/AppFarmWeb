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
from database import (ObsLaboratorio, ObsNombreDroga, ObsProducto, ObsStock,
                      ObsVentaMensual, Producto)


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

    @app.route('/informes/presentaciones-por-droga')
    @login_required
    def informe_presentaciones_por_droga():
        """Informe #3: para una droga elegida, agrupa los productos por
        presentación (dosis × cant por envase) y muestra qué se vende más.

        Útil para decidir qué stockear: ej. para AMOXICILINA, ¿se vende
        más x10 o x20? ¿La de 500mg o 750mg?
        """
        import re as _re

        droga_id = request.args.get('droga_id', type=int)
        droga_nombre = None
        presentaciones = []
        chart_data = None

        if droga_id:
            desde, hasta = _ventana_12m()
            with database.get_db() as session:
                droga = session.get(ObsNombreDroga, droga_id)
                if droga:
                    droga_nombre = droga.descripcion

                    # Traer todos los productos de la droga + su lab + ventas 12m.
                    q = (session.query(
                            ObsProducto.descripcion.label('desc'),
                            ObsProducto.cantidad_envase,
                            ObsLaboratorio.descripcion.label('lab'),
                            func.coalesce(func.sum(ObsVentaMensual.unidades), 0).label('u12m'),
                         )
                         .join(ObsLaboratorio,
                               ObsLaboratorio.observer_id == ObsProducto.laboratorio_observer)
                         .outerjoin(ObsVentaMensual,
                                    (ObsVentaMensual.producto_observer == ObsProducto.observer_id) &
                                    (ObsVentaMensual.anio * 100 + ObsVentaMensual.mes >= desde) &
                                    (ObsVentaMensual.anio * 100 + ObsVentaMensual.mes <= hasta))
                         .filter(ObsProducto.nombre_droga_observer == droga_id)
                         .filter(ObsProducto.fecha_baja.is_(None))
                         .group_by(
                            ObsProducto.descripcion,
                            ObsProducto.cantidad_envase,
                            ObsLaboratorio.descripcion,
                         ))

                    # Extraer "dosis mg" de la descripción + cantidad de envase.
                    # Combinarlos en una clave de presentación.
                    re_dosis = _re.compile(r'(\d+(?:[.,]\d+)?)\s*mg', _re.IGNORECASE)
                    by_pres = {}    # clave → {total, por_lab: {lab: unidades}}
                    for r in q.all():
                        m = re_dosis.search(r.desc or '')
                        dosis = m.group(1).replace(',', '.') if m else '?'
                        cant = int(r.cantidad_envase) if r.cantidad_envase else None
                        clave = f'{dosis} mg' + (f' × {cant}' if cant else '')
                        ent = by_pres.setdefault(clave, {
                            'presentacion': clave,
                            'dosis': dosis,
                            'cant_envase': cant,
                            'total_u12m': 0,
                            'por_lab': {},
                        })
                        ent['total_u12m'] += int(r.u12m or 0)
                        ent['por_lab'].setdefault(r.lab, 0)
                        ent['por_lab'][r.lab] += int(r.u12m or 0)

                    presentaciones = sorted(by_pres.values(),
                                             key=lambda p: -p['total_u12m'])

                    # Datos para el chart: barras agrupadas por presentación.
                    # Series = top labs (max 6 para no saturar). El resto se agrupa en "Otros".
                    todos_labs = {}
                    for p in presentaciones:
                        for lab, u in p['por_lab'].items():
                            todos_labs[lab] = todos_labs.get(lab, 0) + u
                    top_labs = sorted(todos_labs.items(), key=lambda kv: -kv[1])
                    series_labs = [l for l, _ in top_labs[:6]]
                    chart_data = {
                        'labels': [p['presentacion'] for p in presentaciones[:15]],
                        'series': [],
                    }
                    for lab in series_labs:
                        chart_data['series'].append({
                            'lab': lab,
                            'data': [p['por_lab'].get(lab, 0) for p in presentaciones[:15]],
                        })
                    if len(top_labs) > 6:
                        chart_data['series'].append({
                            'lab': 'Otros',
                            'data': [
                                sum(u for lab, u in p['por_lab'].items()
                                    if lab not in series_labs)
                                for p in presentaciones[:15]
                            ],
                        })

        return render_template('informes_presentaciones_por_droga.html',
                               droga_id=droga_id,
                               droga_nombre=droga_nombre,
                               presentaciones=presentaciones,
                               chart_data=chart_data)

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

    @app.route('/informes/bajo-minimo')
    @login_required
    def informe_bajo_minimo():
        """Análisis de mínimos #1: productos con stock_actual < minimo en
        ObServer (sumado por farmacia). Suma las ventas 12m para que el
        usuario priorice los de alta rotación.

        Filtros: lab (opcional), solo activos por defecto.
        """
        lab_id = request.args.get('lab_id', type=int)
        rows = []
        labs_disponibles = []
        with database.get_db() as session:
            stock_q = (
                session.query(
                    ObsStock.producto_observer.label('pid'),
                    func.sum(ObsStock.stock_actual).label('stock'),
                    func.sum(ObsStock.minimo).label('minimo'),
                )
                .filter(ObsStock.minimo.isnot(None))
                .filter(ObsStock.minimo > 0)
                .group_by(ObsStock.producto_observer)
                .subquery()
            )

            desde, hasta = _ventana_12m()
            ventas_sub = (
                session.query(
                    ObsVentaMensual.producto_observer.label('pid'),
                    func.sum(ObsVentaMensual.unidades).label('u12m'),
                )
                .filter(ObsVentaMensual.anio * 100 + ObsVentaMensual.mes >= desde)
                .filter(ObsVentaMensual.anio * 100 + ObsVentaMensual.mes <= hasta)
                .group_by(ObsVentaMensual.producto_observer)
                .subquery()
            )

            q = (session.query(
                    ObsProducto.observer_id.label('pid'),
                    ObsProducto.descripcion.label('desc'),
                    ObsProducto.codigo_alfabeta,
                    ObsLaboratorio.observer_id.label('lab_id'),
                    ObsLaboratorio.descripcion.label('lab_nombre'),
                    stock_q.c.stock,
                    stock_q.c.minimo,
                    func.coalesce(ventas_sub.c.u12m, 0).label('u12m'),
                 )
                 .join(stock_q, stock_q.c.pid == ObsProducto.observer_id)
                 .outerjoin(ObsLaboratorio,
                            ObsLaboratorio.observer_id == ObsProducto.laboratorio_observer)
                 .outerjoin(ventas_sub, ventas_sub.c.pid == ObsProducto.observer_id)
                 .filter(ObsProducto.fecha_baja.is_(None))
                 .filter(stock_q.c.stock < stock_q.c.minimo)
            )
            if lab_id:
                q = q.filter(ObsProducto.laboratorio_observer == lab_id)
            # Orden: por unidades faltantes (minimo - stock) desc, después por
            # ventas 12m desc para que los de alta rotación queden arriba.
            q = q.order_by(
                (stock_q.c.minimo - stock_q.c.stock).desc(),
                func.coalesce(ventas_sub.c.u12m, 0).desc(),
            )

            obs_ids = []
            for r in q.all():
                stock = int(r.stock or 0)
                minimo = int(r.minimo or 0)
                rows.append({
                    'producto_id': r.pid,
                    'descripcion': r.desc,
                    'codigo_alfabeta': r.codigo_alfabeta,
                    'lab_id': r.lab_id,
                    'lab_nombre': r.lab_nombre or '—',
                    'stock': stock,
                    'minimo': minimo,
                    'faltan': max(0, minimo - stock),
                    'u12m': int(r.u12m or 0),
                    'ean': None,
                })
                obs_ids.append(r.pid)

            if obs_ids:
                ean_by_obs = dict(
                    session.query(Producto.observer_id, Producto.codigo_barra)
                    .filter(Producto.observer_id.in_(obs_ids)).all()
                )
                for r in rows:
                    r['ean'] = ean_by_obs.get(r['producto_id'])

            # Lista de labs presentes en el resultado (para el filtro).
            labs_set = {(r['lab_id'], r['lab_nombre']) for r in rows
                        if r['lab_id']}
            labs_disponibles = sorted(labs_set, key=lambda kv: kv[1])

        stats = {
            'productos': len(rows),
            'total_faltan': sum(r['faltan'] for r in rows),
            'con_ventas': sum(1 for r in rows if r['u12m'] > 0),
        }
        return render_template('informes_bajo_minimo.html',
                               rows=rows,
                               stats=stats,
                               lab_id=lab_id,
                               labs_disponibles=labs_disponibles)

    @app.route('/informes/pedido-auto', methods=['GET'])
    @login_required
    def informe_pedido_auto():
        """Análisis de mínimos #2: armar un pedido sugerido para un laboratorio,
        partiendo de los productos del lab que están bajo mínimo en ObServer.

        Cantidad sugerida por ítem:
          - Si hay máximo cargado: max(1, maximo - stock_actual).
          - Si no: max(1, minimo - stock_actual).
        """
        lab_id = request.args.get('lab_id', type=int)
        labs_con_alertas = []
        rows = []
        lab_nombre = None

        with database.get_db() as session:
            stock_q = (
                session.query(
                    ObsStock.producto_observer.label('pid'),
                    func.sum(ObsStock.stock_actual).label('stock'),
                    func.sum(ObsStock.minimo).label('minimo'),
                    func.sum(ObsStock.maximo).label('maximo'),
                )
                .filter(ObsStock.minimo.isnot(None))
                .filter(ObsStock.minimo > 0)
                .group_by(ObsStock.producto_observer)
                .subquery()
            )

            # Selector de labs: solo los que tienen al menos 1 producto bajo mínimo.
            labs_q = (session.query(
                        ObsLaboratorio.observer_id,
                        ObsLaboratorio.descripcion,
                        func.count(distinct(ObsProducto.observer_id)).label('n'),
                     )
                     .join(ObsProducto,
                           ObsProducto.laboratorio_observer == ObsLaboratorio.observer_id)
                     .join(stock_q, stock_q.c.pid == ObsProducto.observer_id)
                     .filter(ObsProducto.fecha_baja.is_(None))
                     .filter(stock_q.c.stock < stock_q.c.minimo)
                     .group_by(ObsLaboratorio.observer_id, ObsLaboratorio.descripcion)
                     .order_by(func.count(distinct(ObsProducto.observer_id)).desc()))
            labs_con_alertas = [
                {'lab_id': r[0], 'nombre': r[1], 'n_productos': int(r[2])}
                for r in labs_q.all()
            ]

            if lab_id:
                lab = session.get(ObsLaboratorio, lab_id)
                lab_nombre = lab.descripcion if lab else None

                desde, hasta = _ventana_12m()
                ventas_sub = (
                    session.query(
                        ObsVentaMensual.producto_observer.label('pid'),
                        func.sum(ObsVentaMensual.unidades).label('u12m'),
                        func.sum(ObsVentaMensual.monto).label('m12m'),
                    )
                    .filter(ObsVentaMensual.anio * 100 + ObsVentaMensual.mes >= desde)
                    .filter(ObsVentaMensual.anio * 100 + ObsVentaMensual.mes <= hasta)
                    .group_by(ObsVentaMensual.producto_observer)
                    .subquery()
                )

                q = (session.query(
                        ObsProducto.observer_id.label('pid'),
                        ObsProducto.descripcion.label('desc'),
                        ObsProducto.codigo_alfabeta,
                        stock_q.c.stock,
                        stock_q.c.minimo,
                        stock_q.c.maximo,
                        func.coalesce(ventas_sub.c.u12m, 0).label('u12m'),
                        func.coalesce(ventas_sub.c.m12m, 0).label('m12m'),
                     )
                     .join(stock_q, stock_q.c.pid == ObsProducto.observer_id)
                     .outerjoin(ventas_sub, ventas_sub.c.pid == ObsProducto.observer_id)
                     .filter(ObsProducto.fecha_baja.is_(None))
                     .filter(ObsProducto.laboratorio_observer == lab_id)
                     .filter(stock_q.c.stock < stock_q.c.minimo)
                     .order_by(
                        (stock_q.c.minimo - stock_q.c.stock).desc(),
                        func.coalesce(ventas_sub.c.u12m, 0).desc(),
                     ))

                obs_ids = []
                for r in q.all():
                    stock = int(r.stock or 0)
                    minimo = int(r.minimo or 0)
                    maximo = int(r.maximo or 0) if r.maximo is not None else None
                    if maximo and maximo > stock:
                        sugerido = maximo - stock
                        base = 'max-stock'
                    else:
                        sugerido = max(1, minimo - stock)
                        base = 'min-stock'
                    u12m = int(r.u12m or 0)
                    m12m = float(r.m12m or 0)
                    avg_mensual = u12m / 12.0 if u12m else 0.0
                    precio_unit = (m12m / u12m) if u12m else 0.0
                    factor_falta = min(1.0, max(0.0, (minimo - stock) / minimo)) if minimo else 0.0
                    perdida_mensual = round(avg_mensual * factor_falta, 1)
                    perdida_pesos = round(perdida_mensual * precio_unit, 2)
                    # Diagnóstico del mínimo configurado vs ventas:
                    #   ratio = minimo / avg_mensual
                    #   <0.5  → bajo (cubre <2 semanas, vas a vivir bajo mínimo)
                    #   0.5–2 → ok (1–2 meses de cover)
                    #   >2    → alto (mínimo sobredimensionado, plata frenada)
                    if avg_mensual <= 0:
                        diag = ('sin_ventas', 'Sin ventas 12m')
                    else:
                        ratio = minimo / avg_mensual
                        if ratio < 0.5:
                            diag = ('bajo', f'Bajo — cubre ~{int(ratio*30)}d, sugerido ≥{int(round(avg_mensual))}')
                        elif ratio > 2:
                            diag = ('alto', f'Alto — cubre ~{int(ratio*30)}d, sugerido ≈{int(round(avg_mensual*1.5))}')
                        else:
                            diag = ('ok', f'OK — cubre ~{int(ratio*30)}d')
                    rows.append({
                        'producto_id': r.pid,
                        'descripcion': r.desc,
                        'codigo_alfabeta': r.codigo_alfabeta,
                        'stock': stock,
                        'minimo': minimo,
                        'maximo': maximo,
                        'sugerido': max(1, sugerido),
                        'base_sugerido': base,
                        'u12m': u12m,
                        'perdida_mensual': perdida_mensual,
                        'perdida_pesos': perdida_pesos,
                        'precio_unit': round(precio_unit, 2),
                        'min_diag': diag[0],
                        'min_diag_label': diag[1],
                        'ean': None,
                    })
                    obs_ids.append(r.pid)

                if obs_ids:
                    ean_by_obs = dict(
                        session.query(Producto.observer_id, Producto.codigo_barra)
                        .filter(Producto.observer_id.in_(obs_ids)).all()
                    )
                    for r in rows:
                        r['ean'] = ean_by_obs.get(r['producto_id'])

        stats = {
            'productos': len(rows),
            'unidades_total': sum(r['sugerido'] for r in rows),
            'perdida_mensual_total': round(sum(r.get('perdida_mensual', 0) for r in rows), 1),
            'perdida_pesos_total': round(sum(r.get('perdida_pesos', 0) for r in rows), 2),
        }
        # Top 10 por pérdida estimada para el gráfico de barras.
        top_perdida = sorted(
            [r for r in rows if r.get('perdida_mensual', 0) > 0],
            key=lambda r: -r['perdida_mensual'],
        )[:10]
        chart_perdida = {
            'labels': [r['descripcion'][:50] for r in top_perdida],
            'data':   [r['perdida_mensual'] for r in top_perdida],
        }
        # Top 10 por pérdida valorizada en pesos.
        top_pesos = sorted(
            [r for r in rows if r.get('perdida_pesos', 0) > 0],
            key=lambda r: -r['perdida_pesos'],
        )[:10]
        chart_pesos = {
            'labels': [r['descripcion'][:50] for r in top_pesos],
            'data':   [r['perdida_pesos'] for r in top_pesos],
        }
        return render_template('informes_pedido_auto.html',
                               lab_id=lab_id,
                               lab_nombre=lab_nombre,
                               labs_con_alertas=labs_con_alertas,
                               rows=rows,
                               stats=stats,
                               chart_perdida=chart_perdida,
                               chart_pesos=chart_pesos)

    @app.route('/informes/pedido-auto/crear', methods=['POST'])
    @login_required
    def informe_pedido_auto_crear():
        """Crea un Pedido + PedidoItems con las cantidades editadas por el
        usuario en la pantalla de pedido auto."""
        from database import Pedido, PedidoItem
        lab_id = request.form.get('lab_id', type=int)
        if not lab_id:
            return jsonify({'error': 'lab_id requerido'}), 400

        with database.get_db() as session:
            lab = session.get(ObsLaboratorio, lab_id)
            lab_nombre = lab.descripcion if lab else f'Lab #{lab_id}'

            items = []
            i = 0
            while True:
                pid = request.form.get(f'pid_{i}', type=int)
                if pid is None:
                    break
                qty = request.form.get(f'qty_{i}', type=int) or 0
                if qty > 0:
                    nombre = request.form.get(f'nombre_{i}', '').strip()
                    cb = request.form.get(f'ean_{i}', '').strip() or f'OBS:{pid}'
                    items.append(PedidoItem(
                        codigo_barra=cb[:20],
                        nombre=nombre[:200],
                        cantidad=qty,
                        precio_pvp=0,
                        subtotal=0,
                    ))
                i += 1

            if not items:
                from flask import flash, redirect, url_for
                flash('No hay items con cantidad > 0.', 'warning')
                return redirect(url_for('informe_pedido_auto', lab_id=lab_id))

            from helpers import now_ar
            pedido = Pedido(
                laboratorio=lab_nombre,
                farmacia='',
                periodo=f'Auto bajo mínimo {now_ar().strftime("%Y-%m-%d")}',
                n_days=0,
                items=items,
                estado='PENDIENTE',
            )
            session.add(pedido)
            session.commit()
            pedido_id = pedido.id

        from flask import flash, redirect, url_for
        flash(f'Pedido creado: {len(items)} productos.', 'success')
        return redirect(url_for('order_detail', pedido_id=pedido_id))

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
