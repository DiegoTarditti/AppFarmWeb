"""Listado, detalle y ABM local de clientes (espejo DW.Clientes + extensión local)."""

from datetime import datetime

from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy import func as _f
from sqlalchemy import or_

import database


def init_app(app):

    @app.route('/clientes')
    @login_required
    def clientes_list():
        q = (request.args.get('q') or '').strip()
        grupo_id = request.args.get('grupo_id', type=int)
        localidad = (request.args.get('localidad') or '').strip()
        os_id = request.args.get('os_id', type=int)
        try:
            page = max(1, int(request.args.get('page', '1')))
        except ValueError:
            page = 1
        per_page = 50
        offset = (page - 1) * per_page

        with database.get_db() as session:
            base = session.query(database.ObsCliente)
            if q:
                from helpers import multi_token_filter
                # Match exacto por DNI si el query es solo numérico (atajo).
                if q.isdigit():
                    base = base.filter(or_(
                        database.ObsCliente.documento_numero == int(q),
                        database.ObsCliente.telefono.ilike(f'%{q}%'),
                    ))
                else:
                    clausula = multi_token_filter(q,
                        database.ObsCliente.apellido_nombre,
                        database.ObsCliente.telefono,
                        database.ObsCliente.domicilio_direccion)
                    if clausula is not None:
                        base = base.filter(clausula)
            if grupo_id:
                base = base.filter(database.ObsCliente.grupo_observer == grupo_id)
            if localidad:
                base = base.filter(database.ObsCliente.localidad.ilike(f'%{localidad}%'))
            # Filtro por OS principal inferida (cliente_os_inferida)
            if os_id is not None:
                if os_id == 0:
                    # 0 = "sin OS principal" (ningún match en cliente_os_inferida con OS)
                    sub = (session.query(database.ClienteOsInferida.cliente_observer)
                           .filter(database.ClienteOsInferida.obra_social_observer.isnot(None))
                           .subquery())
                    base = base.filter(~database.ObsCliente.observer_id.in_(sub))
                else:
                    sub = (session.query(database.ClienteOsInferida.cliente_observer)
                           .filter(database.ClienteOsInferida.obra_social_observer == os_id)
                           .subquery())
                    base = base.filter(database.ObsCliente.observer_id.in_(sub))

            total = base.count()
            clientes_raw = (base.order_by(database.ObsCliente.apellido_nombre)
                            .offset(offset).limit(per_page).all())

            obs_ids = [c.observer_id for c in clientes_raw]

            # Nombres de grupos
            grupos_map = dict(
                session.query(database.ObsGrupoCliente.observer_id,
                              database.ObsGrupoCliente.descripcion).all()
            )

            # Detectar cuáles tienen extensión local (Cliente) ya cargada
            con_extension = set()
            if obs_ids:
                con_extension = {oid for (oid,) in
                                 session.query(database.Cliente.observer_id)
                                 .filter(database.Cliente.observer_id.in_(obs_ids)).all()}

            # OS principal inferida por cliente (con confianza)
            os_inferida_map = {}
            if obs_ids:
                rows = (session.query(database.ClienteOsInferida.cliente_observer,
                                      database.ClienteOsInferida.obra_social_observer,
                                      database.ClienteOsInferida.confianza_pct)
                        .filter(database.ClienteOsInferida.cliente_observer.in_(obs_ids))
                        .filter(database.ClienteOsInferida.obra_social_observer.isnot(None))
                        .all())
                os_ids_para_nombrar = {r[1] for r in rows}
                os_nombres = dict(session.query(database.ObsObraSocial.observer_id,
                                                database.ObsObraSocial.descripcion)
                                  .filter(database.ObsObraSocial.observer_id.in_(os_ids_para_nombrar)).all()) if os_ids_para_nombrar else {}
                for cli, os_obs, conf in rows:
                    os_inferida_map[cli] = {
                        'os_id': os_obs,
                        'nombre': os_nombres.get(os_obs, f'OS#{os_obs}'),
                        'confianza': float(conf) if conf is not None else None,
                    }

            # Lista de grupos para el filtro
            grupos = (session.query(database.ObsGrupoCliente)
                      .filter(database.ObsGrupoCliente.fecha_baja.is_(None))
                      .order_by(database.ObsGrupoCliente.descripcion).all())

            # Lista de OS con clientes inferidos (para dropdown del filtro)
            from sqlalchemy import func as _func
            os_con_clientes = (session.query(database.ObsObraSocial.observer_id,
                                              database.ObsObraSocial.descripcion,
                                              _func.count(database.ClienteOsInferida.cliente_observer))
                                .join(database.ClienteOsInferida,
                                      database.ClienteOsInferida.obra_social_observer == database.ObsObraSocial.observer_id)
                                .filter(database.ObsObraSocial.fecha_baja.is_(None))
                                .group_by(database.ObsObraSocial.observer_id, database.ObsObraSocial.descripcion)
                                .order_by(_func.count(database.ClienteOsInferida.cliente_observer).desc())
                                .all())

            clientes = []
            for c in clientes_raw:
                clientes.append({
                    'observer_id': c.observer_id,
                    'apellido_nombre': c.apellido_nombre,
                    'documento': (f'{c.documento_tipo} {c.documento_numero}'
                                   if c.documento_numero else ''),
                    'telefono': c.telefono or '',
                    'localidad': c.localidad or '',
                    'direccion': c.domicilio_direccion or '',
                    'grupo': grupos_map.get(c.grupo_observer, ''),
                    'tiene_extension': c.observer_id in con_extension,
                    'os_inferida': os_inferida_map.get(c.observer_id),
                })

            last_page = max(1, (total + per_page - 1) // per_page)
            return render_template('clientes_list.html',
                                   clientes=clientes,
                                   total=total,
                                   grupos=[{'observer_id': g.observer_id,
                                            'descripcion': g.descripcion} for g in grupos],
                                   obras_sociales=[{'os_id': r[0], 'nombre': r[1], 'n_clientes': r[2]}
                                                    for r in os_con_clientes],
                                   q=q, grupo_id=grupo_id, localidad=localidad, os_id=os_id,
                                   page=page, last_page=last_page)

    @app.route('/clientes/stats')
    @login_required
    def clientes_stats():
        """Dashboard demográfico de clientes: distribuciones por grupo, categoría,
        localidad, provincia + conteo de extensiones locales cargadas."""
        with database.get_db() as session:
            total = session.query(database.ObsCliente).count()

            grupos_map = dict(session.query(database.ObsGrupoCliente.observer_id,
                                            database.ObsGrupoCliente.descripcion).all())
            cats_map = dict(session.query(database.ObsCategoriaCliente.observer_id,
                                          database.ObsCategoriaCliente.descripcion).all())

            por_grupo = (session.query(database.ObsCliente.grupo_observer,
                                       _f.count(database.ObsCliente.observer_id))
                         .group_by(database.ObsCliente.grupo_observer)
                         .order_by(_f.count(database.ObsCliente.observer_id).desc()).all())
            por_grupo = [{'label': grupos_map.get(g) or '(sin grupo)', 'count': int(n)}
                         for g, n in por_grupo]

            por_cat = (session.query(database.ObsCliente.categoria_observer,
                                     _f.count(database.ObsCliente.observer_id))
                       .group_by(database.ObsCliente.categoria_observer)
                       .order_by(_f.count(database.ObsCliente.observer_id).desc()).all())
            por_cat = [{'label': cats_map.get(c) or '(sin categoría)', 'count': int(n)}
                       for c, n in por_cat]

            por_loc = (session.query(database.ObsCliente.localidad,
                                     _f.count(database.ObsCliente.observer_id))
                       .filter(database.ObsCliente.localidad.isnot(None),
                               database.ObsCliente.localidad != '')
                       .group_by(database.ObsCliente.localidad)
                       .order_by(_f.count(database.ObsCliente.observer_id).desc())
                       .limit(15).all())
            por_loc = [{'label': l or '—', 'count': int(n)} for l, n in por_loc]

            por_prov = (session.query(database.ObsCliente.provincia,
                                      _f.count(database.ObsCliente.observer_id))
                        .filter(database.ObsCliente.provincia.isnot(None),
                                database.ObsCliente.provincia != '')
                        .group_by(database.ObsCliente.provincia)
                        .order_by(_f.count(database.ObsCliente.observer_id).desc()).all())
            por_prov = [{'label': p or '—', 'count': int(n)} for p, n in por_prov]

            # Extensión local
            n_ext = session.query(database.Cliente).count()
            n_wa = session.query(database.Cliente).filter(database.Cliente.whatsapp.isnot(None),
                                                          database.Cliente.whatsapp != '').count()
            n_mail = session.query(database.Cliente).filter(database.Cliente.email.isnot(None),
                                                            database.Cliente.email != '').count()
            n_notas = session.query(database.Cliente).filter(database.Cliente.notas.isnot(None),
                                                             database.Cliente.notas != '').count()
            n_tags = session.query(database.Cliente).filter(database.Cliente.tags.isnot(None),
                                                            database.Cliente.tags != '').count()

        return render_template('clientes_stats.html',
                               total=total,
                               por_grupo=por_grupo, por_cat=por_cat,
                               por_loc=por_loc, por_prov=por_prov,
                               n_ext=n_ext, n_wa=n_wa, n_mail=n_mail,
                               n_notas=n_notas, n_tags=n_tags)

    @app.route('/clientes/<int:observer_id>')
    @login_required
    def cliente_detail(observer_id):
        with database.get_db() as session:
            obs = session.get(database.ObsCliente, observer_id)
            if not obs:
                flash('Cliente no encontrado.', 'error')
                return redirect(url_for('clientes_list'))

            grupo = (session.get(database.ObsGrupoCliente, obs.grupo_observer)
                     if obs.grupo_observer else None)
            categoria = (session.get(database.ObsCategoriaCliente, obs.categoria_observer)
                         if obs.categoria_observer else None)
            ext = session.query(database.Cliente).filter_by(observer_id=observer_id).first()

            return render_template('cliente_detail.html',
                                   obs=obs,
                                   grupo=grupo.descripcion if grupo else None,
                                   categoria=categoria.descripcion if categoria else None,
                                   ext=ext)

    @app.route('/clientes/<int:observer_id>/edit', methods=['POST'])
    @login_required
    def cliente_edit(observer_id):
        """ABM de la extensión local (Cliente). Crea o actualiza."""
        with database.get_db() as session:
            obs = session.get(database.ObsCliente, observer_id)
            if not obs:
                flash('Cliente no encontrado.', 'error')
                return redirect(url_for('clientes_list'))

            ext = session.query(database.Cliente).filter_by(observer_id=observer_id).first()
            if not ext:
                ext = database.Cliente(observer_id=observer_id)
                session.add(ext)

            ext.notas = (request.form.get('notas') or '').strip() or None
            ext.tags = (request.form.get('tags') or '').strip() or None
            ext.whatsapp = (request.form.get('whatsapp') or '').strip() or None
            ext.email = (request.form.get('email') or '').strip() or None
            fn = (request.form.get('fecha_nacimiento') or '').strip()
            if fn:
                try:
                    ext.fecha_nacimiento = datetime.strptime(fn, '%Y-%m-%d').date()
                except ValueError:
                    flash('Fecha de nacimiento inválida.', 'warning')
                    ext.fecha_nacimiento = None
            else:
                ext.fecha_nacimiento = None

            session.commit()
            flash('Datos locales actualizados.', 'success')
            return redirect(url_for('cliente_detail', observer_id=observer_id))

    @app.route('/clientes/<int:observer_id>/borrar-extension', methods=['POST'])
    @login_required
    def cliente_borrar_extension(observer_id):
        """Borra la extensión local (sin tocar el cliente del ObServer)."""
        with database.get_db() as session:
            ext = session.query(database.Cliente).filter_by(observer_id=observer_id).first()
            if ext:
                session.delete(ext)
                session.commit()
                flash('Datos locales eliminados.', 'success')
        return redirect(url_for('cliente_detail', observer_id=observer_id))
