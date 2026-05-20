"""Planificación de compras del mes.

Dado capital disponible + días de cobertura objetivo, calcula qué comprar,
cuánto y a qué lab, priorizando por urgencia (menor cobertura primero).

Fase 1: cálculo plano sin estacionalidad ni cronograma.
"""
from datetime import date

from flask import render_template, request
from flask_login import login_required
from sqlalchemy import func

import database
from database import Laboratorio, ObsLaboratorio, ObsProducto, ObsStock, ObsVentaMensual


def _keys_ultimos_n_meses(n):
    hoy = date.today()
    keys = []
    for i in range(n):
        m = hoy.month - i
        y = hoy.year
        if m <= 0:
            m += 12
            y -= 1
        keys.append(y * 100 + m)
    return keys


def init_app(app):

    @app.route('/planificacion/compras-mes')
    @login_required
    def planificacion_compras_mes():
        try:
            capital = float(request.args.get('capital') or 0)
        except (TypeError, ValueError):
            capital = 0.0
        try:
            cobertura = int(request.args.get('cobertura') or 30)
        except (TypeError, ValueError):
            cobertura = 30
        cobertura = max(7, min(cobertura, 90))
        try:
            meses = int(request.args.get('meses') or 3)
        except (TypeError, ValueError):
            meses = 3
        meses = max(1, min(meses, 12))

        items = []
        farmacias = []

        with database.get_db() as session:
            # Farmacias disponibles en ObsStock
            farmacias = [r[0] for r in session.query(
                ObsStock.id_farmacia).distinct().order_by(ObsStock.id_farmacia).all()]
            try:
                farmacia_id = int(request.args.get('farmacia_id') or (farmacias[0] if farmacias else 1))
            except (TypeError, ValueError):
                farmacia_id = farmacias[0] if farmacias else 1

            keys = _keys_ultimos_n_meses(meses)

            # Stock actual por producto (esta farmacia)
            stock_q = (session.query(
                ObsStock.producto_observer,
                ObsStock.stock_actual)
                .filter(ObsStock.id_farmacia == farmacia_id)
                .subquery())

            # Ventas últimos N meses por producto
            ventas_q = (session.query(
                ObsVentaMensual.producto_observer,
                func.sum(ObsVentaMensual.unidades).label('total_u'))
                .filter(ObsVentaMensual.id_farmacia == farmacia_id,
                        (ObsVentaMensual.anio * 100 + ObsVentaMensual.mes).in_(keys))
                .group_by(ObsVentaMensual.producto_observer)
                .subquery())

            rows = (session.query(
                ObsProducto.observer_id,
                ObsProducto.descripcion,
                ObsProducto.descripcion_custom,
                ObsLaboratorio.descripcion.label('lab_nombre'),
                Laboratorio.descuento_base,
                stock_q.c.stock_actual,
                ventas_q.c.total_u)
                .join(stock_q, stock_q.c.producto_observer == ObsProducto.observer_id)
                .join(ventas_q, ventas_q.c.producto_observer == ObsProducto.observer_id)
                .outerjoin(ObsLaboratorio,
                           ObsLaboratorio.observer_id == ObsProducto.laboratorio_observer)
                .outerjoin(Laboratorio,
                           Laboratorio.observer_id == ObsLaboratorio.observer_id)
                .filter(ObsProducto.fecha_baja.is_(None),
                        ventas_q.c.total_u > 0)
                .all())

            for r in rows:
                stock = int(r.stock_actual or 0)
                total_u = float(r.total_u or 0)
                consumo_diario = total_u / (meses * 30)
                if consumo_diario <= 0:
                    continue
                cobertura_actual = stock / consumo_diario
                if cobertura_actual >= cobertura:
                    continue
                unidades = max(1, round((cobertura - cobertura_actual) * consumo_diario))
                dto = float(r.descuento_base or 30)
                items.append({
                    'producto_id': r.observer_id,
                    'nombre': r.descripcion_custom or r.descripcion,
                    'lab': r.lab_nombre or '—',
                    'stock': stock,
                    'cobertura_actual': round(cobertura_actual, 1),
                    'consumo_diario': round(consumo_diario, 2),
                    'unidades': unidades,
                    'dto_pct': dto,
                })

        items.sort(key=lambda x: x['cobertura_actual'])

        # Marcar cuáles caben en el capital (sin precio exacto, marcamos todos si capital=0)
        acum = 0.0
        for it in items:
            it['entra'] = True  # sin precio no podemos filtrar por capital aún
            it['acum'] = acum

        return render_template('planificacion_compras_mes.html',
                               items=items,
                               capital=capital,
                               cobertura=cobertura,
                               meses=meses,
                               farmacia_id=farmacia_id,
                               farmacias=farmacias,
                               n_total=len(items))
