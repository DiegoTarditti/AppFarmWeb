"""Flujo de fondos — predicción plana de ingresos vs egresos.

Modelo (Fase 3 — predicción sin cronograma):
- Ingreso semanal = SUM(importe neto ventas 12m) / 52.
- Egreso semanal por LAB = ventas_neto_12m del lab / 52 × (1 - dto_lab).
- Egreso semanal por DROG = compras_12m (suma Invoice.total FAC) / 52 × (1 - dto_drog).
- Cada semana del horizonte muestra los mismos totales (predicción plana).
- Estacionalidad/cronograma no se considera en esta vista.
"""
from datetime import date as _date
from datetime import timedelta

from flask import jsonify, render_template, request
from flask_login import login_required
from sqlalchemy import func, or_

from database import (
    Invoice,
    Laboratorio,
    ObsProducto,
    ObsVentaDetalle,
    ObsVentaMensual,
    ProveedorCronograma,
    Provider,
    get_db,
)


def _lunes_de(d):
    return d - timedelta(days=d.weekday())


def init_app(app):

    @app.route('/finanzas/flujo')
    @login_required
    def flujo_fondos():
        """Vista semanal: ingreso vs egreso (predicción plana por partner)."""
        try:
            n_semanas = int(request.args.get('n', 8))
        except (TypeError, ValueError):
            n_semanas = 8
        n_semanas = max(2, min(n_semanas, 26))

        hoy = _date.today()
        lunes_inicial = _lunes_de(hoy)

        with get_db() as session:
            # ─── Ingreso semanal (NETO 12m / 52) ─────────────────────────
            anio_actual, mes_actual = hoy.year, hoy.month
            keys_12m = []
            for _i in range(0, 12):
                m = mes_actual - _i
                y = anio_actual
                if m <= 0:
                    m += 12
                    y -= 1
                keys_12m.append(y * 100 + m)
            neto_12m_q = (session.query(func.coalesce(func.sum(ObsVentaDetalle.importe), 0))
                          .filter((ObsVentaDetalle.anio * 100 + ObsVentaDetalle.mes).in_(keys_12m))
                          .scalar())
            try:
                total_anual = float(neto_12m_q or 0)
            except (TypeError, ValueError):
                total_anual = 0.0
            if total_anual <= 0:
                # Fallback bruto si el detalle está vacío — mismo filtro 12m.
                total_bruto_q = session.query(func.coalesce(
                    func.sum(ObsVentaMensual.monto), 0)).filter(
                    (ObsVentaMensual.anio * 100 + ObsVentaMensual.mes).in_(keys_12m)
                ).scalar()
                try:
                    total_anual = float(total_bruto_q or 0)
                except (TypeError, ValueError):
                    total_anual = 0.0
            ingreso_semanal = total_anual / 52.0 if total_anual else 0.0

            # ─── Egreso por LAB (predicción) ─────────────────────────────
            # ventas_neto_12m por lab (joining ObsProducto.laboratorio_observer)
            labs_q = (session.query(
                        Laboratorio.id,
                        Laboratorio.nombre,
                        Laboratorio.descuento_base,
                        func.coalesce(func.sum(ObsVentaDetalle.importe), 0).label('ventas_12m'))
                      .join(ObsProducto,
                            ObsProducto.laboratorio_observer == Laboratorio.observer_id)
                      .join(ObsVentaDetalle,
                            ObsVentaDetalle.producto_observer == ObsProducto.observer_id)
                      .filter(Laboratorio.observer_id.isnot(None),
                              (ObsVentaDetalle.anio * 100 + ObsVentaDetalle.mes).in_(keys_12m))
                      .group_by(Laboratorio.id, Laboratorio.nombre, Laboratorio.descuento_base)
                      .having(func.coalesce(func.sum(ObsVentaDetalle.importe), 0) > 0)
                      .all())
            partners_lab = []
            for lab in labs_q:
                ventas = float(lab.ventas_12m or 0)
                bruto_sem = ventas / 52.0
                dto_pct = float(lab.descuento_base) if lab.descuento_base else 0.0
                neto_sem = bruto_sem * (1.0 - dto_pct / 100.0)
                partners_lab.append({
                    'tipo': 'laboratorio',
                    'id': lab.id,
                    'nombre': lab.nombre,
                    'ventas_12m': ventas,
                    'bruto_sem': bruto_sem,
                    'dto_pct': dto_pct,
                    'neto_sem': neto_sem,
                })

            # ─── Egreso por DROG (predicción) ────────────────────────────
            # compras_12m: sum Invoice.total FAC últimos 12 meses por proveedor.
            # Usamos el mismo período que keys_12m (primer día del mes más antiguo).
            oldest_key = min(keys_12m)
            hace_12m = _date(oldest_key // 100, oldest_key % 100, 1)
            drogs_q = (session.query(
                        Provider.id,
                        Provider.razon_social,
                        Provider.descuento_sin_transfer,
                        Provider.descuento_con_transfer,
                        func.coalesce(func.sum(Invoice.total), 0).label('compras_12m'))
                       .join(Invoice, or_(
                           Invoice.proveedor_razon == Provider.razon_social,
                           Invoice.proveedor_cuit == Provider.cuit,
                       ))
                       .filter(Invoice.fecha >= hace_12m,
                               or_(Invoice.tipo_comprobante == 'FAC',
                                   Invoice.tipo_comprobante.is_(None)))
                       .group_by(Provider.id, Provider.razon_social,
                                 Provider.descuento_sin_transfer,
                                 Provider.descuento_con_transfer)
                       .having(func.coalesce(func.sum(Invoice.total), 0) > 0)
                       .all())
            partners_drog = []
            for d in drogs_q:
                compras = float(d.compras_12m or 0)
                bruto_sem = compras / 52.0
                dto_raw = d.descuento_sin_transfer if d.descuento_sin_transfer is not None else (d.descuento_con_transfer or 0)
                dto_pct = float(dto_raw) if dto_raw else 0.0
                neto_sem = bruto_sem * (1.0 - dto_pct / 100.0)
                partners_drog.append({
                    'tipo': 'drogueria',
                    'id': d.id,
                    'nombre': d.razon_social,
                    'ventas_12m': compras,  # acá es compras, no ventas
                    'bruto_sem': bruto_sem,
                    'dto_pct': dto_pct,
                    'neto_sem': neto_sem,
                })

            partners = partners_lab + partners_drog
            partners.sort(key=lambda p: p['neto_sem'], reverse=True)

            egreso_bruto_sem = sum(p['bruto_sem'] for p in partners)
            egreso_neto_sem = sum(p['neto_sem'] for p in partners)
            saldo_sem = ingreso_semanal - egreso_neto_sem

            # ─── Semanas (todas iguales) ─────────────────────────────────
            semanas = []
            for i in range(n_semanas):
                w_lunes = lunes_inicial + timedelta(days=i * 7)
                w_domingo = w_lunes + timedelta(days=6)
                semanas.append({
                    'idx': i,
                    'lunes': w_lunes,
                    'domingo': w_domingo,
                    'label': f"{w_lunes.strftime('%d/%m')} – {w_domingo.strftime('%d/%m')}",
                    'es_actual': w_lunes <= hoy <= w_domingo,
                    'ingreso_proy': ingreso_semanal,
                    'egreso_bruto': egreso_bruto_sem,
                    'egreso_neto': egreso_neto_sem,
                    'saldo': saldo_sem,
                })

        return render_template('flujo_fondos.html',
                               semanas=semanas,
                               partners=partners,
                               partners_lab=partners_lab,
                               partners_drog=partners_drog,
                               n_semanas=n_semanas,
                               ingreso_semanal_base=ingreso_semanal,
                               total_anual=total_anual,
                               egreso_bruto_sem=egreso_bruto_sem,
                               egreso_neto_sem=egreso_neto_sem,
                               saldo_sem=saldo_sem,
                               hoy_iso=hoy.isoformat())

    @app.route('/api/flujo/cronograma-precarga')
    @login_required
    def api_flujo_cronograma_precarga():
        """Devuelve, por partner con cronograma activo+programado, qué semanas
        del horizonte caen sus pedidos. Formato:
            {result: {"<tipo>-<id>": [bool x N], ...}, horizonte: N}
        """
        try:
            n_semanas = int(request.args.get('n', 8))
        except (TypeError, ValueError):
            n_semanas = 8
        n_semanas = max(2, min(n_semanas, 26))

        hoy = _date.today()
        lunes_inicial = _lunes_de(hoy)
        # bounds: [lunes sem0, lunes sem1, ..., lunes semN] (N+1 puntos)
        bounds = [lunes_inicial + timedelta(days=i * 7) for i in range(n_semanas + 1)]

        with get_db() as session:
            crons = session.query(ProveedorCronograma).filter(
                ProveedorCronograma.activo.is_(True),
                ProveedorCronograma.tipo_pedido == 'programado',
            ).all()
            result = {}
            for c in crons:
                if not c.proxima_fecha or not c.cadencia_dias or c.cadencia_dias <= 0:
                    continue
                weeks = [False] * n_semanas
                # Retroceder hasta antes del horizonte
                d = c.proxima_fecha
                while d > bounds[0]:
                    d = d - timedelta(days=c.cadencia_dias)
                # Avanzar marcando semanas
                while d < bounds[-1]:
                    if d >= bounds[0]:
                        for i in range(n_semanas):
                            if bounds[i] <= d < bounds[i + 1]:
                                weeks[i] = True
                                break
                    d = d + timedelta(days=c.cadencia_dias)
                if any(weeks):
                    key = f"{c.partner_tipo}-{c.proveedor_id}"
                    result[key] = weeks
        return jsonify({'result': result, 'horizonte': n_semanas,
                        'partners_con_cron': len(result)})
