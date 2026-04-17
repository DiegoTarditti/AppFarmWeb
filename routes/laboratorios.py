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
            data = []
            for l in labs:
                prod_count = session.query(_func.count(Producto.id)).filter_by(laboratorio_id=l.id).scalar() or 0
                ped_count = session.query(_func.count(database.Pedido.id)).filter_by(laboratorio=l.nombre).scalar() or 0
                analytics_count = session.query(_func.count(database.ProductAnalytics.codigo_barra))\
                    .filter_by(laboratorio=l.nombre).scalar() or 0
                data.append({
                    'id': l.id, 'nombre': l.nombre,
                    'prod_count': prod_count,
                    'ped_count': ped_count, 'analytics_count': analytics_count,
                })
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
