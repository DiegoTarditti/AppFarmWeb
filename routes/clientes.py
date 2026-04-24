"""Listado, detalle y ABM local de clientes (espejo DW.Clientes + extensión local)."""

from datetime import datetime
from flask import render_template, request, redirect, url_for, flash
from flask_login import login_required
from sqlalchemy import or_, func as _f
import database


def init_app(app):

    @app.route('/clientes')
    @login_required
    def clientes_list():
        q = (request.args.get('q') or '').strip()
        grupo_id = request.args.get('grupo_id', type=int)
        localidad = (request.args.get('localidad') or '').strip()
        try:
            page = max(1, int(request.args.get('page', '1')))
        except ValueError:
            page = 1
        per_page = 50
        offset = (page - 1) * per_page

        with database.get_db() as session:
            base = session.query(database.ObsCliente)
            if q:
                like = f'%{q}%'
                terms = [database.ObsCliente.apellido_nombre.ilike(like),
                         database.ObsCliente.telefono.ilike(like),
                         database.ObsCliente.domicilio_direccion.ilike(like)]
                if q.isdigit():
                    terms.append(database.ObsCliente.documento_numero == int(q))
                base = base.filter(or_(*terms))
            if grupo_id:
                base = base.filter(database.ObsCliente.grupo_observer == grupo_id)
            if localidad:
                base = base.filter(database.ObsCliente.localidad.ilike(f'%{localidad}%'))

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

            # Lista de grupos para el filtro
            grupos = (session.query(database.ObsGrupoCliente)
                      .filter(database.ObsGrupoCliente.fecha_baja.is_(None))
                      .order_by(database.ObsGrupoCliente.descripcion).all())

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
                })

            last_page = max(1, (total + per_page - 1) // per_page)
            return render_template('clientes_list.html',
                                   clientes=clientes,
                                   total=total,
                                   grupos=[{'observer_id': g.observer_id,
                                            'descripcion': g.descripcion} for g in grupos],
                                   q=q, grupo_id=grupo_id, localidad=localidad,
                                   page=page, last_page=last_page)

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
