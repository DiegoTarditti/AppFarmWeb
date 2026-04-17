"""Laboratorio CRUD routes."""

from flask import request, redirect, url_for, flash, render_template
import database
from database import Laboratorio, Producto


def init_app(app):

    @app.route('/laboratorios')
    def laboratorios_list():
        from sqlalchemy import func as _func
        with database.get_db() as session:
            labs = session.query(Laboratorio).order_by(Laboratorio.nombre).all()
            lab_ids   = [l.id     for l in labs]
            lab_names = [l.nombre for l in labs]

            prod_map = dict(
                session.query(Producto.laboratorio_id, _func.count(Producto.id))
                .filter(Producto.laboratorio_id.in_(lab_ids))
                .group_by(Producto.laboratorio_id).all()
            ) if lab_ids else {}

            ped_map = dict(
                session.query(database.Pedido.laboratorio, _func.count(database.Pedido.id))
                .filter(database.Pedido.laboratorio.in_(lab_names))
                .group_by(database.Pedido.laboratorio).all()
            ) if lab_names else {}

            analytics_map = dict(
                session.query(database.ProductAnalytics.laboratorio,
                              _func.count(database.ProductAnalytics.codigo_barra))
                .filter(database.ProductAnalytics.laboratorio.in_(lab_names))
                .group_by(database.ProductAnalytics.laboratorio).all()
            ) if lab_names else {}

            data = [{
                'id': l.id, 'nombre': l.nombre,
                'prod_count':      prod_map.get(l.id, 0),
                'ped_count':       ped_map.get(l.nombre, 0),
                'analytics_count': analytics_map.get(l.nombre, 0),
            } for l in labs]
        return render_template('laboratorios.html', laboratorios=data)

    @app.route('/laboratorio/create', methods=['POST'])
    def laboratorio_create():
        nombre = request.form.get('nombre', '').strip()
        if not nombre:
            flash('El nombre es obligatorio.')
            return redirect(url_for('laboratorios_list'))
        with database.get_db() as session:
            existing = session.query(Laboratorio).filter(Laboratorio.nombre.ilike(nombre)).first()
            if existing:
                flash(f'Ya existe un laboratorio con ese nombre.')
            else:
                session.add(Laboratorio(nombre=nombre))
                session.commit()
        return redirect(url_for('laboratorios_list'))

    @app.route('/laboratorio/<int:lab_id>/edit', methods=['POST'])
    def laboratorio_edit(lab_id):
        nombre = request.form.get('nombre', '').strip()
        if not nombre:
            flash('El nombre es obligatorio.')
            return redirect(url_for('laboratorios_list'))
        with database.get_db() as session:
            lab = session.get(Laboratorio, lab_id)
            if lab:
                lab.nombre = nombre
                session.commit()
        return redirect(url_for('laboratorios_list'))

    @app.route('/laboratorio/<int:lab_id>/delete', methods=['POST'])
    def laboratorio_delete(lab_id):
        with database.get_db() as session:
            lab = session.get(Laboratorio, lab_id)
            if lab:
                session.query(Producto).filter_by(laboratorio_id=lab_id).update({'laboratorio_id': None})
                session.delete(lab)
                session.commit()
        return redirect(url_for('laboratorios_list'))
