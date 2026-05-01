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
from database import ObsLaboratorio, ObsNombreDroga, ObsProducto, ObsStock, ObsVentaMensual, Producto


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


def sugerir_drogueria_para_lab(session, lab_nombre):
    """Devuelve dict {provider_id, nombre, n_pedidos_anteriores} o None.

    Busca pedidos pasados con canal='drogueria' para ese laboratorio y
    devuelve la droguería más frecuente. Si no hay historial → None.

    Args:
        session: SQLAlchemy session.
        lab_nombre: nombre del laboratorio (string, como en Pedido.laboratorio).
    """
    from sqlalchemy import func

    from database import Pedido, Provider

    if not lab_nombre:
        return None

    row = (session.query(
                Pedido.partner_id,
                func.count(Pedido.id).label('n'),
            )
            .filter(Pedido.laboratorio == lab_nombre)
            .filter(Pedido.canal == 'drogueria')
            .filter(Pedido.partner_id.isnot(None))
            .group_by(Pedido.partner_id)
            .order_by(func.count(Pedido.id).desc())
            .first())

    if not row or not row[0]:
        return None
    prov = session.get(Provider, row[0])
    if not prov:
        return None
    return {
        'provider_id': prov.id,
        'nombre': prov.razon_social,
        'n_pedidos_anteriores': int(row[1]),
    }


def calcular_metricas_pedido_auto(stock, minimo, maximo, u12m, m12m):
    """Calcula las métricas de un producto bajo mínimo para el pedido automático.

    Función pura, sin DB. Testeable.

    Args:
        stock: int, stock actual.
        minimo: int, mínimo configurado.
        maximo: int o None, máximo configurado.
        u12m: int, unidades vendidas en los últimos 12 meses.
        m12m: float, monto vendido en los últimos 12 meses.

    Returns:
        dict con: sugerido, base_sugerido, perdida_mensual, perdida_pesos,
                  precio_unit, min_diag (sin_ventas|bajo|ok|alto), min_diag_label.

    Reglas:
      - sugerido = max(1, maximo - stock) si hay máximo > stock; sino max(1, minimo - stock).
      - avg_mensual = u12m / 12.
      - precio_unit = m12m / u12m (0 si no hay ventas).
      - factor_falta = clamp((minimo - stock) / minimo, 0, 1).
      - perdida_mensual = avg_mensual * factor_falta.
      - perdida_pesos = perdida_mensual * precio_unit.
      - Diagnóstico de mínimo:
          - sin_ventas si u12m=0
          - ratio = minimo / avg_mensual
          - <0.5 → bajo (cubre <~2 semanas)
          - >2   → alto (cubre >2 meses)
          - sino → ok
    """
    stock = int(stock or 0)
    minimo = int(minimo or 0)
    maximo = int(maximo) if maximo is not None else None
    u12m = int(u12m or 0)
    m12m = float(m12m or 0)

    if maximo and maximo > stock:
        sugerido = maximo - stock
        base_sugerido = 'max-stock'
    else:
        sugerido = max(1, minimo - stock)
        base_sugerido = 'min-stock'

    avg_mensual = u12m / 12.0 if u12m else 0.0
    precio_unit = (m12m / u12m) if u12m else 0.0
    factor_falta = min(1.0, max(0.0, (minimo - stock) / minimo)) if minimo else 0.0
    perdida_mensual = round(avg_mensual * factor_falta, 1)
    perdida_pesos = round(perdida_mensual * precio_unit, 2)

    if avg_mensual <= 0:
        min_diag = 'sin_ventas'
        min_diag_label = 'Sin ventas 12m'
    else:
        ratio = minimo / avg_mensual
        if ratio < 0.5:
            min_diag = 'bajo'
            min_diag_label = f'Bajo — cubre ~{int(ratio * 30)}d, sugerido ≥{int(round(avg_mensual))}'
        elif ratio > 2:
            min_diag = 'alto'
            min_diag_label = f'Alto — cubre ~{int(ratio * 30)}d, sugerido ≈{int(round(avg_mensual * 1.5))}'
        else:
            min_diag = 'ok'
            min_diag_label = f'OK — cubre ~{int(ratio * 30)}d'

    return {
        'sugerido': max(1, sugerido),
        'base_sugerido': base_sugerido,
        'avg_mensual': round(avg_mensual, 2),
        'precio_unit': round(precio_unit, 2),
        'perdida_mensual': perdida_mensual,
        'perdida_pesos': perdida_pesos,
        'min_diag': min_diag,
        'min_diag_label': min_diag_label,
    }


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
        venta_tipo = (request.args.get('venta_tipo') or '').strip()
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
                    ObsProducto.id_tipo_venta_control.label('tvc'),
                    ObsProducto.nombre_droga_observer.label('droga_id'),
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
            if venta_tipo == 'libre':
                q = q.filter(ObsProducto.id_tipo_venta_control == 'L')
            elif venta_tipo == 'receta':
                q = q.filter(ObsProducto.id_tipo_venta_control.in_(['R', 'A']))
            elif venta_tipo == 'controlado':
                q = q.filter(ObsProducto.id_tipo_venta_control.in_(['1','2','3','4','5','6','7','8']))
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
                    'tvc': (r.tvc or '').strip(),
                    'droga_id': r.droga_id,
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
                               venta_tipo=venta_tipo,
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
        venta_tipo = (request.args.get('venta_tipo') or '').strip()
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

            tiene_plantilla = False
            local_lab_nombre = None
            droguerias = []
            sugerencia_drogueria = None
            if lab_id:
                lab = session.get(ObsLaboratorio, lab_id)
                lab_nombre = lab.descripcion if lab else None
                # Buscar Laboratorio local mapeado para detectar plantilla.
                from database import ExportTemplate, Laboratorio, Provider
                local_lab = (session.query(Laboratorio)
                             .filter(Laboratorio.observer_id == lab_id).first())
                if local_lab:
                    local_lab_nombre = local_lab.nombre
                    tpl = session.get(ExportTemplate, local_lab.id)
                    tiene_plantilla = bool(tpl and tpl.columns_json)

                # Droguerías disponibles para el dropdown del canal.
                provs = (session.query(Provider)
                         .filter(Provider.tipo == 'drogueria')
                         .filter(Provider.activo == True)  # noqa: E712
                         .order_by(Provider.razon_social).all())
                droguerias = [{'id': p.id, 'nombre': p.razon_social} for p in provs]

                # Sugerencia: el lab que usamos en Pedido.laboratorio es el local.
                nombre_para_buscar = local_lab_nombre or lab_nombre
                sugerencia_drogueria = sugerir_drogueria_para_lab(
                    session, nombre_para_buscar)

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
                        ObsProducto.id_tipo_venta_control.label('tvc'),
                        ObsProducto.nombre_droga_observer.label('droga_id'),
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
                if venta_tipo == 'libre':
                    q = q.filter(ObsProducto.id_tipo_venta_control == 'L')
                elif venta_tipo == 'receta':
                    q = q.filter(ObsProducto.id_tipo_venta_control.in_(['R', 'A']))
                elif venta_tipo == 'controlado':
                    q = q.filter(ObsProducto.id_tipo_venta_control.in_(['1','2','3','4','5','6','7','8']))

                obs_ids = []
                for r in q.all():
                    metricas = calcular_metricas_pedido_auto(
                        stock=r.stock, minimo=r.minimo, maximo=r.maximo,
                        u12m=r.u12m, m12m=r.m12m,
                    )
                    rows.append({
                        'producto_id': r.pid,
                        'descripcion': r.desc,
                        'codigo_alfabeta': r.codigo_alfabeta,
                        'tvc': (r.tvc or '').strip(),
                        'droga_id': r.droga_id,
                        'stock': int(r.stock or 0),
                        'minimo': int(r.minimo or 0),
                        'maximo': int(r.maximo) if r.maximo is not None else None,
                        'u12m': int(r.u12m or 0),
                        'ean': None,
                        **metricas,
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
                               venta_tipo=venta_tipo,
                               lab_nombre=lab_nombre,
                               labs_con_alertas=labs_con_alertas,
                               rows=rows,
                               stats=stats,
                               chart_perdida=chart_perdida,
                               chart_pesos=chart_pesos,
                               tiene_plantilla=tiene_plantilla,
                               local_lab_nombre=local_lab_nombre,
                               droguerias=droguerias,
                               sugerencia_drogueria=sugerencia_drogueria)

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
            # Preferir el nombre del Laboratorio LOCAL si está mapeado:
            # así ExportTemplate (PK=lab.id) y filtros de plantilla del
            # /order/<id> matchean por pedido.laboratorio.
            from database import Laboratorio
            local_lab = (session.query(Laboratorio)
                         .filter(Laboratorio.observer_id == lab_id).first())
            if local_lab:
                lab_nombre = local_lab.nombre
            else:
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

            # Canal: lo que el user eligió en el form (laboratorio/drogueria/'').
            canal = (request.form.get('canal') or '').strip() or None
            partner_id = request.form.get('partner_id', type=int)
            if canal not in ('laboratorio', 'drogueria'):
                canal = None
            if canal == 'drogueria' and not partner_id:
                # Pidió droguería pero no eligió cuál → no settear canal.
                canal = None
                partner_id = None
            if canal != 'drogueria':
                partner_id = None

            from helpers import _upsert_pedido_items, now_ar
            pedido = Pedido(
                laboratorio=lab_nombre,
                farmacia='',
                periodo=f'Auto bajo mínimo {now_ar().strftime("%Y-%m-%d")}',
                n_days=0,
                items=items,
                estado='PENDIENTE',
                canal=canal,
                partner_id=partner_id,
                canal_elegido_en=now_ar() if canal else None,
            )
            session.add(pedido)
            # Sumar al catálogo master los productos del pedido (antes este flow lo omitía).
            _upsert_pedido_items(session, items)
            session.commit()
            pedido_id = pedido.id

            # Si el user clickeó "Crear + exportar plantilla", generamos el XLSX
            # acá mismo en lugar de redirigir a /order/<id>. Reusa la lógica del
            # endpoint /order/<id>/export/plantilla pero sin requerir round-trip.
            if request.form.get('exportar_plantilla') == '1':
                import json as _json
                from io import BytesIO

                import openpyxl
                from flask import send_file
                from openpyxl.styles import Alignment, Font, PatternFill

                from database import ExportTemplate, Laboratorio

                local_lab_for_tpl = (session.query(Laboratorio)
                                     .filter_by(nombre=lab_nombre).first())
                tpl = (session.get(ExportTemplate, local_lab_for_tpl.id)
                       if local_lab_for_tpl else None)
                if tpl and tpl.columns_json:
                    cols = [c for c in _json.loads(tpl.columns_json) if c.get('enabled')]
                    if cols:
                        # Construimos rows con los campos disponibles. El pedido auto
                        # tiene ean/nombre/cantidad sí o sí; el resto queda vacío y
                        # la plantilla pone celda en blanco.
                        rows = [{
                            'ean': it.codigo_barra,
                            'codigo_barra': it.codigo_barra,
                            'nombre': it.nombre,
                            'descripcion': it.nombre,
                            'cantidad': it.cantidad,
                            'total': it.cantidad,
                        } for it in items]

                        wb = openpyxl.Workbook()
                        ws = wb.active
                        ws.title = 'Pedido'
                        row_offset = 1
                        if tpl.custom_header:
                            ws.cell(row=1, column=1, value=tpl.custom_header).font = Font(bold=True, size=12)
                            ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(cols))
                            row_offset = 2
                        hdr_fill = PatternFill('solid', fgColor='1e1e1e')
                        for ci, col in enumerate(cols, 1):
                            cell = ws.cell(row=row_offset, column=ci, value=col['label'])
                            cell.font = Font(bold=True, color='FFFFFF', size=10)
                            cell.fill = hdr_fill
                            cell.alignment = Alignment(horizontal='center')
                        for ri, row in enumerate(rows, row_offset + 1):
                            for ci, col in enumerate(cols, 1):
                                val = row.get(col['field'])
                                if val == '':
                                    val = None
                                ws.cell(row=ri, column=ci, value=val)
                        for ci, col in enumerate(cols, 1):
                            field = col['field']
                            ws.column_dimensions[openpyxl.utils.get_column_letter(ci)].width = (
                                20 if field in ('nombre', 'descripcion') else 15 if field in ('ean', 'codigo_barra') else 12
                            )
                        buf = BytesIO()
                        wb.save(buf)
                        buf.seek(0)
                        fname = f"Pedido_{lab_nombre}_AutoBajoMinimo.xlsx".replace(' ', '_')
                        return send_file(
                            buf, as_attachment=True, download_name=fname,
                            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        )
                # Si la plantilla no está bien configurada, fallback a redirect normal.
                from flask import flash as _flash
                _flash('La plantilla del laboratorio no está bien configurada. Pedido creado, exportá manualmente desde la pantalla del pedido.', 'warning')

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

            # Stock + mínimo: sumamos sobre todas las farmacias si hay datos.
            from database import ObsStock
            stock_row = (session.query(
                            func.coalesce(func.sum(ObsStock.stock_actual), 0),
                            func.coalesce(func.sum(ObsStock.minimo), 0),
                         )
                         .filter(ObsStock.producto_observer == observer_id).first())
            stock_total = int(stock_row[0] or 0) if stock_row else 0
            minimo_total = int(stock_row[1] or 0) if stock_row else 0

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
                'stock': stock_total,
                'minimo': minimo_total,
                'rotacion': rot,
                'tipo': 'N',
                'start_month': start_m,
                'n_days': 35,
                'sin_historial': not no_cero,
                'analizado_en': None,
            })

    @app.route('/api/observer-product/<int:observer_id>/chart-mes')
    @login_required
    def api_observer_product_chart_mes(observer_id):
        """Gráfico del último mes (30 días) por DÍA, desde obs_ventas_detalle.

        Devuelve labels = ['DD/MM' x 30] y data = unidades vendidas ese día.
        """
        from datetime import date as _date
        from datetime import timedelta

        from database import ObsVentaDetalle
        with database.get_db() as session:
            obs = session.get(ObsProducto, observer_id)
            if not obs:
                return jsonify({'ok': False, 'error': 'Producto ObServer no encontrado'}), 404

            hoy = _date.today()
            desde = hoy - timedelta(days=29)  # ventana de 30 días incluyendo hoy
            rows = (session.query(
                        ObsVentaDetalle.fecha_estadistica,
                        func.coalesce(func.sum(ObsVentaDetalle.cantidad), 0),
                    )
                    .filter(ObsVentaDetalle.producto_observer == observer_id,
                            ObsVentaDetalle.fecha_estadistica.isnot(None),
                            ObsVentaDetalle.fecha_estadistica >= desde,
                            ObsVentaDetalle.fecha_estadistica <= hoy)
                    .group_by(ObsVentaDetalle.fecha_estadistica)
                    .all())
            por_fecha = {r[0]: float(r[1] or 0) for r in rows}
            labels, datos = [], []
            d = desde
            while d <= hoy:
                labels.append(d.strftime('%d/%m'))
                datos.append(round(por_fecha.get(d, 0), 2))
                d += timedelta(days=1)
            total = sum(datos)
            avg = total / 30.0
            return jsonify({
                'ok': True,
                'nombre': obs.descripcion or '',
                'observer_id': observer_id,
                'labels': labels,
                'data': datos,
                'total_30d': round(total, 2),
                'avg_diario': round(avg, 2),
                'desde': desde.isoformat(),
                'hasta': hoy.isoformat(),
            })

    @app.route('/api/observer-product/<int:observer_id>/ingresos-mes')
    @login_required
    def api_observer_product_ingresos_mes(observer_id):
        """Ingresos (factura_items) + Pedidos (pedido_emitido_item) por día,
        últimos 30 días.

        - Ingresos: mapea observer_id → códigos de barra (Producto + obs_codigos_barras).
          NCR resta.
        - Pedidos: usa observer_id directo de pedido_emitido_item.
        """
        from datetime import date as _date
        from datetime import timedelta

        from database import Invoice, InvoiceItem, ObsCodigoBarras, PedidoEmitido, PedidoEmitidoItem, Producto
        with database.get_db() as session:
            obs = session.get(ObsProducto, observer_id)
            if not obs:
                return jsonify({'ok': False, 'error': 'Producto ObServer no encontrado'}), 404

            codigos = set()
            # Vía obs_codigos_barras (todas las orden, sin baja)
            for r in (session.query(ObsCodigoBarras.codigo_barras)
                      .filter(ObsCodigoBarras.producto_observer == observer_id,
                              ObsCodigoBarras.fecha_baja.is_(None)).all()):
                if r[0]: codigos.add(r[0])
            # Vía Producto local (codigo_barra + alts)
            prod = (session.query(Producto)
                    .filter(Producto.observer_id == observer_id).first())
            if prod:
                for c in (prod.codigo_barra, prod.codigo_barra_alt1,
                          prod.codigo_barra_alt2, prod.codigo_barra_alt3):
                    if c: codigos.add(c)

            hoy = _date.today()
            desde = hoy - timedelta(days=29)
            por_fecha = {}
            if codigos:
                rows = (session.query(Invoice.fecha,
                                      func.coalesce(func.sum(InvoiceItem.cantidad), 0))
                        .join(InvoiceItem, InvoiceItem.factura_id == Invoice.id)
                        .filter(InvoiceItem.codigo_barra.in_(list(codigos)))
                        .filter(Invoice.fecha >= desde,
                                Invoice.fecha <= hoy)
                        .group_by(Invoice.fecha).all())
                por_fecha = {r[0]: float(r[1] or 0) for r in rows}

            # Pedidos por día (cantidad_pedida agregada por fecha del pedido).
            ped_rows = (session.query(
                            func.date(PedidoEmitido.fecha),
                            func.coalesce(func.sum(PedidoEmitidoItem.cantidad_pedida), 0))
                        .join(PedidoEmitido,
                              PedidoEmitido.id == PedidoEmitidoItem.pedido_id)
                        .filter(PedidoEmitidoItem.observer_id == observer_id,
                                func.date(PedidoEmitido.fecha) >= desde,
                                func.date(PedidoEmitido.fecha) <= hoy)
                        .group_by(func.date(PedidoEmitido.fecha))
                        .all())
            por_fecha_ped = {r[0]: float(r[1] or 0) for r in ped_rows}

            labels, datos, datos_ped = [], [], []
            d = desde
            while d <= hoy:
                labels.append(d.strftime('%d/%m'))
                datos.append(round(por_fecha.get(d, 0), 2))
                datos_ped.append(round(por_fecha_ped.get(d, 0), 2))
                d += timedelta(days=1)
            total = sum(datos)
            total_ped = sum(datos_ped)
            return jsonify({
                'ok': True,
                'nombre': obs.descripcion or '',
                'observer_id': observer_id,
                'codigos_resueltos': len(codigos),
                'labels': labels,
                'data': datos,
                'total_30d': round(total, 2),
                'pedido_data': datos_ped,
                'pedido_total_30d': round(total_ped, 2),
                'desde': desde.isoformat(),
                'hasta': hoy.isoformat(),
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
