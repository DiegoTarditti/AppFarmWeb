"""Consulta de estadísticas de una OS (mobile-first).

Patrón consulta-medico/lab. Filtros core: OS + rango. Group_by:
producto / medico / mes / droga. Aplica:
- ventas_periodo_filter (suma neta — descuenta devoluciones)
- excluir_no_medicamentos_ovd (sin sellado/cupón)

Endpoints:
  GET  /consulta-os                 → pantalla entrada (autocomplete OS)
  GET  /consulta-os/<int:os_id>     → resultado con KPIs + tabs
  GET  /api/consulta-os/buscar      → autocomplete OS (multi-token AND)
"""
from datetime import date, timedelta

from flask import jsonify, render_template, request
from flask_login import login_required
from sqlalchemy import desc, func

import database
from helpers import excluir_no_medicamentos_ovd, ventas_periodo_filter


def init_app(app):

    @app.route('/consulta-os')
    @login_required
    def consulta_os():
        return render_template('consulta_os.html')

    @app.route('/consulta-os/<int:os_id>')
    @login_required
    def consulta_os_detalle(os_id):
        try:
            n_days = max(7, min(365, int(request.args.get('dias', 90))))
        except (ValueError, TypeError):
            n_days = 90
        group_by = (request.args.get('gb') or 'producto').strip()
        if group_by not in ('producto', 'medico', 'mes', 'droga'):
            group_by = 'producto'
        hasta = date.today()
        desde = hasta - timedelta(days=n_days)

        info = {'os_id': os_id, 'encontrado': False, 'n_days': n_days,
                'group_by': group_by, 'rows': []}
        with database.get_db() as session:
            os_obj = session.get(database.ObsObraSocial, os_id)
            if not os_obj:
                return render_template('consulta_os_resultado.html', info=info)

            info.update({
                'encontrado': True,
                'nombre': os_obj.descripcion or '',
                'baja': bool(os_obj.fecha_baja),
            })

            # Base: ventas a esta OS, neto, sin sellado/cupón.
            base = (session.query(database.ObsVentaDetalle)
                    .filter(database.ObsVentaDetalle.obra_social_observer == os_id,
                            ventas_periodo_filter(database.ObsVentaDetalle, desde, hasta),
                            excluir_no_medicamentos_ovd(database.ObsVentaDetalle,
                                                        database.ObsProducto, session)))

            # KPIs (importe + a cargo OS específico — distintivo vs consulta-medico).
            kpi_row = base.with_entities(
                func.coalesce(func.sum(database.ObsVentaDetalle.cantidad), 0).label('uds'),
                func.coalesce(func.sum(database.ObsVentaDetalle.importe), 0).label('importe'),
                func.coalesce(func.sum(database.ObsVentaDetalle.importe_a_cargo_os), 0).label('a_cargo_os'),
                func.count(database.ObsVentaDetalle.id_operacion.distinct()).label('ops'),
                func.count(database.ObsVentaDetalle.cliente_observer.distinct()).label('clientes'),
                func.count(database.ObsVentaDetalle.medico_observer.distinct()).label('medicos'),
            ).first()
            importe_total = float(kpi_row.importe or 0)
            a_cargo_os = float(kpi_row.a_cargo_os or 0)
            info['kpis'] = {
                'unidades':       float(kpi_row.uds or 0),
                'importe':        importe_total,
                'a_cargo_os':     a_cargo_os,
                'pct_a_cargo_os': (100 * a_cargo_os / importe_total) if importe_total > 0 else 0,
                'ops':            int(kpi_row.ops or 0),
                'clientes':       int(kpi_row.clientes or 0),
                'medicos':        int(kpi_row.medicos or 0),
            }

            # Pivot
            if group_by == 'producto':
                rows = _agrupar(base, database.ObsVentaDetalle.producto_observer)
                ids = [r['key'] for r in rows if r['key']]
                desc_map = {}
                if ids:
                    for op in (session.query(database.ObsProducto)
                               .filter(database.ObsProducto.observer_id.in_(ids)).all()):
                        desc_map[op.observer_id] = op.descripcion or f'#{op.observer_id}'
                for r in rows:
                    r['label'] = desc_map.get(r['key'], f'#{r["key"]}')

            elif group_by == 'medico':
                rows = _agrupar(base, database.ObsVentaDetalle.medico_observer)
                ids = [r['key'] for r in rows if r['key']]
                med_map = {}
                if ids:
                    for m in (session.query(database.ObsMedico)
                              .filter(database.ObsMedico.observer_id.in_(ids)).all()):
                        med_map[m.observer_id] = m.nombre or f'#{m.observer_id}'
                for r in rows:
                    r['label'] = med_map.get(r['key'],
                                              '— sin médico —' if not r['key'] else f'#{r["key"]}')

            elif group_by == 'mes':
                anio = func.extract('year', database.ObsVentaDetalle.fecha_estadistica)
                mes = func.extract('month', database.ObsVentaDetalle.fecha_estadistica)
                q = (base.with_entities(
                        anio.label('a'), mes.label('m'),
                        func.coalesce(func.sum(database.ObsVentaDetalle.cantidad), 0).label('uds'),
                        func.coalesce(func.sum(database.ObsVentaDetalle.importe), 0).label('imp'),
                        func.count(database.ObsVentaDetalle.id_operacion.distinct()).label('ops'),
                     ).group_by(anio, mes).order_by(anio, mes))
                _MES = ['', 'Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul',
                        'Ago', 'Sep', 'Oct', 'Nov', 'Dic']
                rows = [{
                    'key':      f'{int(r.a)}-{int(r.m):02d}',
                    'label':    f'{_MES[int(r.m)]} {int(r.a)}',
                    'unidades': float(r.uds or 0),
                    'importe':  float(r.imp or 0),
                    'ops':      int(r.ops or 0),
                } for r in q.all()]

            else:  # droga
                base_p = base.join(database.ObsProducto,
                                   database.ObsProducto.observer_id == database.ObsVentaDetalle.producto_observer)
                q = (base_p.with_entities(
                        database.ObsProducto.nombre_droga_observer.label('key'),
                        func.coalesce(func.sum(database.ObsVentaDetalle.cantidad), 0).label('uds'),
                        func.coalesce(func.sum(database.ObsVentaDetalle.importe), 0).label('imp'),
                        func.count(database.ObsVentaDetalle.id_operacion.distinct()).label('ops'),
                     ).group_by(database.ObsProducto.nombre_droga_observer)
                     .order_by(desc('uds')).limit(100))
                base_rows = q.all()
                drog_ids = [r.key for r in base_rows if r.key]
                drog_map = {}
                if drog_ids:
                    for d in (session.query(database.ObsNombreDroga)
                              .filter(database.ObsNombreDroga.observer_id.in_(drog_ids)).all()):
                        drog_map[d.observer_id] = d.descripcion or f'#{d.observer_id}'
                rows = [{
                    'key':      r.key,
                    'label':    drog_map.get(r.key, '— sin droga —' if not r.key else f'#{r.key}'),
                    'unidades': float(r.uds or 0),
                    'importe':  float(r.imp or 0),
                    'ops':      int(r.ops or 0),
                } for r in base_rows]

            info['rows'] = rows
            info['total_unidades'] = sum(r['unidades'] for r in rows)
            info['total_importe'] = sum(r['importe'] for r in rows)

        return render_template('consulta_os_resultado.html', info=info)

    @app.route('/api/consulta-os/buscar')
    @login_required
    def api_consulta_os_buscar():
        """Búsqueda tokenizada de OS. Multi-token AND. Top 20."""
        q = (request.args.get('q') or '').strip()
        if len(q) < 2:
            return jsonify({'items': []})
        tokens = [t for t in q.split() if t]
        with database.get_db() as session:
            base = (session.query(database.ObsObraSocial)
                    .filter(database.ObsObraSocial.fecha_baja.is_(None)))
            for t in tokens:
                base = base.filter(database.ObsObraSocial.descripcion.ilike(f'%{t}%'))
            results = base.order_by(database.ObsObraSocial.descripcion).limit(20).all()
            return jsonify({'items': [{'id': o.observer_id, 'nombre': o.descripcion}
                                       for o in results]})


def _agrupar(base_q, col):
    """Helper interno: agrupa base_q por col, devuelve rows con key + KPIs."""
    from sqlalchemy import desc as _desc
    from sqlalchemy import func as _func

    import database as _db
    q = (base_q.with_entities(
            col.label('key'),
            _func.coalesce(_func.sum(_db.ObsVentaDetalle.cantidad), 0).label('uds'),
            _func.coalesce(_func.sum(_db.ObsVentaDetalle.importe), 0).label('imp'),
            _func.count(_db.ObsVentaDetalle.id_operacion.distinct()).label('ops'),
         ).group_by(col).order_by(_desc('uds')).limit(100))
    return [{'key': r.key,
             'unidades': float(r.uds or 0),
             'importe':  float(r.imp or 0),
             'ops':      int(r.ops or 0)} for r in q.all()]
