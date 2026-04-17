"""Producto routes: list, CRUD, API."""

from flask import render_template, request, jsonify
import database
from database import Producto, Laboratorio
from helpers import _find_producto


def init_app(app):

    @app.route('/productos')
    def productos_list():
        with database.get_db() as session:
            labs = [{'id': l.id, 'nombre': l.nombre}
                    for l in session.query(Laboratorio).order_by(Laboratorio.nombre).all()]
            return render_template('productos.html', laboratorios=labs)

    @app.route('/api/productos')
    def api_productos():
        with database.get_db() as session:
            from sqlalchemy.orm import joinedload
            prods = (session.query(Producto)
                     .options(joinedload(Producto.laboratorio))
                     .order_by(Producto.descripcion).all())
            data = [
                {
                    'id': p.id,
                    'codigo_barra': p.codigo_barra,
                    'descripcion': p.descripcion or '',
                    'alt1': p.codigo_barra_alt1 or '',
                    'alt2': p.codigo_barra_alt2 or '',
                    'alt3': p.codigo_barra_alt3 or '',
                    'precio_pvp': float(p.precio_pvp) if p.precio_pvp else None,
                    'laboratorio_id': p.laboratorio_id or '',
                    'laboratorio_nombre': p.laboratorio.nombre if p.laboratorio else '',
                    'actualizado_en': p.actualizado_en.strftime('%d/%m/%Y') if p.actualizado_en else '',
                    'es_pack': p.es_pack or 0,
                }
                for p in prods
            ]
            return jsonify(data)

    @app.route('/producto/<int:prod_id>/laboratorio', methods=['POST'])
    def producto_set_laboratorio(prod_id):
        lab_id = request.form.get('laboratorio_id') or None
        with database.get_db() as session:
            prod = session.get(Producto, prod_id)
            if prod:
                prod.laboratorio_id = int(lab_id) if lab_id else None
                session.commit()
        return ('', 204)

    @app.route('/producto/<int:prod_id>/edit', methods=['POST'])
    def producto_edit(prod_id):
        data = request.get_json(silent=True) or {}
        field = data.get('field')
        value = (data.get('value') or '').strip()
        allowed = {'descripcion', 'codigo_barra', 'codigo_barra_alt1', 'codigo_barra_alt2', 'codigo_barra_alt3', 'precio_pvp', 'es_pack'}
        if field not in allowed:
            return {'error': 'Campo no permitido'}, 400
        with database.get_db() as session:
            prod = session.get(Producto, prod_id)
            if not prod:
                return {'error': 'No encontrado'}, 404
            if field == 'precio_pvp':
                try:
                    setattr(prod, field, float(value.replace(',', '.')) if value else None)
                except ValueError:
                    return {'error': 'Precio inválido'}, 400
            elif field == 'es_pack':
                prod.es_pack = 1 if value in ('1', 'true', 'True') else 0
            else:
                setattr(prod, field, value or None)
            from datetime import datetime as _dt
            prod.actualizado_en = _dt.now().date()
            session.commit()
            return {'ok': True}

    @app.route('/producto/edit-by-barcode', methods=['POST'])
    def producto_edit_by_barcode():
        data = request.get_json(silent=True) or {}
        cb    = (data.get('codigo_barra') or '').strip()
        field = data.get('field')
        value = (data.get('value') or '').strip()
        if not cb or field not in {'descripcion', 'precio_pvp'}:
            return {'error': 'Parámetros inválidos'}, 400
        with database.get_db() as session:
            try:
                prod = _find_producto(session, cb)
                if not prod:
                    prod = Producto(codigo_barra=cb)
                    session.add(prod)
                    session.flush()
                if field == 'precio_pvp':
                    prod.precio_pvp = float(value.replace(',', '.')) if value else None
                else:
                    setattr(prod, field, value or None)
                from datetime import datetime as _dt
                prod.actualizado_en = _dt.now().date()
                session.commit()
                return {'ok': True, 'id': prod.id}
            except Exception as e:
                session.rollback()
                return {'error': str(e)}, 500

    @app.route('/producto/create', methods=['POST'])
    def producto_create():
        data = request.get_json(silent=True) or {}
        cb = (data.get('codigo_barra') or '').strip()
        if not cb:
            return {'error': 'Código de barra requerido'}, 400
        with database.get_db() as session:
            try:
                if session.query(Producto).filter_by(codigo_barra=cb).first():
                    return {'error': 'Ya existe un producto con ese código'}, 409
                prod = Producto(
                    codigo_barra=cb,
                    descripcion=(data.get('descripcion') or '').strip() or None,
                    precio_pvp=float(data['precio_pvp']) if data.get('precio_pvp') else None,
                    es_pack=1 if data.get('es_pack') else 0,
                )
                session.add(prod)
                session.commit()
                return {'ok': True, 'id': prod.id}
            except Exception as e:
                session.rollback()
                return {'error': str(e)}, 500

    @app.route('/producto/<int:prod_id>/delete', methods=['POST'])
    def producto_delete(prod_id):
        with database.get_db() as session:
            prod = session.get(Producto, prod_id)
            if not prod:
                return {'error': 'No encontrado'}, 404
            session.delete(prod)
            session.commit()
            return {'ok': True}
