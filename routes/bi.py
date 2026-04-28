"""Inteligencia de Negocios — tablero del dueño.

Pantalla `/bi` con 4 cards básicos pensados para que el dueño de la
farmacia los mire al abrir la mañana, en menos de 5 minutos.

Cards:
1. Para reponer ya — productos bajo mínimo + pérdida estimada.
2. Próximos quiebres — productos con stock > 0 pero cobertura ≤ 14 días.
3. Top vendidos del mes — top 10 por unidades del mes anterior completo.
4. Top labs del mes — top 10 por unidades del mes anterior completo.

Reusa queries existentes de `routes/informes.py` cuando es posible.
"""
from datetime import date

from flask import render_template
from flask_login import login_required
from sqlalchemy import func

import database
from database import ObsLaboratorio, ObsProducto, ObsStock, ObsVentaMensual, Producto


def _mes_anterior_ym():
    """Devuelve (anio, mes) del mes anterior completo."""
    hoy = date.today()
    if hoy.month == 1:
        return hoy.year - 1, 12
    return hoy.year, hoy.month - 1


def init_app(app):

    @app.route('/bi')
    @login_required
    def bi_tablero():
        """Tablero diario del dueño."""
        with database.get_db() as session:
            # ── Card 1: Para reponer ya ────────────────────────────────────
            # Sumar stock y mínimo agrupado por producto, contar los que
            # tienen stock < minimo. Pérdida estimada = avg_mensual ×
            # factor_falta × precio_promedio.
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

            ventas_12m = (
                session.query(
                    ObsVentaMensual.producto_observer.label('pid'),
                    func.sum(ObsVentaMensual.unidades).label('u12m'),
                    func.sum(ObsVentaMensual.monto).label('m12m'),
                )
                .group_by(ObsVentaMensual.producto_observer)
                .subquery()
            )

            # Filtros aplicados para evitar valores irreales que distorsionaban
            # la lectura del tablero (antes mostraba ~1428 productos / $43M):
            #   1. fecha_baja IS NULL → solo productos activos.
            #   2. id_tipo_venta_control IN ('L','R','A') o NULL → excluye
            #      controlados (psicotrópicos/estupefacientes 1-8 tienen flujo
            #      distinto y no aplican al "para reponer ya").
            #   3. u12m > 0 → no contar clavos absolutos (sin venta histórica).
            #   4. minimo <= u12m / 2 → excluye mínimos mal cargados en
            #      ObServer: si el mínimo es > 50% de las ventas anuales, está
            #      sobredimensionado y va a aparecer perpetuamente "bajo mínimo".
            bajo_min_q = (
                session.query(
                    stock_q.c.stock,
                    stock_q.c.minimo,
                    func.coalesce(ventas_12m.c.u12m, 0).label('u12m'),
                    func.coalesce(ventas_12m.c.m12m, 0).label('m12m'),
                )
                .outerjoin(ventas_12m, ventas_12m.c.pid == stock_q.c.pid)
                .join(ObsProducto, ObsProducto.observer_id == stock_q.c.pid)
                .filter(ObsProducto.fecha_baja.is_(None))
                .filter(stock_q.c.stock < stock_q.c.minimo)
                # Tipo de venta válido (L=Libre, R=Receta, A=Archivada). Los
                # controlados ('1'-'8') quedan fuera. NULL los aceptamos para
                # no perder productos donde el campo aún no se sincronizó.
                .filter(
                    (ObsProducto.id_tipo_venta_control.in_(['L', 'R', 'A'])) |
                    (ObsProducto.id_tipo_venta_control.is_(None))
                )
                # Excluye clavos absolutos y mínimos sobredimensionados.
                .filter(ventas_12m.c.u12m > 0)
                .filter(stock_q.c.minimo <= ventas_12m.c.u12m / 2)
            )

            n_bajo_min = 0
            perdida_pesos_total = 0.0
            for r in bajo_min_q.all():
                n_bajo_min += 1
                stock = int(r.stock or 0)
                minimo = int(r.minimo or 0)
                u12m = int(r.u12m or 0)
                m12m = float(r.m12m or 0)
                avg_mensual = u12m / 12.0 if u12m else 0.0
                precio = (m12m / u12m) if u12m else 0.0
                factor = (minimo - stock) / minimo if minimo else 0.0
                factor = min(1.0, max(0.0, factor))
                perdida_pesos_total += avg_mensual * factor * precio

            # ── Card 2: Próximos quiebres ───────────────────────────────────
            # Productos con stock > 0 pero cobertura (stock / venta_diaria) ≤ 14
            # días. Solo consideramos productos con ventas (avg > 0).
            quiebres_q = (
                session.query(
                    ObsProducto.observer_id.label('pid'),
                    ObsProducto.descripcion.label('desc'),
                    ObsProducto.nombre_droga_observer.label('droga_id'),
                    stock_q.c.stock,
                    ventas_12m.c.u12m,
                    ObsLaboratorio.descripcion.label('lab'),
                )
                .join(stock_q, stock_q.c.pid == ObsProducto.observer_id)
                .join(ventas_12m, ventas_12m.c.pid == ObsProducto.observer_id)
                .outerjoin(ObsLaboratorio,
                           ObsLaboratorio.observer_id == ObsProducto.laboratorio_observer)
                .filter(ObsProducto.fecha_baja.is_(None))
                .filter(stock_q.c.stock > 0)
                .filter(ventas_12m.c.u12m > 0)
            )
            quiebres_proximos = []
            for r in quiebres_q.all():
                stock = int(r.stock or 0)
                u12m = int(r.u12m or 0)
                avg_diaria = u12m / 365.0
                if avg_diaria <= 0:
                    continue
                dias_cobertura = stock / avg_diaria
                if dias_cobertura <= 14:
                    quiebres_proximos.append({
                        'producto_id': r.pid,
                        'descripcion': r.desc,
                        'droga_id': r.droga_id,
                        'lab': r.lab or '—',
                        'stock': stock,
                        'dias': round(dias_cobertura, 1),
                        'avg_diaria': round(avg_diaria, 2),
                    })
            quiebres_proximos.sort(key=lambda x: x['dias'])
            top_quiebres = quiebres_proximos[:10]
            n_quiebres_total = len(quiebres_proximos)

            # ── Card 3: Top vendidos del mes anterior ───────────────────────
            anio_prev, mes_prev = _mes_anterior_ym()
            top_vendidos_q = (
                session.query(
                    ObsProducto.observer_id.label('pid'),
                    ObsProducto.descripcion.label('desc'),
                    ObsProducto.nombre_droga_observer.label('droga_id'),
                    ObsLaboratorio.descripcion.label('lab'),
                    func.sum(ObsVentaMensual.unidades).label('u'),
                )
                .join(ObsVentaMensual,
                      ObsVentaMensual.producto_observer == ObsProducto.observer_id)
                .outerjoin(ObsLaboratorio,
                           ObsLaboratorio.observer_id == ObsProducto.laboratorio_observer)
                .filter(ObsVentaMensual.anio == anio_prev)
                .filter(ObsVentaMensual.mes == mes_prev)
                .filter(ObsProducto.fecha_baja.is_(None))
                .group_by(ObsProducto.observer_id, ObsProducto.descripcion,
                          ObsProducto.nombre_droga_observer,
                          ObsLaboratorio.descripcion)
                .order_by(func.sum(ObsVentaMensual.unidades).desc())
                .limit(10)
            )
            top_vendidos = [{
                'producto_id': r.pid,
                'descripcion': r.desc,
                'droga_id': r.droga_id,
                'lab': r.lab or '—',
                'unidades': int(r.u or 0),
            } for r in top_vendidos_q.all()]

            # ── Card 4: Top labs del mes anterior ───────────────────────────
            top_labs_q = (
                session.query(
                    ObsLaboratorio.observer_id.label('lab_id'),
                    ObsLaboratorio.descripcion.label('lab'),
                    func.sum(ObsVentaMensual.unidades).label('u'),
                    func.sum(ObsVentaMensual.monto).label('m'),
                )
                .join(ObsProducto,
                      ObsProducto.laboratorio_observer == ObsLaboratorio.observer_id)
                .join(ObsVentaMensual,
                      ObsVentaMensual.producto_observer == ObsProducto.observer_id)
                .filter(ObsVentaMensual.anio == anio_prev)
                .filter(ObsVentaMensual.mes == mes_prev)
                .filter(ObsProducto.fecha_baja.is_(None))
                .group_by(ObsLaboratorio.observer_id, ObsLaboratorio.descripcion)
                .order_by(func.sum(ObsVentaMensual.unidades).desc())
                .limit(10)
            )
            top_labs = [{
                'lab_id': r.lab_id,
                'lab': r.lab,
                'unidades': int(r.u or 0),
                'monto': float(r.m or 0),
            } for r in top_labs_q.all()]

            # Mes en español para mostrar al usuario.
            meses = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
                     'julio', 'agosto', 'septiembre', 'octubre',
                     'noviembre', 'diciembre']
            mes_label = f'{meses[mes_prev - 1]} {anio_prev}'

        return render_template('bi_tablero.html',
                               n_bajo_min=n_bajo_min,
                               perdida_pesos_total=round(perdida_pesos_total, 0),
                               n_quiebres_total=n_quiebres_total,
                               top_quiebres=top_quiebres,
                               top_vendidos=top_vendidos,
                               top_labs=top_labs,
                               mes_label=mes_label)
