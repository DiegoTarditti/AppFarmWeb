"""Panel del gerente — vista estratégica unificada de stock, ventas y robot."""
from datetime import date, datetime

from flask import render_template
from flask_login import login_required
from sqlalchemy import func

import database
from database import (
    ObsLaboratorio,
    ObsProducto,
    ObsStock,
    ObsVentaMensual,
    RowaSnapshot,
    get_db,
)

_MESES_ES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
             'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']


def _saludo():
    hora = datetime.now().hour
    if hora < 12:
        return "Buenos días"
    if hora < 19:
        return "Buenas tardes"
    return "Buenas noches"


def _fecha_larga(d):
    return f"{d.day} de {_MESES_ES[d.month - 1]} de {d.year}"


def init_app(app):

    @app.route('/gerente')
    @login_required
    def gerente_index():
        hoy = date.today()
        anio_act, mes_act = hoy.year, hoy.month
        anio_ant = anio_act - 1

        with get_db() as session:

            # ── Subqueries compartidas ────────────────────────────────────────
            stock_q = (
                session.query(
                    ObsStock.producto_observer.label('pid'),
                    func.sum(ObsStock.stock_actual).label('stock'),
                    func.sum(ObsStock.minimo).label('minimo'),
                )
                .filter(ObsStock.minimo.isnot(None), ObsStock.minimo > 0)
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

            # ── KPI 1: Bajo mínimo + pérdida estimada ────────────────────────
            n_bajo_min = 0
            perdida_estimada = 0.0
            try:
                rows = (
                    session.query(
                        stock_q.c.stock, stock_q.c.minimo,
                        func.coalesce(ventas_12m.c.u12m, 0).label('u12m'),
                        func.coalesce(ventas_12m.c.m12m, 0).label('m12m'),
                    )
                    .outerjoin(ventas_12m, ventas_12m.c.pid == stock_q.c.pid)
                    .join(ObsProducto, ObsProducto.observer_id == stock_q.c.pid)
                    .filter(ObsProducto.fecha_baja.is_(None))
                    .filter(stock_q.c.stock < stock_q.c.minimo)
                    .filter(
                        ObsProducto.id_tipo_venta_control.in_(['L', 'R', 'A']) |
                        ObsProducto.id_tipo_venta_control.is_(None)
                    )
                    .filter(ventas_12m.c.u12m > 0)
                    .filter(stock_q.c.minimo <= ventas_12m.c.u12m / 2)
                    .all()
                )
                n_bajo_min = len(rows)
                for r in rows:
                    u12m = int(r.u12m or 0)
                    m12m = float(r.m12m or 0)
                    minimo = int(r.minimo or 0)
                    stock = int(r.stock or 0)
                    avg_mensual = u12m / 12.0 if u12m else 0.0
                    precio = m12m / u12m if u12m else 0.0
                    factor = min(1.0, max(0.0, (minimo - stock) / minimo)) if minimo else 0.0
                    perdida_estimada += avg_mensual * factor * precio
            except Exception:
                pass

            # ── KPI 2 + Alertas: Quiebres ≤7d ───────────────────────────────
            n_quiebres_7d = 0
            alertas = []
            try:
                rows_q = (
                    session.query(
                        ObsProducto.observer_id.label('pid'),
                        ObsProducto.descripcion.label('desc'),
                        ObsLaboratorio.descripcion.label('lab'),
                        stock_q.c.stock,
                        ventas_12m.c.u12m,
                    )
                    .join(stock_q, stock_q.c.pid == ObsProducto.observer_id)
                    .join(ventas_12m, ventas_12m.c.pid == ObsProducto.observer_id)
                    .outerjoin(ObsLaboratorio,
                               ObsLaboratorio.observer_id == ObsProducto.laboratorio_observer)
                    .filter(ObsProducto.fecha_baja.is_(None))
                    .filter(stock_q.c.stock > 0)
                    .filter(ventas_12m.c.u12m > 6)
                    .all()
                )
                for r in rows_q:
                    avg_dia = int(r.u12m or 0) / 365.0
                    if not avg_dia:
                        continue
                    dias = int(r.stock or 0) / avg_dia
                    if dias <= 7:
                        n_quiebres_7d += 1
                        alertas.append({
                            'pid': r.pid,
                            'desc': (r.desc or '—')[:50],
                            'lab': r.lab or '—',
                            'stock': int(r.stock or 0),
                            'dias': round(dias, 1),
                        })
                alertas.sort(key=lambda x: x['dias'])
                alertas = alertas[:10]
            except Exception:
                pass

            # ── KPI 3: Facturación mes actual vs mismo mes año anterior ───────
            facturacion_mes = 0.0
            facturacion_mismo_mes_ant = 0.0
            try:
                facturacion_mes = float(
                    session.query(func.sum(ObsVentaMensual.monto))
                    .filter(ObsVentaMensual.anio == anio_act,
                            ObsVentaMensual.mes == mes_act)
                    .scalar() or 0
                )
                facturacion_mismo_mes_ant = float(
                    session.query(func.sum(ObsVentaMensual.monto))
                    .filter(ObsVentaMensual.anio == anio_ant,
                            ObsVentaMensual.mes == mes_act)
                    .scalar() or 0
                )
            except Exception:
                pass

            # ── KPI 4: Robot Rowa ─────────────────────────────────────────────
            rowa_ts = None
            rowa_horas = None
            rowa_n_art = 0
            rowa_n_sin_stock = 0
            try:
                ts_row = (
                    session.query(RowaSnapshot.tomado_en)
                    .order_by(RowaSnapshot.tomado_en.desc())
                    .first()
                )
                if ts_row:
                    rowa_ts = ts_row[0]
                    rowa_horas = round((datetime.now() - rowa_ts).total_seconds() / 3600, 1)
                    rowa_n_art = (
                        session.query(func.count(RowaSnapshot.article_id))
                        .filter(RowaSnapshot.tomado_en == rowa_ts)
                        .scalar() or 0
                    )
                    rowa_n_sin_stock = (
                        session.query(func.count(RowaSnapshot.article_id))
                        .filter(RowaSnapshot.tomado_en == rowa_ts,
                                RowaSnapshot.cantidad == 0)
                        .scalar() or 0
                    )
            except Exception:
                pass

        # Delta YoY facturación
        delta_pct = None
        if facturacion_mismo_mes_ant > 0:
            delta_pct = round(
                (facturacion_mes - facturacion_mismo_mes_ant) / facturacion_mismo_mes_ant * 100, 1
            )

        return render_template(
            'gerente.html',
            saludo=_saludo(),
            fecha_hoy=_fecha_larga(hoy),
            n_bajo_min=n_bajo_min,
            perdida_estimada=round(perdida_estimada, 0),
            n_quiebres_7d=n_quiebres_7d,
            facturacion_mes=facturacion_mes,
            delta_pct=delta_pct,
            mes_label=_MESES_ES[mes_act - 1],
            rowa_ts=rowa_ts,
            rowa_horas=rowa_horas,
            rowa_n_art=rowa_n_art,
            rowa_n_sin_stock=rowa_n_sin_stock,
            alertas=alertas,
        )
