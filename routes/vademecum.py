"""Vademécum routes: búsqueda integrada en PR Vademécum con cache local."""

from flask import render_template, request, jsonify
import database
from database import Producto


def init_app(app):

    @app.route('/vademecum')
    def vademecum_index():
        return render_template('vademecum.html')

    @app.route('/api/vademecum/search')
    def api_vademecum_search():
        """Busca en PR Vademécum.  Devuelve [{name, url, slug, tipo}]."""
        q = (request.args.get('q') or '').strip()
        if not q or len(q) < 2:
            return jsonify([])
        try:
            from parsers.vademecum import search
            results = search(q)
            return jsonify(results)
        except Exception as e:
            return jsonify({'error': str(e)}), 502

    @app.route('/api/vademecum/detail')
    def api_vademecum_detail():
        """Trae el detalle de un medicamento.  ?slug=amoxidal-244"""
        slug = (request.args.get('slug') or '').strip()
        if not slug:
            return jsonify({'error': 'slug requerido'}), 400
        try:
            from parsers.vademecum import detail
            data = detail(slug)
            return jsonify(data)
        except Exception as e:
            return jsonify({'error': str(e)}), 502

    @app.route('/api/vademecum/save', methods=['POST'])
    def api_vademecum_save():
        """Guarda datos de vademécum en un Producto existente.

        Body JSON: {producto_id, monodroga, presentacion, accion_terapeutica}
        """
        body = request.get_json(silent=True) or {}
        prod_id = body.get('producto_id')
        if not prod_id:
            return jsonify({'error': 'producto_id requerido'}), 400

        with database.get_db() as session:
            try:
                prod = session.get(Producto, int(prod_id))
                if not prod:
                    return jsonify({'error': 'Producto no encontrado'}), 404
                if body.get('monodroga'):
                    prod.monodroga = body['monodroga'].strip()
                if body.get('presentacion'):
                    prod.presentacion = body['presentacion'].strip()
                if body.get('accion_terapeutica'):
                    prod.accion_terapeutica = body['accion_terapeutica'].strip()
                from datetime import datetime as _dt
                prod.actualizado_en = _dt.now()
                session.commit()
                return jsonify({'ok': True})
            except Exception as e:
                session.rollback()
                return jsonify({'error': str(e)}), 500
