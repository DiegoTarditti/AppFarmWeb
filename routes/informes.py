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
from sqlalchemy import func

import database
from database import ObsLaboratorio, ObsNombreDroga, ObsProducto, ObsVentaMensual


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
                        })
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
