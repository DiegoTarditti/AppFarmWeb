"""Laboratorio CRUD routes."""

import os
import tempfile
from flask import request, redirect, url_for, flash, render_template, jsonify
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

    @app.route('/api/ofertas/preview', methods=['POST'])
    def api_ofertas_preview():
        """Preview de ofertas simples (solo EAN + descripción)."""
        from parsers.ofertas_xlsx import parse_ofertas_xlsx
        f = request.files.get('archivo')
        if not f or not f.filename:
            return jsonify({'error': 'No se recibió archivo'}), 400
        ext = f.filename.rsplit('.', 1)[-1].lower()
        if ext not in ('xlsx', 'xls'):
            return jsonify({'error': 'Solo se aceptan .xlsx / .xls'}), 400
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f'.{ext}')
        f.save(tmp.name); tmp.close()
        try:
            items = parse_ofertas_xlsx(tmp.name)
            return jsonify({'items': items})
        except Exception as e:
            return jsonify({'error': str(e)}), 400
        finally:
            try: os.unlink(tmp.name)
            except OSError: pass

    @app.route('/api/ofertas/preview-con-minimo', methods=['POST'])
    def api_ofertas_preview_con_minimo():
        """Preview de ofertas con cantidad mínima (formato Bernabó)."""
        from parsers.bernabo_ofertas import parse_bernabo_ofertas
        f = request.files.get('archivo')
        if not f or not f.filename:
            return jsonify({'error': 'No se recibió archivo'}), 400
        ext = f.filename.rsplit('.', 1)[-1].lower()
        if ext not in ('xlsx', 'xls'):
            return jsonify({'error': 'Solo se aceptan .xlsx / .xls'}), 400
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f'.{ext}')
        f.save(tmp.name); tmp.close()
        try:
            items = parse_bernabo_ofertas(tmp.name)
            grupos = len({it['grupo_id'] for it in items if it['grupo_id'] is not None})
            return jsonify({'items': items, 'grupos': grupos or None})
        except Exception as e:
            return jsonify({'error': str(e)}), 400
        finally:
            try: os.unlink(tmp.name)
            except OSError: pass
