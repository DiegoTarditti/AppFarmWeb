"""Consulta de estadísticas de un paciente (cliente Observer) — mobile.

Drill-down desde /consulta-os u otras consultas. Replica la UX:
KPIs + top productos comprados + top médicos que le recetan +
top OS + serie 12 meses + donut.

Endpoints:
  GET /consulta-paciente/<cliente_id>  → pantalla resultado (mobile)
"""
from datetime import date, timedelta

from flask import render_template, request
from flask_login import login_required
from sqlalchemy import desc, func

import database
from helpers import excluir_no_medicamentos_ovd, ventas_periodo_filter


def init_app(app):

    @app.route('/consulta-paciente/<int:cliente_id>')
    @login_required
    def consulta_paciente_detalle(cliente_id):
        try:
            n_days = max(7, min(365, int(request.args.get('dias', 90))))
        except (ValueError, TypeError):
            n_days = 90
        hasta = date.today()
        desde = hasta - timedelta(days=n_days)

        info = {'cliente_id': cliente_id, 'encontrado': False, 'n_days': n_days}
        with database.get_db() as session:
            cli = session.get(database.ObsCliente, cliente_id)
            if not cli:
                return render_template('consulta_paciente_resultado.html', info=info)

            doc_str = ''
            if cli.documento_numero:
                doc_str = f'{cli.documento_tipo or ""} {cli.documento_numero}'.strip()
            info.update({
                'encontrado': True,
                'nombre':    cli.apellido_nombre or f'#{cliente_id}',
                'documento': doc_str,
                'localidad': cli.localidad or '',
            })

            base = (session.query(database.ObsVentaDetalle)
                    .filter(database.ObsVentaDetalle.cliente_observer == cliente_id,
                            ventas_periodo_filter(database.ObsVentaDetalle, desde, hasta),
                            excluir_no_medicamentos_ovd(database.ObsVentaDetalle,
                                                        database.ObsProducto, session)))

            # KPIs
            kpi_row = base.with_entities(
                func.coalesce(func.sum(database.ObsVentaDetalle.cantidad), 0).label('uds'),
                func.coalesce(func.sum(database.ObsVentaDetalle.importe), 0).label('importe'),
                func.count(database.ObsVentaDetalle.id_operacion.distinct()).label('ops'),
                func.count(database.ObsVentaDetalle.producto_observer.distinct()).label('productos'),
                func.count(database.ObsVentaDetalle.medico_observer.distinct()).label('medicos'),
            ).first()
            info['kpis'] = {
                'unidades':  float(kpi_row.uds or 0),
                'importe':   float(kpi_row.importe or 0),
                'ops':       int(kpi_row.ops or 0),
                'productos': int(kpi_row.productos or 0),
                'medicos':   int(kpi_row.medicos or 0),
            }

            # Top 10 productos comprados
            prod_rows = (base.with_entities(
                            database.ObsVentaDetalle.producto_observer,
                            func.coalesce(func.sum(database.ObsVentaDetalle.cantidad), 0).label('uds'),
                            func.coalesce(func.sum(database.ObsVentaDetalle.importe), 0).label('imp'),
                         )
                         .group_by(database.ObsVentaDetalle.producto_observer)
                         .order_by(desc('uds'))
                         .limit(10).all())
            prod_ids = [r[0] for r in prod_rows if r[0]]
            desc_map = {}
            if prod_ids:
                for op in (session.query(database.ObsProducto)
                           .filter(database.ObsProducto.observer_id.in_(prod_ids)).all()):
                    desc_map[op.observer_id] = op.descripcion or ''
            info['top_productos'] = [{
                'observer_id': r[0],
                'nombre':      desc_map.get(r[0], f'#{r[0]}'),
                'unidades':    float(r[1] or 0),
                'importe':     float(r[2] or 0),
            } for r in prod_rows if r[0]]

            # Top 10 médicos
            med_rows = (base.with_entities(
                            database.ObsVentaDetalle.medico_observer,
                            func.coalesce(func.sum(database.ObsVentaDetalle.cantidad), 0).label('uds'),
                        )
                        .filter(database.ObsVentaDetalle.medico_observer.isnot(None))
                        .group_by(database.ObsVentaDetalle.medico_observer)
                        .order_by(desc('uds'))
                        .limit(10).all())
            med_ids = [r[0] for r in med_rows if r[0]]
            med_map = {}
            if med_ids:
                for m in (session.query(database.ObsMedico)
                          .filter(database.ObsMedico.observer_id.in_(med_ids)).all()):
                    med_map[m.observer_id] = m.nombre or ''
            info['top_medicos'] = [{
                'observer_id': r[0],
                'nombre':      med_map.get(r[0], f'#{r[0]}'),
                'unidades':    float(r[1] or 0),
            } for r in med_rows]

            # Top 5 OS
            os_rows = (base.with_entities(
                            database.ObsVentaDetalle.obra_social_observer,
                            func.coalesce(func.sum(database.ObsVentaDetalle.cantidad), 0).label('uds'),
                       )
                       .group_by(database.ObsVentaDetalle.obra_social_observer)
                       .order_by(desc('uds'))
                       .limit(5).all())
            os_ids = [r[0] for r in os_rows if r[0]]
            os_map = {}
            if os_ids:
                for o in (session.query(database.ObsObraSocial)
                          .filter(database.ObsObraSocial.observer_id.in_(os_ids)).all()):
                    os_map[o.observer_id] = o.descripcion or ''
            info['top_os'] = [{
                'observer_id': r[0],
                'nombre':      os_map.get(r[0], f'#{r[0]}' if r[0] else 'Particular'),
                'unidades':    float(r[1] or 0),
            } for r in os_rows]

            # Serie 12 meses
            desde_serie = hasta - timedelta(days=365)
            ym = (func.extract('year', database.ObsVentaDetalle.fecha_estadistica) * 100
                  + func.extract('month', database.ObsVentaDetalle.fecha_estadistica))
            serie_rows = (session.query(
                              ym.label('ym'),
                              func.coalesce(func.sum(database.ObsVentaDetalle.cantidad), 0).label('uds'))
                          .filter(database.ObsVentaDetalle.cliente_observer == cliente_id,
                                  database.ObsVentaDetalle.fecha_estadistica >= desde_serie,
                                  database.ObsVentaDetalle.fecha_estadistica <= hasta,
                                  excluir_no_medicamentos_ovd(database.ObsVentaDetalle,
                                                              database.ObsProducto, session))
                          .group_by('ym').order_by('ym').all())
            info['serie'] = [{
                'anio':     int(r[0] // 100),
                'mes':      int(r[0] % 100),
                'unidades': float(r[1] or 0),
            } for r in serie_rows]

        return render_template('consulta_paciente_resultado.html', info=info)
