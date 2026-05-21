"""Documentos pendientes routes."""

import json
import os

from flask import flash, jsonify, redirect, render_template, request, url_for
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
                flash('Archivo no encontrado en el servidor.')
                return redirect(url_for('docs_pendientes'))
            filename = doc.filename
            doc_id_val = doc.id
        return redirect(url_for('ingresos', pdf_pendiente=filename, doc_pendiente_id=doc_id_val))

    @app.route('/docs-pendientes/upload-api', methods=['POST'])
    def docs_pendientes_upload_api():
        """API para el agente local: recibe PDFs via multipart y devuelve JSON."""
        expected = os.environ.get('AGENTE_TOKEN', '')
        if expected:
            sent = request.headers.get('X-Agent-Token', '')
            if sent != expected:
                return jsonify({'ok': False, 'error': 'token inválido'}), 401
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

    @app.route('/api/notifications')
    def api_notifications():
        """Notificaciones para la campanita del sidebar. Liviano — solo counts."""
        with database.get_db() as session:
            q = (session.query(database.DocumentoPendiente)
                 .filter(database.DocumentoPendiente.estado == 'PENDIENTE')
                 .order_by(database.DocumentoPendiente.fecha_detectado.desc()))
            count = q.count()
            ultimos = [{
                'id': d.id,
                'filename': d.filename,
                'fecha': d.fecha_detectado.strftime('%d/%m %H:%M') if d.fecha_detectado else '',
            } for d in q.limit(5).all()]
        max_id = max((d['id'] for d in ultimos), default=0)
        return jsonify({
            'docs_pendientes': {'count': count, 'ultimos': ultimos, 'max_id': max_id},
        })

    @app.route('/api/product/<path:barcode>/chart')
    def api_product_chart(barcode):
        """Devuelve datos de ventas históricas de un producto.

        Calcula EN VIVO desde obs_stock + obs_ventas_mensuales vía el source of
        truth unico (services.producto_metrics). NO usa ProductAnalytics: esa
        tabla queda stale (~1 mes) y producia divergencias con el resto de la app
        (mismo producto con stock/prom distintos segun la pantalla).

        Resuelve barcode → observer_id (OBS:N, EAN real vía bridge, EAN en
        obs_codigos_barras, o IdProducto numerico directo). Si no resuelve,
        responde sin_historial=True con el nombre del Producto local si existe.
        """
        from helpers import _find_producto
        from purchase_engine import tipo_producto
        from services.producto_metrics import metricas_producto
        with database.get_db() as session:
            # Resolver barcode → observer_id.
            obs_id = None
            if barcode.startswith('OBS:'):
                try:
                    obs_id = int(barcode[4:])
                except (ValueError, TypeError):
                    pass
            if obs_id is None:
                prod = _find_producto(session, barcode)
                if prod and prod.observer_id:
                    obs_id = prod.observer_id
            if obs_id is None:
                row = (session.query(database.ObsCodigoBarras.producto_observer)
                       .filter(database.ObsCodigoBarras.codigo_barras == barcode,
                               database.ObsCodigoBarras.fecha_baja.is_(None))
                       .first())
                if row:
                    obs_id = row[0]
            if obs_id is None:
                try:
                    obs_id = int(barcode)
                except (ValueError, TypeError):
                    pass

            obs_p = session.get(database.ObsProducto, obs_id) if obs_id else None
            if not obs_p:
                prod_local = _find_producto(session, barcode)
                nombre = (prod_local.descripcion if prod_local else '') or barcode
                return jsonify({
                    'ok': True, 'nombre': nombre, 'codigo_barra': barcode,
                    'ventas': [], 'avg_monthly': 0, 'avg_3m': 0, 'avg_12m': 0,
                    'slope': 0, 'stock': 0, 'minimo': 0, 'rotacion': '', 'tipo': 'N',
                    'start_month': 4, 'n_days': 35, 'sin_historial': True,
                    'fuente': 'sin_datos',
                })

            m = metricas_producto(session, obs_id)
            return jsonify({
                'ok': True,
                'nombre': obs_p.descripcion or '',
                'codigo_barra': barcode,
                'ventas': m['ventas12'],
                'avg_monthly': m['avg_monthly'],   # alias de avg_12m (backcompat)
                'avg_3m': m['avg_3m'],
                'avg_12m': m['avg_12m'],
                'slope': m['slope'],
                'stock': m['stock'],
                'minimo': m['minimo'],
                'rotacion': m['rotacion'],
                'tipo': tipo_producto(m['ventas12']),
                'start_month': m['start_month'],
                'n_days': 30,
                'sin_historial': m['sin_historial'],
                'analizado_en': None,
                'fuente': 'observer',
            })
