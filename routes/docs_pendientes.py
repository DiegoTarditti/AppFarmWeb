"""Documentos pendientes routes."""

import os
import json
from flask import render_template, request, redirect, url_for, flash, jsonify
from werkzeug.utils import secure_filename
import database


def init_app(app):

    @app.route('/docs-pendientes')
    def docs_pendientes():
        from sqlalchemy.orm import joinedload
        with database.get_db() as session:
            docs = (session.query(database.DocumentoPendiente)
                    .options(joinedload(database.DocumentoPendiente.proveedor))
                    .order_by(database.DocumentoPendiente.fecha_detectado.desc())
                    .all())
            return render_template('docs_pendientes.html', docs=docs)

    @app.route('/docs-pendientes/upload', methods=['POST'])
    def docs_pendientes_upload():
        """Subir uno o más PDFs a la bandeja de pendientes."""
        files = request.files.getlist('pdfs')
        if not files or not files[0].filename:
            flash('Seleccioná al menos un archivo PDF.')
            return redirect(url_for('docs_pendientes'))

        with database.get_db() as session:
            try:
                existentes = {d.filename for d in session.query(database.DocumentoPendiente)
                              .filter(database.DocumentoPendiente.estado == 'PENDIENTE').all()}
                nuevos = 0
                for f in files:
                    if not f.filename.lower().endswith('.pdf'):
                        continue
                    fname = secure_filename(f.filename)
                    if fname in existentes:
                        continue
                    dst = os.path.join(app.config['UPLOAD_FOLDER'], fname)
                    f.save(dst)
                    doc = database.DocumentoPendiente(
                        filename=fname,
                        ruta_completa=dst,
                    )
                    session.add(doc)
                    existentes.add(fname)
                    nuevos += 1
                session.commit()
                if nuevos:
                    flash(f'{nuevos} documento(s) subido(s).')
                else:
                    flash('No se subieron documentos nuevos (ya existían o no eran PDF).')
            except Exception as e:
                session.rollback()
                flash(f'Error: {e}')
        return redirect(url_for('docs_pendientes'))

    @app.route('/docs-pendientes/<int:doc_id>/procesar')
    def docs_pendientes_procesar(doc_id):
        """Redirige a la pantalla de ingreso con el PDF pre-seleccionado."""
        with database.get_db() as session:
            doc = session.get(database.DocumentoPendiente, doc_id)
            if not doc or doc.estado != 'PENDIENTE':
                flash('Documento no encontrado o ya procesado.')
                return redirect(url_for('docs_pendientes'))
            dst = os.path.join(app.config['UPLOAD_FOLDER'], doc.filename)
            if not os.path.isfile(dst):
                flash(f'Archivo no encontrado en el servidor.')
                return redirect(url_for('docs_pendientes'))
            filename = doc.filename
            doc_id_val = doc.id
        return redirect(url_for('ingresos', pdf_pendiente=filename, doc_pendiente_id=doc_id_val))

    @app.route('/docs-pendientes/upload-api', methods=['POST'])
    def docs_pendientes_upload_api():
        """API para el agente local: recibe PDFs via multipart y devuelve JSON."""
        files = request.files.getlist('pdfs')
        if not files or not files[0].filename:
            return jsonify({'ok': False, 'error': 'No se recibieron archivos'}), 400

        with database.get_db() as session:
            try:
                existentes = {d.filename for d in session.query(database.DocumentoPendiente)
                              .filter(database.DocumentoPendiente.estado == 'PENDIENTE').all()}
                nuevos = 0
                nombres = []
                for f in files:
                    if not f.filename.lower().endswith('.pdf'):
                        continue
                    fname = secure_filename(f.filename)
                    if fname in existentes:
                        continue
                    dst = os.path.join(app.config['UPLOAD_FOLDER'], fname)
                    f.save(dst)
                    doc = database.DocumentoPendiente(
                        filename=fname,
                        ruta_completa=dst,
                    )
                    session.add(doc)
                    existentes.add(fname)
                    nuevos += 1
                    nombres.append(fname)
                session.commit()
                return jsonify({'ok': True, 'nuevos': nuevos, 'archivos': nombres})
            except Exception as e:
                session.rollback()
                return jsonify({'ok': False, 'error': str(e)}), 500

    @app.route('/docs-pendientes/<int:doc_id>/delete', methods=['POST'])
    def docs_pendientes_delete(doc_id):
        with database.get_db() as session:
            try:
                doc = session.get(database.DocumentoPendiente, doc_id)
                if doc:
                    session.delete(doc)
                    session.commit()
                    flash('Registro eliminado.')
            except Exception as e:
                session.rollback()
                flash(f'Error: {e}')
        return redirect(url_for('docs_pendientes'))

    @app.route('/api/product/<barcode>/chart')
    def api_product_chart(barcode):
        """Devuelve datos de ventas históricas de un producto desde ProductAnalytics."""
        with database.get_db() as session:
            pa = session.get(database.ProductAnalytics, barcode)
            if not pa:
                return jsonify({'ok': False, 'error': 'Producto no encontrado. Procesá un análisis de ventas primero.'}), 404
            ventas = []
            if pa.ventas_json:
                try:
                    ventas = json.loads(pa.ventas_json)
                except (json.JSONDecodeError, TypeError):
                    pass
            return jsonify({
                'ok': True,
                'nombre': pa.descripcion or '',
                'codigo_barra': barcode,
                'ventas': ventas,
                'avg_monthly': float(pa.avg_monthly or 0),
                'slope': float(pa.slope or 0),
                'stock': pa.stock or 0,
                'rotacion': pa.rotacion or '',
                'tipo': pa.tipo or 'N',
                'start_month': pa.start_month or 4,
                'n_days': pa.n_days or 35,
                'sin_historial': len(ventas) == 0,
                'analizado_en': pa.actualizado_en.strftime('%d/%m/%Y') if pa.actualizado_en else None,
            })
