"""Catálogo (listado + detalle) de obras sociales + convenios + planes.
Espejo de DW.ObrasSociales / DW.Convenios / DW.Planes. Solo lectura."""

from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy import func as _f

import database


def init_app(app):

    @app.route('/obras-sociales/catalogo')
    @login_required
    def obras_sociales_catalogo():
        q = (request.args.get('q') or '').strip()
        try:
            page = max(1, int(request.args.get('page', '1')))
        except ValueError:
            page = 1
        per_page = 50
        offset = (page - 1) * per_page

        with database.get_db() as session:
            base = (session.query(database.ObsObraSocial)
                    .filter(database.ObsObraSocial.fecha_baja.is_(None)))
            if q:
                base = base.filter(database.ObsObraSocial.descripcion.ilike(f'%{q}%'))
            total = base.count()
            oss = (base.order_by(database.ObsObraSocial.descripcion)
                   .offset(offset).limit(per_page).all())

            # Conteo convenios por OS (batch)
            os_ids = [o.observer_id for o in oss]
            conv_map = {}
            if os_ids:
                rows = (session.query(database.ObsConvenio.obra_social_observer,
                                       _f.count(database.ObsConvenio.observer_id))
                        .filter(database.ObsConvenio.obra_social_observer.in_(os_ids),
                                database.ObsConvenio.fecha_baja.is_(None))
                        .group_by(database.ObsConvenio.obra_social_observer).all())
                conv_map = {os_id: int(n) for os_id, n in rows}

            # Conteo planes por OS (via convenio)
            planes_map = {}
            if os_ids:
                conv_os = dict(session.query(database.ObsConvenio.observer_id,
                                              database.ObsConvenio.obra_social_observer)
                               .filter(database.ObsConvenio.obra_social_observer.in_(os_ids)).all())
                conv_ids = list(conv_os.keys())
                if conv_ids:
                    rows = (session.query(database.ObsPlan.convenio_observer,
                                           _f.count(database.ObsPlan.observer_id))
                            .filter(database.ObsPlan.convenio_observer.in_(conv_ids),
                                    database.ObsPlan.fecha_baja.is_(None))
                            .group_by(database.ObsPlan.convenio_observer).all())
                    for conv_id, n in rows:
                        os_id = conv_os.get(conv_id)
                        if os_id:
                            planes_map[os_id] = planes_map.get(os_id, 0) + int(n)

            data = [{
                'observer_id': o.observer_id,
                'descripcion': o.descripcion,
                'n_convenios': conv_map.get(o.observer_id, 0),
                'n_planes': planes_map.get(o.observer_id, 0),
            } for o in oss]

            last_page = max(1, (total + per_page - 1) // per_page)
            return render_template('obras_sociales_catalogo.html',
                                   obras=data, total=total, q=q,
                                   page=page, last_page=last_page)

    @app.route('/obras-sociales/catalogo/<int:observer_id>')
    @login_required
    def obra_social_catalogo_detail(observer_id):
        with database.get_db() as session:
            os_obj = session.get(database.ObsObraSocial, observer_id)
            if not os_obj:
                flash('Obra social no encontrada.', 'error')
                return redirect(url_for('obras_sociales_catalogo'))

            convenios_raw = (session.query(database.ObsConvenio)
                             .filter(database.ObsConvenio.obra_social_observer == observer_id,
                                     database.ObsConvenio.fecha_baja.is_(None))
                             .order_by(database.ObsConvenio.descripcion).all())

            conv_ids = [c.observer_id for c in convenios_raw]
            planes_por_conv = {}
            if conv_ids:
                for pl in (session.query(database.ObsPlan)
                           .filter(database.ObsPlan.convenio_observer.in_(conv_ids),
                                   database.ObsPlan.fecha_baja.is_(None))
                           .order_by(database.ObsPlan.descripcion).all()):
                    planes_por_conv.setdefault(pl.convenio_observer, []).append({
                        'observer_id': pl.observer_id,
                        'descripcion': pl.descripcion,
                        'habilitado': pl.habilitado,
                    })

            convenios = [{
                'observer_id': c.observer_id,
                'descripcion': c.descripcion or '(sin descripción)',
                'planes': planes_por_conv.get(c.observer_id, []),
            } for c in convenios_raw]

            return render_template('obra_social_catalogo_detail.html',
                                   obra=os_obj, convenios=convenios)
