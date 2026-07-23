"""Consulta de estadísticas de una droga / monodroga (mobile).

Drill-down desde /consulta-os o /consulta-medico cuando agrupás por droga.
Replica la UX de consulta-medico: KPIs + top productos + top médicos +
top OS + serie 12 meses + donut.

Endpoints:
  GET /consulta-droga/<droga_id>      → pantalla resultado (mobile-first)
"""
from datetime import date, timedelta

from flask import jsonify, render_template, request
from flask_login import login_required
from sqlalchemy import desc, func

import database
from helpers import excluir_no_medicamentos_ovd, ventas_periodo_filter


def init_app(app):

    @app.route('/consulta-droga')
    @login_required
    def consulta_droga():
        """Pantalla de entrada mobile: input de búsqueda por nombre. Mismo
        patrón que /consulta-os, /consulta-medico, etc."""
        return render_template('consulta_droga.html')

    @app.route('/api/consulta-droga/buscar')
    @login_required
    def api_consulta_droga_buscar():
        """Autocomplete de drogas por nombre. Multi-token AND. Top 20."""
        q = (request.args.get('q') or '').strip()
        if len(q) < 2:
            return jsonify({'items': []})
        tokens = [t for t in q.split() if t]
        with database.get_db() as session:
            base = session.query(database.ObsNombreDroga)
            for t in tokens:
                base = base.filter(database.ObsNombreDroga.descripcion.ilike(f'%{t}%'))
            results = base.order_by(database.ObsNombreDroga.descripcion).limit(20).all()
            return jsonify({'items': [{'id': d.observer_id, 'nombre': d.descripcion}
                                       for d in results]})

    @app.route('/consulta-droga/<int:droga_id>')
    @login_required
    def consulta_droga_detalle(droga_id):
        try:
            n_days = max(7, min(365, int(request.args.get('dias', 90))))
        except (ValueError, TypeError):
            n_days = 90
        hasta = date.today()
        desde = hasta - timedelta(days=n_days)

        info = {'droga_id': droga_id, 'encontrado': False, 'n_days': n_days}
        with database.get_db() as session:
            droga = session.get(database.ObsNombreDroga, droga_id)
            if not droga:
                return render_template('consulta_droga_resultado.html', info=info)

            info.update({
                'encontrado': True,
                'nombre': droga.descripcion or f'#{droga_id}',
            })

            # Base: ventas de productos cuya monodroga es esta. Requiere JOIN
            # con ObsProducto para filtrar por nombre_droga_observer.
            base = (session.query(database.ObsVentaDetalle)
                    .join(database.ObsProducto,
                          database.ObsProducto.observer_id == database.ObsVentaDetalle.producto_observer)
                    .filter(database.ObsProducto.nombre_droga_observer == droga_id,
                            ventas_periodo_filter(database.ObsVentaDetalle, desde, hasta),
                            excluir_no_medicamentos_ovd(database.ObsVentaDetalle,
                                                        database.ObsProducto, session)))

            # KPIs
            kpi_row = base.with_entities(
                func.coalesce(func.sum(database.ObsVentaDetalle.cantidad), 0).label('uds'),
                func.coalesce(func.sum(database.ObsVentaDetalle.importe), 0).label('importe'),
                func.count(database.ObsVentaDetalle.id_operacion.distinct()).label('ops'),
                func.count(database.ObsVentaDetalle.cliente_observer.distinct()).label('pacientes'),
                func.count(database.ObsVentaDetalle.medico_observer.distinct()).label('medicos'),
            ).first()
            info['kpis'] = {
                'unidades':  float(kpi_row.uds or 0),
                'importe':   float(kpi_row.importe or 0),
                'ops':       int(kpi_row.ops or 0),
                'pacientes': int(kpi_row.pacientes or 0),
                'medicos':   int(kpi_row.medicos or 0),
            }

            # Top 10 productos con esta droga
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

            # Top 10 médicos prescriptores
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
                          .join(database.ObsProducto,
                                database.ObsProducto.observer_id == database.ObsVentaDetalle.producto_observer)
                          .filter(database.ObsProducto.nombre_droga_observer == droga_id,
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

        return render_template('consulta_droga_resultado.html', info=info)
