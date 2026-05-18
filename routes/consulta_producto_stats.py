"""Consulta de estadísticas de un producto (Observer ID) — mobile.

Distinto de /consulta-producto (que es scan de troquel / EAN). Esta entra
con observer_id desde drills como /consulta-os o /consulta-droga, y muestra
top médicos prescriptores, top OS, top pacientes, serie 12 meses, etc.

Endpoints:
  GET /consulta-producto-stats/<int:observer_id>  → mobile
"""
from datetime import date, timedelta

from flask import render_template, request
from flask_login import login_required
from sqlalchemy import desc, func

import database
from helpers import (
    excluir_no_medicamentos_ovd,
    medicos_observer_ids_compartidos,
    ventas_periodo_filter,
)


def init_app(app):

    @app.route('/consulta-producto-stats/<int:observer_id>')
    @login_required
    def consulta_producto_stats(observer_id):
        try:
            n_days = max(7, min(365, int(request.args.get('dias', 90))))
        except (ValueError, TypeError):
            n_days = 90
        # Filtro opcional: si se pasa ?medico_id=N, restringe stats a ese médico
        # (usa medicos_observer_ids_compartidos para agrupar variantes por matrícula).
        try:
            medico_id = int(request.args.get('medico_id') or 0) or None
        except (ValueError, TypeError):
            medico_id = None
        hasta = date.today()
        desde = hasta - timedelta(days=n_days)

        info = {'observer_id': observer_id, 'encontrado': False, 'n_days': n_days,
                'filtro_medico_id': medico_id, 'filtro_medico_nombre': None}
        with database.get_db() as session:
            prod = session.get(database.ObsProducto, observer_id)
            if not prod:
                return render_template('consulta_producto_stats_resultado.html', info=info)

            # Resolver codigo_barra para link a la pantalla scan existente.
            cb = None
            p_local = (session.query(database.Producto)
                       .filter(database.Producto.observer_id == observer_id).first())
            if p_local and p_local.codigo_barra:
                cb = p_local.codigo_barra
            else:
                row = (session.query(database.ObsCodigoBarras.codigo_barras)
                       .filter(database.ObsCodigoBarras.producto_observer == observer_id,
                               database.ObsCodigoBarras.fecha_baja.is_(None),
                               database.ObsCodigoBarras.orden == 1).first())
                if row:
                    cb = row[0]

            info.update({
                'encontrado':   True,
                'nombre':       prod.descripcion or f'#{observer_id}',
                'codigo_barra': cb,
            })

            # Si vino ?medico_id=N, resolver IDs compartidos (variantes por matrícula)
            # y mostrar el nombre del médico para el banner contextual.
            medico_ids_filter = None
            if medico_id:
                med = session.get(database.ObsMedico, medico_id)
                if med:
                    info['filtro_medico_nombre'] = med.nombre or f'#{medico_id}'
                    medico_ids_filter = medicos_observer_ids_compartidos(session, medico_id)

            base = (session.query(database.ObsVentaDetalle)
                    .filter(database.ObsVentaDetalle.producto_observer == observer_id,
                            ventas_periodo_filter(database.ObsVentaDetalle, desde, hasta),
                            excluir_no_medicamentos_ovd(database.ObsVentaDetalle,
                                                        database.ObsProducto, session)))
            if medico_ids_filter:
                base = base.filter(database.ObsVentaDetalle.medico_observer.in_(medico_ids_filter))

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

            # Top 5 pacientes (clientes recurrentes de este producto)
            cli_rows = (base.with_entities(
                            database.ObsVentaDetalle.cliente_observer,
                            func.coalesce(func.sum(database.ObsVentaDetalle.cantidad), 0).label('uds'),
                        )
                        .filter(database.ObsVentaDetalle.cliente_observer.isnot(None))
                        .group_by(database.ObsVentaDetalle.cliente_observer)
                        .order_by(desc('uds'))
                        .limit(5).all())
            cli_ids = [r[0] for r in cli_rows if r[0]]
            cli_map = {}
            if cli_ids:
                for c in (session.query(database.ObsCliente)
                          .filter(database.ObsCliente.observer_id.in_(cli_ids)).all()):
                    cli_map[c.observer_id] = c.apellido_nombre or ''
            info['top_pacientes'] = [{
                'observer_id': r[0],
                'nombre':      cli_map.get(r[0], f'#{r[0]}'),
                'unidades':    float(r[1] or 0),
            } for r in cli_rows]

            # Serie 12 meses (también respeta filtro de médico si vino).
            desde_serie = hasta - timedelta(days=365)
            ym = (func.extract('year', database.ObsVentaDetalle.fecha_estadistica) * 100
                  + func.extract('month', database.ObsVentaDetalle.fecha_estadistica))
            serie_q = (session.query(
                              ym.label('ym'),
                              func.coalesce(func.sum(database.ObsVentaDetalle.cantidad), 0).label('uds'))
                          .filter(database.ObsVentaDetalle.producto_observer == observer_id,
                                  database.ObsVentaDetalle.fecha_estadistica >= desde_serie,
                                  database.ObsVentaDetalle.fecha_estadistica <= hasta,
                                  excluir_no_medicamentos_ovd(database.ObsVentaDetalle,
                                                              database.ObsProducto, session)))
            if medico_ids_filter:
                serie_q = serie_q.filter(database.ObsVentaDetalle.medico_observer.in_(medico_ids_filter))
            serie_rows = serie_q.group_by('ym').order_by('ym').all()
            info['serie'] = [{
                'anio':     int(r[0] // 100),
                'mes':      int(r[0] % 100),
                'unidades': float(r[1] or 0),
            } for r in serie_rows]

        return render_template('consulta_producto_stats_resultado.html', info=info)
