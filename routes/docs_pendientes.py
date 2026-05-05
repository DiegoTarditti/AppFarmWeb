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

        Fuente principal: ProductAnalytics (poblado desde análisis de ventas
        del usuario). Fallback: obs_productos + obs_ventas_mensuales (cuando
        el barcode es un IdProducto numérico de ObServer o pseudo-EAN OBS:N
        — caso típico de pedidos generados desde ObServer sin factura aún).
        """
        import os as _os
        from datetime import datetime
        with database.get_db() as session:
            pa = session.get(database.ProductAnalytics, barcode)
            if pa:
                ventas = []
                if pa.ventas_json:
                    try:
                        ventas = json.loads(pa.ventas_json)
                    except (json.JSONDecodeError, TypeError):
                        pass
                # ProductAnalytics no almacena el mínimo. Lo buscamos en
                # paralelo en obs_stock vía bridge productos.observer_id.
                minimo = 0
                from helpers import _find_producto
                prod_local = _find_producto(session, barcode)
                if prod_local and prod_local.observer_id:
                    from sqlalchemy import func as _f
                    m_row = (session.query(_f.coalesce(_f.sum(database.ObsStock.minimo), 0))
                             .filter(database.ObsStock.producto_observer == prod_local.observer_id)
                             .scalar())
                    minimo = int(m_row or 0)
                return jsonify({
                    'ok': True,
                    'nombre': pa.descripcion or '',
                    'codigo_barra': barcode,
                    'ventas': ventas,
                    'avg_monthly': float(pa.avg_monthly or 0),
                    'slope': float(pa.slope or 0),
                    'stock': pa.stock or 0,
                    'minimo': minimo,
                    'rotacion': pa.rotacion or '',
                    'tipo': pa.tipo or 'N',
                    'start_month': pa.start_month or 4,
                    'n_days': pa.n_days or 35,
                    'sin_historial': len(ventas) == 0,
                    'analizado_en': pa.actualizado_en.strftime('%d/%m/%Y') if pa.actualizado_en else None,
                    'fuente': 'analisis',
                })

            # Fallback: resolver barcode → observer_id y traer del mirror.
            obs_id = None
            if barcode.startswith('OBS:'):
                try:
                    obs_id = int(barcode[4:])
                except (ValueError, TypeError):
                    pass
            if obs_id is None:
                try:
                    obs_id = int(barcode)
                except (ValueError, TypeError):
                    pass
            if obs_id is None:
                # Bridge vía productos local (cascada legacy + 1-a-N + obs)
                from helpers import _find_producto
                prod = _find_producto(session, barcode)
                if prod and prod.observer_id:
                    obs_id = prod.observer_id

            # Si no resolvimos obs_id o no hay ObsProducto, igual respondemos
            # OK con sin_historial=True usando el Producto local (si existe).
            obs_p = session.get(database.ObsProducto, obs_id) if obs_id else None
            if not obs_p:
                from helpers import _find_producto
                prod_local = _find_producto(session, barcode)
                nombre = (prod_local.descripcion if prod_local else '') or barcode
                return jsonify({
                    'ok': True, 'nombre': nombre, 'codigo_barra': barcode,
                    'ventas': [], 'avg_monthly': 0, 'slope': 0,
                    'stock': 0, 'minimo': 0, 'rotacion': '', 'tipo': 'N',
                    'start_month': 4, 'n_days': 35, 'sin_historial': True,
                    'fuente': 'sin_datos',
                })

            id_farmacia = int(_os.environ.get('OBSERVER_ID_FARMACIA', '10525'))
            hoy = datetime.now()
            # Construir lista de los últimos 12 meses como (anio, mes)
            meses = []
            y, m = hoy.year, hoy.month
            for _ in range(12):
                meses.append((y, m))
                m -= 1
                if m == 0:
                    m, y = 12, y - 1
            meses.reverse()
            # Stock actual + mínimo configurado en ObServer
            stock_row = (session.query(
                            database.ObsStock.stock_actual,
                            database.ObsStock.minimo,
                         )
                         .filter(database.ObsStock.id_farmacia == id_farmacia,
                                 database.ObsStock.producto_observer == obs_id).first())
            stock = int(stock_row[0]) if stock_row else 0
            minimo = int(stock_row[1] or 0) if stock_row else 0
            # Ventas por mes
            rows = (session.query(database.ObsVentaMensual.anio,
                                   database.ObsVentaMensual.mes,
                                   database.ObsVentaMensual.unidades)
                    .filter(database.ObsVentaMensual.id_farmacia == id_farmacia,
                            database.ObsVentaMensual.producto_observer == obs_id).all())
            ventas_map = {(int(r[0]), int(r[1])): float(r[2] or 0) for r in rows}
            ventas = [round(ventas_map.get((y, m), 0.0), 2) for (y, m) in meses]
            n_full = max(1, len(ventas) - 1)  # excluye mes actual parcial
            avg_monthly = sum(ventas[:n_full]) / n_full
            return jsonify({
                'ok': True,
                'nombre': obs_p.descripcion or '',
                'codigo_barra': barcode,
                'ventas': ventas,
                'avg_monthly': round(avg_monthly, 2),
                'slope': 0,
                'stock': stock,
                'minimo': minimo,
                'rotacion': '',
                'tipo': 'N',
                'start_month': meses[0][1],
                'n_days': 30,
                'sin_historial': sum(ventas) == 0,
                'analizado_en': None,
                'fuente': 'observer',
            })
