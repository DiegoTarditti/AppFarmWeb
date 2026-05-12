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
from flask_login import login_required
from sqlalchemy import desc, func

import database
from helpers import medicos_observer_ids_compartidos


def init_app(app):

    @app.route('/consulta-medico')
    @login_required
    def consulta_medico():
        return render_template('consulta_medico.html')

    @app.route('/consulta-medico/<int:medico_id>')
    @login_required
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

            # Matrículas vienen de obs_medicos_matriculas (1-a-N).
            matriculas = (session.query(database.ObsMedicoMatricula.matricula)
                          .filter(database.ObsMedicoMatricula.medico_observer == medico_id,
                                  database.ObsMedicoMatricula.fecha_baja.is_(None))
                          .all())
            matriculas_str = ', '.join([m[0] for m in matriculas if m[0]])

            # Consolidar IDs por matrícula: el POS de Observer duplica al
            # médico una vez por cada lab que promociona productos suyos
            # ("BERNABO PALADINO", "BONO BERNABO PALADINO", etc.). Todos los
            # observer_id que comparten matrícula son la MISMA persona.
            medico_ids = medicos_observer_ids_compartidos(session, medico_id)

            info.update({
                'encontrado': True,
                'nombre': medico.nombre or '',
                'cuit': medico.cuit or '',
                'matriculas': matriculas_str,
                'baja': bool(medico.fecha_baja),
                'medico_ids_agrupados': medico_ids,
            })

            # KPIs en el rango. Filtros importantes:
            # - medico_observer IN (todos los IDs que comparten matrícula).
            # - SIN filtro por tipo_operacion: incluimos V (ventas), D
            #   (devoluciones), NC (notas de crédito). El signo de `cantidad`
            #   ya lleva el descuento (devoluciones vienen con cantidad < 0),
            #   asi el sum() neto refleja ventas reales menos devueltas.
            base = (session.query(database.ObsVentaDetalle)
                    .filter(database.ObsVentaDetalle.medico_observer.in_(medico_ids),
                            database.ObsVentaDetalle.fecha_estadistica >= desde,
                            database.ObsVentaDetalle.fecha_estadistica <= hasta))

            kpi_row = base.with_entities(
                func.coalesce(func.sum(database.ObsVentaDetalle.cantidad), 0).label('uds'),
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
                            func.coalesce(func.sum(database.ObsVentaDetalle.cantidad), 0).label('uds'),
                            func.coalesce(func.sum(database.ObsVentaDetalle.importe), 0).label('imp'),
                        )
                        .group_by(database.ObsVentaDetalle.producto_observer)
                        .order_by(desc('uds'))
                        .limit(10).all())
            prod_ids = [r[0] for r in top_rows if r[0]]
            prod_map = {}
            prod_cb = {}
            if prod_ids:
                for op in (session.query(database.ObsProducto)
                           .filter(database.ObsProducto.observer_id.in_(prod_ids)).all()):
                    prod_map[op.observer_id] = op.descripcion or ''
                # Resolver codigo_barra: primero via productos local (bridge),
                # fallback a obs_codigos_barras (orden=1).
                for p in (session.query(database.Producto)
                          .filter(database.Producto.observer_id.in_(prod_ids)).all()):
                    if p.codigo_barra:
                        prod_cb[p.observer_id] = p.codigo_barra
                sin_cb = [pid for pid in prod_ids if pid not in prod_cb]
                if sin_cb:
                    for row in (session.query(database.ObsCodigoBarras.producto_observer,
                                              database.ObsCodigoBarras.codigo_barras)
                                .filter(database.ObsCodigoBarras.producto_observer.in_(sin_cb),
                                        database.ObsCodigoBarras.fecha_baja.is_(None),
                                        database.ObsCodigoBarras.orden == 1).all()):
                        if row[1]:
                            prod_cb[row[0]] = row[1].strip()
            info['top_productos'] = [{
                'observer_id': r[0],
                'codigo_barra': prod_cb.get(r[0]),
                'nombre':      prod_map.get(r[0], f'#{r[0]}'),
                'unidades':    float(r[1] or 0),
                'importe':     float(r[2] or 0),
            } for r in top_rows if r[0]]

            # Top OS atendidas (saber para quién receta).
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
                              func.coalesce(func.sum(database.ObsVentaDetalle.cantidad), 0).label('uds'))
                          .filter(database.ObsVentaDetalle.medico_observer.in_(medico_ids),
                                  database.ObsVentaDetalle.fecha_estadistica >= desde_serie,
                                  database.ObsVentaDetalle.fecha_estadistica <= hasta)
                          .group_by('ym')
                          .order_by('ym').all())
            info['serie'] = [{
                'anio': int(r[0] // 100),
                'mes':  int(r[0] % 100),
                'unidades': float(r[1] or 0),
            } for r in serie_rows]

        return render_template('consulta_medico_resultado.html', info=info)

    @app.route('/api/consulta-medico/buscar')
    @login_required
    def api_consulta_medico_buscar():
        """Búsqueda tokenizada por nombre de médico. Mínimo 2 chars totales.

        Cada token (separado por espacio) debe matchear el nombre con ILIKE
        (case-insensitive, sustring). AND entre tokens — orden y posición
        libres. Top 20 alfabético.
        """
        q = (request.args.get('q') or '').strip()
        if len(q) < 2:
            return jsonify({'items': []})
        tokens = [t for t in q.split() if t]
        with database.get_db() as session:
            base = (session.query(database.ObsMedico)
                    .filter(database.ObsMedico.fecha_baja.is_(None)))
            for t in tokens:
                base = base.filter(database.ObsMedico.nombre.ilike(f'%{t}%'))
            results = base.order_by(database.ObsMedico.nombre).limit(20).all()
            # Las matrículas viven en ObsMedicoMatricula (1-a-N). Las traemos
            # en una sola query para mostrar la primera disponible.
            ids = [m.observer_id for m in results]
            mat_map = {}
            if ids:
                for row in (session.query(database.ObsMedicoMatricula.medico_observer,
                                          database.ObsMedicoMatricula.matricula)
                            .filter(database.ObsMedicoMatricula.medico_observer.in_(ids),
                                    database.ObsMedicoMatricula.fecha_baja.is_(None))
                            .all()):
                    mat_map.setdefault(row[0], row[1])
            return jsonify({'items': [{
                'id':       m.observer_id,
                'nombre':   m.nombre,
                'cuit':     m.cuit or '',
                'matricula': mat_map.get(m.observer_id, '') or '',
            } for m in results]})
