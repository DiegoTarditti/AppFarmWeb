"""Batch processing routes."""

import os
from flask import render_template, request, redirect, url_for, flash, jsonify
from werkzeug.utils import secure_filename
import database
from database import InvoiceBatch
from data_extract import parse_invoice_pdf, parse_erp_excel, save_invoice_to_db, save_erp_to_db, compare_invoice_vs_erp, save_differences
from helpers import UPLOAD_FOLDER, allowed_file, get_providers


def init_app(app):

    @app.route('/batch/new')
    def batch_new():
        return render_template('batch.html', providers=get_providers())

    @app.route('/batch/add-pdf', methods=['POST'])
    def batch_add_pdf():
        proveedor_id = request.form.get('proveedor_id')
        batch_id = request.form.get('batch_id') or None
        tipo_comprobante = request.form.get('tipo_comprobante', 'FAC').upper()
        invoice_file = request.files.get('invoice_pdf')

        if not proveedor_id:
            return jsonify({'error': 'Seleccioná un proveedor.'}), 400
        if not invoice_file or not allowed_file(invoice_file.filename):
            return jsonify({'error': 'PDF inválido.'}), 400

        filename = secure_filename(invoice_file.filename)
        invoice_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        invoice_file.save(invoice_path)

        with database.get_db() as session:
            provider = session.get(database.Provider, int(proveedor_id))
            if not provider:
                return jsonify({'error': 'Proveedor no encontrado.'}), 400
            if not provider.parser_file:
                return jsonify({'error': f'El proveedor "{provider.razon_social}" no tiene parser configurado.'}), 400

            try:
                invoice_data = parse_invoice_pdf(invoice_path, provider.parser_file)
            except Exception as e:
                return jsonify({'error': f'Error al leer PDF: {e}'}), 400

            if not invoice_data.get('items'):
                return jsonify({'error': 'El parser no detectó artículos en este PDF.'}), 400

            if tipo_comprobante not in ('FAC', 'NCR'):
                tipo_comprobante = 'FAC'

            invoice = save_invoice_to_db(session, invoice_data,
                                          pdf_filename=os.path.basename(invoice_path),
                                          tipo_comprobante=tipo_comprobante)

            batch = None
            if batch_id:
                batch = session.get(InvoiceBatch, int(batch_id))
            if not batch:
                batch = InvoiceBatch(proveedor_id=int(proveedor_id))
                session.add(batch)
                session.flush()

            invoice.batch_id = batch.id
            session.commit()

            return jsonify({
                'batch_id': batch.id,
                'invoice_id': invoice.id,
                'numero_factura': invoice.numero_factura,
                'total_articulos': invoice.total_articulos or 0,
                'proveedor_razon': invoice.proveedor_razon,
                'fecha': str(invoice.fecha),
                'tipo_comprobante': invoice.tipo_comprobante,
            }), 200

    @app.route('/batch/process', methods=['POST'])
    def batch_process():
        batch_id = request.form.get('batch_id')
        erp_file = request.files.get('erp_excel')

        if not batch_id:
            flash('Batch no encontrado.')
            return redirect(url_for('index'))
        if not erp_file or not allowed_file(erp_file.filename):
            flash('ERP Excel inválido.')
            return redirect(url_for('batch_new'))

        erp_filename = secure_filename(erp_file.filename)
        erp_path = os.path.join(app.config['UPLOAD_FOLDER'], erp_filename)
        erp_file.save(erp_path)

        try:
            erp_data = parse_erp_excel(erp_path)
        except Exception as e:
            flash(f'Error al leer el ERP: {e}')
            return redirect(url_for('batch_new'))

        with database.get_db() as session:
            batch = session.get(InvoiceBatch, int(batch_id))
            if not batch:
                flash('Batch no encontrado.')
                return redirect(url_for('index'))

            batch.erp_filename = erp_filename
            batch.estado = 'PROCESADO'
            session.commit()

            save_erp_to_db(session, erp_data)

            invoices = session.query(database.Invoice).filter_by(batch_id=batch.id).all()
            for invoice in invoices:
                invoice.erp_filename = erp_filename
                differences = compare_invoice_vs_erp(session, invoice.id)
                save_differences(session, invoice.id, differences)
            session.commit()
            batch_id_result = batch.id

        return redirect(url_for('batch_results', batch_id=batch_id_result))

    @app.route('/batch/<int:batch_id>/results')
    def batch_results(batch_id):
        from sqlalchemy import func as _func
        with database.get_db() as session:
            batch = session.get(InvoiceBatch, batch_id)
            if not batch:
                flash('Batch no encontrado.')
                return redirect(url_for('index'))

            provider = session.get(database.Provider, batch.proveedor_id)
            invoices = session.query(database.Invoice).filter_by(batch_id=batch_id).all()
            inv_ids = [inv.id for inv in invoices]

            diff_counts = dict(
                session.query(database.StockDifference.factura_id,
                              _func.count(database.StockDifference.id))
                .filter(database.StockDifference.factura_id.in_(inv_ids))
                .group_by(database.StockDifference.factura_id).all()
            ) if inv_ids else {}

            invoice_data = [
                {'invoice': inv, 'diff_count': diff_counts.get(inv.id, 0)}
                for inv in invoices
            ]
            return render_template('batch_results.html', batch=batch, provider=provider,
                                   invoices=invoice_data)
