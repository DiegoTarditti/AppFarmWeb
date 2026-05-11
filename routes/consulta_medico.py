"""Consulta de estadísticas de un médico (mobile).

Pantalla mobile-first: buscás médico por nombre (autocomplete) y ves
KPIs + top productos recetados + chart histórico. Reusa endpoints
existentes de informes/buscar-medico y ObsVentaDetalle.

Endpoints:
  GET /consulta-medico              → pantalla entrada (autocomplete)
  GET /consulta-medico/<medico_id>  → pantalla resultado con KPIs + tops
"""
from datetime import date, timedelta

from flask import jsonify, render_template, request
from sqlalchemy import desc, func, or_

import database


def init_app(app):

    @app.route('/consulta-medico')
    def consulta_medico():
        return render_template('consulta_medico.html')

    @app.route('/consulta-medico/<int:medico_id>')
    def consulta_medico_detalle(medico_id):
        """KPIs y top productos del médico en los últimos N días."""
        try:
            n_days = max(7, min(365, int(request.args.get('dias', 90))))
        except (ValueError, TypeError):
            n_days = 90
        hasta = date.today()
        desde = hasta - timedelta(days=n_days)

        info = {'medico_id': medico_id, 'encontrado': False, 'n_days': n_days}
        with database.get_db() as session:
            medico = session.get(database.ObsMedico, medico_id)
            if not medico:
                return render_template('consulta_medico_resultado.html', info=info)

            info.update({
                'encontrado': True,
                'nombre': medico.nombre or '',
                'cuit': medico.cuit or '',
                'matricula_provincial': medico.matricula_provincial or '',
                'matricula_nacional': medico.matricula_nacional or '',
                'baja': bool(medico.fecha_baja),
            })

            # KPIs en el rango.
            base = (session.query(database.ObsVentaDetalle)
                    .filter(database.ObsVentaDetalle.medico_observer == medico_id,
                            database.ObsVentaDetalle.fecha_estadistica >= desde,
                            database.ObsVentaDetalle.fecha_estadistica <= hasta,
                            or_(database.ObsVentaDetalle.tipo_operacion == 'V',
                                database.ObsVentaDetalle.tipo_operacion.is_(None))))

            kpi_row = base.with_entities(
                func.coalesce(func.sum(database.ObsVentaDetalle.cantidad_facturada), 0).label('uds'),
                func.coalesce(func.sum(database.ObsVentaDetalle.importe), 0).label('importe'),
                func.count(database.ObsVentaDetalle.id_operacion.distinct()).label('ops'),
                func.count(database.ObsVentaDetalle.cliente_observer.distinct()).label('clientes'),
            ).first()
            info['kpis'] = {
                'unidades': float(kpi_row.uds or 0),
                'importe':  float(kpi_row.importe or 0),
                'ops':      int(kpi_row.ops or 0),
                'clientes': int(kpi_row.clientes or 0),
            }

            # Top 10 productos recetados (sum cantidad).
            top_rows = (base.with_entities(
                            database.ObsVentaDetalle.producto_observer,
                            func.coalesce(func.sum(database.ObsVentaDetalle.cantidad_facturada), 0).label('uds'),
                            func.coalesce(func.sum(database.ObsVentaDetalle.importe), 0).label('imp'),
                        )
                        .group_by(database.ObsVentaDetalle.producto_observer)
                        .order_by(desc('uds'))
                        .limit(10).all())
            prod_ids = [r[0] for r in top_rows if r[0]]
            prod_map = {}
            if prod_ids:
                for op in (session.query(database.ObsProducto)
                           .filter(database.ObsProducto.observer_id.in_(prod_ids)).all()):
                    prod_map[op.observer_id] = op.descripcion or ''
            info['top_productos'] = [{
                'observer_id': r[0],
                'nombre':      prod_map.get(r[0], f'#{r[0]}'),
                'unidades':    float(r[1] or 0),
                'importe':     float(r[2] or 0),
            } for r in top_rows if r[0]]

            # Top OS atendidas (saber para quién receta).
            os_rows = (base.with_entities(
                            database.ObsVentaDetalle.obra_social_observer,
                            func.coalesce(func.sum(database.ObsVentaDetalle.cantidad_facturada), 0).label('uds'),
                       )
                       .group_by(database.ObsVentaDetalle.obra_social_observer)
                       .order_by(desc('uds'))
                       .limit(5).all())
            os_ids = [r[0] for r in os_rows if r[0]]
            os_map = {}
            if os_ids:
                from database import ObsObraSocial
                for o in (session.query(ObsObraSocial)
                          .filter(ObsObraSocial.observer_id.in_(os_ids)).all()):
                    os_map[o.observer_id] = o.descripcion or ''
            info['top_os'] = [{
                'observer_id': r[0],
                'nombre':      os_map.get(r[0], f'#{r[0]}' if r[0] else 'Sin OS'),
                'unidades':    float(r[1] or 0),
            } for r in os_rows]

            # Serie histórica mensual (últimos 12 meses).
            desde_serie = hasta - timedelta(days=365)
            ym = (func.extract('year', database.ObsVentaDetalle.fecha_estadistica) * 100
                  + func.extract('month', database.ObsVentaDetalle.fecha_estadistica))
            serie_rows = (session.query(
                              ym.label('ym'),
                              func.coalesce(func.sum(database.ObsVentaDetalle.cantidad_facturada), 0).label('uds'))
                          .filter(database.ObsVentaDetalle.medico_observer == medico_id,
                                  database.ObsVentaDetalle.fecha_estadistica >= desde_serie,
                                  database.ObsVentaDetalle.fecha_estadistica <= hasta,
                                  or_(database.ObsVentaDetalle.tipo_operacion == 'V',
                                      database.ObsVentaDetalle.tipo_operacion.is_(None)))
                          .group_by('ym')
                          .order_by('ym').all())
            info['serie'] = [{
                'anio': int(r[0] // 100),
                'mes':  int(r[0] % 100),
                'unidades': float(r[1] or 0),
            } for r in serie_rows]

        return render_template('consulta_medico_resultado.html', info=info)

    @app.route('/api/consulta-medico/buscar')
    def api_consulta_medico_buscar():
        """Wrapper de /api/informes/buscar-medico sin login_required para
        que el componente mobile pueda buscar sin sesión admin completa.
        Top 20 por nombre, case-insensitive, mínimo 2 chars.
        """
        q = (request.args.get('q') or '').strip()
        if len(q) < 2:
            return jsonify({'items': []})
        with database.get_db() as session:
            results = (session.query(database.ObsMedico)
                       .filter(database.ObsMedico.fecha_baja.is_(None),
                               database.ObsMedico.nombre.ilike(f'%{q}%'))
                       .order_by(database.ObsMedico.nombre)
                       .limit(20).all())
            return jsonify({'items': [{
                'id':       m.observer_id,
                'nombre':   m.nombre,
                'cuit':     m.cuit or '',
                'matricula': m.matricula_provincial or m.matricula_nacional or '',
            } for m in results]})
