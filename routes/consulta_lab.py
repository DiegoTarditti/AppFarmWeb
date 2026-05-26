"""Consulta de estadísticas de un laboratorio (mobile).

Entrada con dropdown (partner_selector tipo='laboratorio'). El detalle
muestra KPIs agregados de productos del lab + top productos + top
médicos que los recetan + top OS + chart 12 meses.

Endpoints:
  GET /consulta-lab              → pantalla entrada (selector)
  POST /consulta-lab/buscar      → recibe lab_id del form, redirect
  GET /consulta-lab/<lab_id>     → pantalla resultado
"""
from datetime import date, timedelta

from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy import desc, func

import database
from helpers import ventas_periodo_filter


def init_app(app):

    @app.route('/consulta-lab')
    @login_required
    def consulta_lab():
        return render_template('consulta_lab.html')

    @app.route('/consulta-lab/buscar', methods=['POST'])
    @login_required
    def consulta_lab_buscar():
        lab_id = request.form.get('partner_id', type=int)
        if not lab_id:
            flash('Elegí un laboratorio.', 'error')
            return redirect(url_for('consulta_lab'))
        return redirect(url_for('consulta_lab_detalle', lab_id=lab_id))

    @app.route('/consulta-lab/<int:lab_id>')
    @login_required
    def consulta_lab_detalle(lab_id):
        try:
            n_days = max(7, min(365, int(request.args.get('dias', 90))))
        except (ValueError, TypeError):
            n_days = 90
        hasta = date.today()
        desde = hasta - timedelta(days=n_days)

        info = {'lab_id': lab_id, 'encontrado': False, 'n_days': n_days}
        with database.get_db() as session:
            lab = session.get(database.Laboratorio, lab_id)
            if not lab:
                return render_template('consulta_lab_resultado.html', info=info)

            info.update({
                'encontrado': True,
                'nombre': lab.nombre,
                'observer_id': lab.observer_id,
            })

            # Necesitamos observer_id del lab para filtrar ObsVentaDetalle via
            # ObsProducto. Si el bridge local→observer no está hecho, intentamos
            # resolver por nombre normalizado contra obs_laboratorios.
            obs_lab_id = lab.observer_id
            if obs_lab_id is None:
                from helpers import _normalizar_nombre_entidad
                norm_local = _normalizar_nombre_entidad(lab.nombre)
                # Fallback (lab sin bridge a Observer): recorre el catálogo de
                # labs normalizando en Python. Traemos SOLO las 2 columnas que
                # se usan y streameamos (sin .all()) para no hidratar el ORM
                # entero ni materializar la lista completa en RAM.
                for o in session.query(database.ObsLaboratorio.observer_id,
                                       database.ObsLaboratorio.descripcion):
                    if _normalizar_nombre_entidad(o.descripcion or '') == norm_local:
                        obs_lab_id = o.observer_id
                        break

            info['obs_lab_id'] = obs_lab_id
            if obs_lab_id is None:
                # Sin bridge a Observer, no podemos calcular KPIs/tops.
                info['kpis'] = {'unidades': 0, 'importe': 0, 'ops': 0, 'productos': 0}
                info['top_productos'] = []
                info['top_medicos'] = []
                info['top_os'] = []
                info['serie'] = []
                info['sin_bridge'] = True
                return render_template('consulta_lab_resultado.html', info=info)

            # Productos del lab en Observer (excluyendo no-medicamentos).
            from helpers import filtro_solo_medicamentos
            base = (session.query(database.ObsVentaDetalle)
                    .join(database.ObsProducto,
                          database.ObsProducto.observer_id == database.ObsVentaDetalle.producto_observer)
                    .filter(database.ObsProducto.laboratorio_observer == obs_lab_id,
                            ventas_periodo_filter(database.ObsVentaDetalle, desde, hasta)))
            base = filtro_solo_medicamentos(base, database.ObsProducto)

            kpi_row = base.with_entities(
                func.coalesce(func.sum(database.ObsVentaDetalle.cantidad), 0).label('uds'),
                func.coalesce(func.sum(database.ObsVentaDetalle.importe), 0).label('importe'),
                func.count(database.ObsVentaDetalle.id_operacion.distinct()).label('ops'),
                func.count(database.ObsVentaDetalle.producto_observer.distinct()).label('productos'),
            ).first()
            info['kpis'] = {
                'unidades':  float(kpi_row.uds or 0),
                'importe':   float(kpi_row.importe or 0),
                'ops':       int(kpi_row.ops or 0),
                'productos': int(kpi_row.productos or 0),
            }

            # Top productos del lab.
            top_p = (base.with_entities(
                         database.ObsVentaDetalle.producto_observer,
                         func.coalesce(func.sum(database.ObsVentaDetalle.cantidad), 0).label('uds'),
                         func.coalesce(func.sum(database.ObsVentaDetalle.importe), 0).label('imp'),
                     )
                     .group_by(database.ObsVentaDetalle.producto_observer)
                     .order_by(desc('uds')).limit(10).all())
            pids = [r[0] for r in top_p if r[0]]
            pmap = {}
            if pids:
                for p in (session.query(database.ObsProducto)
                          .filter(database.ObsProducto.observer_id.in_(pids)).all()):
                    pmap[p.observer_id] = p.descripcion or ''
            info['top_productos'] = [{
                'observer_id': r[0],
                'nombre':      pmap.get(r[0], f'#{r[0]}'),
                'unidades':    float(r[1] or 0),
                'importe':     float(r[2] or 0),
            } for r in top_p if r[0]]

            # Top médicos que recetan productos del lab.
            top_m = (base.with_entities(
                         database.ObsVentaDetalle.medico_observer,
                         func.coalesce(func.sum(database.ObsVentaDetalle.cantidad), 0).label('uds'),
                     )
                     .filter(database.ObsVentaDetalle.medico_observer.isnot(None))
                     .group_by(database.ObsVentaDetalle.medico_observer)
                     .order_by(desc('uds')).limit(10).all())
            mids = [r[0] for r in top_m if r[0]]
            mmap = {}
            if mids:
                for m in (session.query(database.ObsMedico)
                          .filter(database.ObsMedico.observer_id.in_(mids)).all()):
                    mmap[m.observer_id] = m.nombre or ''
            info['top_medicos'] = [{
                'observer_id': r[0],
                'nombre':      mmap.get(r[0], f'#{r[0]}'),
                'unidades':    float(r[1] or 0),
            } for r in top_m if r[0]]

            # Top OS.
            top_os = (base.with_entities(
                          database.ObsVentaDetalle.obra_social_observer,
                          func.coalesce(func.sum(database.ObsVentaDetalle.cantidad), 0).label('uds'),
                      )
                      .group_by(database.ObsVentaDetalle.obra_social_observer)
                      .order_by(desc('uds')).limit(5).all())
            osids = [r[0] for r in top_os if r[0]]
            osmap = {}
            if osids:
                from database import ObsObraSocial
                for o in (session.query(ObsObraSocial)
                          .filter(ObsObraSocial.observer_id.in_(osids)).all()):
                    osmap[o.observer_id] = o.descripcion or ''
            info['top_os'] = [{
                'observer_id': r[0],
                'nombre':      osmap.get(r[0], f'#{r[0]}' if r[0] else 'Sin OS'),
                'unidades':    float(r[1] or 0),
            } for r in top_os]

            # Chart 12 meses.
            desde_serie = hasta - timedelta(days=365)
            ym = (func.extract('year', database.ObsVentaDetalle.fecha_estadistica) * 100
                  + func.extract('month', database.ObsVentaDetalle.fecha_estadistica))
            serie_q = (session.query(ym.label('ym'),
                                       func.coalesce(func.sum(database.ObsVentaDetalle.cantidad), 0).label('uds'))
                          .join(database.ObsProducto,
                                database.ObsProducto.observer_id == database.ObsVentaDetalle.producto_observer)
                          .filter(database.ObsProducto.laboratorio_observer == obs_lab_id,
                                  ventas_periodo_filter(database.ObsVentaDetalle, desde_serie, hasta)))
            serie_rows = filtro_solo_medicamentos(serie_q, database.ObsProducto)\
                          .group_by('ym').order_by('ym').all()
            info['serie'] = [{
                'anio': int(r[0] // 100), 'mes': int(r[0] % 100),
                'unidades': float(r[1] or 0),
            } for r in serie_rows]

        return render_template('consulta_lab_resultado.html', info=info)
