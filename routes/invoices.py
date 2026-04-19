"""Invoice routes: upload, process_upload, parse-helper, compare, items, pick-fields, header."""

import os
from flask import render_template, request, redirect, url_for, flash, jsonify, make_response
from werkzeug.utils import secure_filename
import database
from data_extract import (parse_invoice_pdf, parse_erp_excel, compare_invoice_vs_erp,
                          save_invoice_to_db, save_erp_to_db, save_differences,
                          get_saved_differences, get_erp_items_with_issues,
                          save_barcode_mapping)
from helpers import (
    UPLOAD_FOLDER, allowed_file, get_config, get_providers,
    _make_parser_slug, _ensure_parser_file, _get_or_create_provider_by_name,
    _upsert_producto, _add_alt_barcode, _build_item_pattern, _bulk_upsert_productos,
)


def process_upload(app):
    is_new = request.form.get('is_new_provider') == '1'
    erp_file = request.files.get('erp_excel')

    if not erp_file or not allowed_file(erp_file.filename):
        return {'error': 'Por favor cargue un archivo de informe ERP Excel válido.'}, 400

    if is_new:
        razon_social = request.form.get('provider_name_new', '').strip()
        pdf_filename = request.form.get('pdf_temp_filename', '').strip()
        if not razon_social:
            return {'error': 'El nombre del proveedor es obligatorio.'}, 400
        if not pdf_filename:
            return {'error': 'El archivo PDF no está disponible. Intentá de nuevo.'}, 400

        invoice_path = os.path.join(app.config['UPLOAD_FOLDER'], pdf_filename)
        if not os.path.exists(invoice_path):
            return {'error': 'El archivo PDF ya no está disponible. Intentá de nuevo.'}, 400

        parser_name = _make_parser_slug(razon_social)
        _ensure_parser_file(parser_name, razon_social)
        proveedor_id, parser_file = _get_or_create_provider_by_name(razon_social,
                                                                     parser_name=parser_name)
    else:
        proveedor_id = request.form.get('proveedor_id')
        if not proveedor_id:
            return {'error': 'Seleccioná un proveedor antes de cargar los archivos.'}, 400

        pdf_pendiente = request.form.get('pdf_pendiente_filename', '').strip()
        if pdf_pendiente:
            invoice_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(pdf_pendiente))
            if not os.path.exists(invoice_path):
                return {'error': 'El PDF pendiente ya no está disponible.'}, 400
        else:
            invoice_file = request.files.get('invoice_pdf')
            if not invoice_file or not allowed_file(invoice_file.filename):
                return {'error': 'Por favor cargue un archivo de factura PDF válido.'}, 400
            invoice_filename = secure_filename(invoice_file.filename)
            invoice_path = os.path.join(app.config['UPLOAD_FOLDER'], invoice_filename)
            invoice_file.save(invoice_path)

        with database.get_db() as session:
            provider = session.get(database.Provider, int(proveedor_id))

        if not provider:
            return {'error': 'Proveedor no encontrado.'}, 400
        if not provider.parser_file:
            return {'error': f'El proveedor "{provider.razon_social}" no tiene parser configurado.'}, 400

        parser_file = provider.parser_file

    erp_filename = secure_filename(erp_file.filename)
    erp_path = os.path.join(app.config['UPLOAD_FOLDER'], erp_filename)
    erp_file.save(erp_path)

    try:
        invoice_data = parse_invoice_pdf(invoice_path, parser_file)
    except Exception as e:
        return {'error': f'Error al leer el PDF de factura: {e}'}, 400

    try:
        erp_data = parse_erp_excel(erp_path)
    except Exception as e:
        return {'error': f'Error al leer el Excel ERP: {e}. Asegurate de subir un archivo .xlsx válido.'}, 400

    if not invoice_data.get('items'):
        try:
            with database.get_db() as _session:
                _tipo = request.form.get('tipo_comprobante', 'FAC').upper()
                if _tipo not in ('FAC', 'NCR'):
                    _tipo = 'FAC'
                _inv = save_invoice_to_db(_session, {**invoice_data, 'items': []},
                                          pdf_filename=os.path.basename(invoice_path),
                                          tipo_comprobante=_tipo)
                _inv.erp_filename = erp_filename
                _session.commit()
                save_erp_to_db(_session, erp_data)
                _invoice_id = _inv.id
        except Exception as e:
            return {'error': f'Error al guardar encabezado: {e}'}, 400
        return {'parse_failed': True, 'invoice_id': _invoice_id}, 202

    try:
        with database.get_db() as session:
            tipo_comprobante = request.form.get('tipo_comprobante', 'FAC').upper()
            if tipo_comprobante not in ('FAC', 'NCR'):
                tipo_comprobante = 'FAC'
            invoice = save_invoice_to_db(session, invoice_data,
                                         pdf_filename=os.path.basename(invoice_path),
                                         tipo_comprobante=tipo_comprobante)
            invoice.erp_filename = erp_filename
            session.commit()
            save_erp_to_db(session, erp_data)
            differences = compare_invoice_vs_erp(session, invoice.id)
            save_differences(session, invoice.id, differences)
            try:
                erp_rows = session.query(database.ErpStock).all()
                _bulk_upsert_productos(session, [
                    (e.codigo_barra, e.descripcion, float(e.precio_unitario) if e.precio_unitario else None, None)
                    for e in erp_rows
                ])
                _prov = session.get(database.Provider, int(proveedor_id)) if proveedor_id else None
                if not _prov or _prov.grabar_productos != 0:
                    inv_rows = session.query(database.InvoiceItem).filter_by(factura_id=invoice.id).all()
                    _bulk_upsert_productos(session, [
                        (it.codigo_barra, it.descripcion, None, invoice.fecha)
                        for it in inv_rows
                    ])
                session.commit()
            except Exception:
                app.logger.warning('Error al upsert productos tras upload', exc_info=True)
                session.rollback()
            saved_differences = get_saved_differences(session, invoice.id)
    except Exception as e:
        return {'error': f'Error al procesar los datos: {e}'}, 400

    return {
        'invoice': invoice,
        'differences': [
            {
                'id': d.id,
                'codigo_barra': d.codigo_barra,
                'descripcion': d.descripcion,
                'cantidad_factura': d.cantidad_factura,
                'cantidad_erp': d.cantidad_erp,
                'diferencia': d.diferencia,
                'observaciones': d.observaciones,
            }
            for d in saved_differences
        ]
    }, 200


def init_app(app):

    @app.route('/upload', methods=['POST'])
    def upload_files():
        result, status = process_upload(app)
        if status == 202 and result.get('parse_failed'):
            flash('El parser no detectó artículos. Usá el asistente para extraerlos.', 'warning')
            return redirect(url_for('parse_helper', invoice_id=result['invoice_id']))
        if status != 200:
            flash(result['error'])
            return redirect(url_for('index'))

        doc_pendiente_id = request.form.get('doc_pendiente_id', type=int)
        if doc_pendiente_id:
            with database.get_db() as session:
                try:
                    doc = session.get(database.DocumentoPendiente, doc_pendiente_id)
                    if doc:
                        doc.estado = 'PROCESADO'
                        doc.factura_id = result['invoice'].id
                        inv = result['invoice']
                        if inv.proveedor_cuit:
                            prov = session.query(database.Provider).filter_by(cuit=inv.proveedor_cuit).first()
                            if prov:
                                doc.proveedor_id = prov.id
                                cfg = get_config()
                                ruta_base = cfg.get('ruta_facturas', '')
                                if ruta_base and os.path.isfile(doc.ruta_completa):
                                    import shutil
                                    prov_dir = os.path.join(ruta_base, secure_filename(prov.razon_social))
                                    os.makedirs(prov_dir, exist_ok=True)
                                    dst = os.path.join(prov_dir, doc.filename)
                                    try:
                                        shutil.move(doc.ruta_completa, dst)
                                        doc.ruta_completa = dst
                                    except OSError:
                                        pass
                        session.commit()
                except Exception:
                    app.logger.warning('Error al actualizar doc pendiente', exc_info=True)
                    session.rollback()

        return redirect(url_for('compare_view', invoice_id=result['invoice'].id))

    @app.route('/api/upload', methods=['POST'])
    def upload_files_api():
        result, status = process_upload(app)
        if status != 200:
            return jsonify(result), status
        return jsonify({
            'invoice': {
                'id': result['invoice'].id,
                'numero_factura': result['invoice'].numero_factura,
                'fecha': str(result['invoice'].fecha),
                'proveedor_razon': result['invoice'].proveedor_razon,
                'total': float(result['invoice'].total or 0),
                'total_articulos': result['invoice'].total_articulos,
            },
            'differences': result['differences']
        }), 200

    @app.route('/invoice/<int:invoice_id>/parse-helper')
    def parse_helper(invoice_id):
        import pdfplumber as _plumber
        with database.get_db() as session:
            invoice = session.get(database.Invoice, invoice_id)
        if not invoice:
            flash('Factura no encontrada.')
            return redirect(url_for('index'))
        pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], invoice.pdf_filename or '')
        pdf_text = ''
        if os.path.exists(pdf_path):
            with _plumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    pdf_text += (page.extract_text() or '') + '\n\n'
        return render_template('invoice_parse_helper.html', invoice=invoice, pdf_text=pdf_text)

    @app.route('/invoice/<int:invoice_id>/auto-table', methods=['POST'])
    def auto_table(invoice_id):
        import pdfplumber as _plumber
        from collections import defaultdict as _dd
        with database.get_db() as session:
            invoice = session.get(database.Invoice, invoice_id)
        if not invoice:
            return jsonify({'error': 'Factura no encontrada'}), 404
        pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], invoice.pdf_filename or '')
        if not os.path.exists(pdf_path):
            return jsonify({'error': 'PDF no encontrado'}), 404

        tables = []
        with _plumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                for tbl in (page.extract_tables() or []):
                    if tbl and len(tbl) > 2:
                        tables.append(tbl)

        if tables:
            best = max(tables, key=lambda t: len(t))
            return jsonify({'source': 'table', 'rows': best})

        all_words = []
        with _plumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                all_words.extend(page.extract_words(x_tolerance=3, y_tolerance=3) or [])

        rows_dict = _dd(list)
        for w in all_words:
            y_key = round(w['top'] / 4) * 4
            rows_dict[y_key].append(w)

        word_rows = []
        for y in sorted(rows_dict.keys()):
            row = sorted(rows_dict[y], key=lambda w: w['x0'])
            word_rows.append([w['text'] for w in row])

        return jsonify({'source': 'words', 'rows': word_rows[:80]})

    @app.route('/invoice/<int:invoice_id>/map-columns', methods=['GET', 'POST'])
    def map_columns(invoice_id):
        import pdfplumber as _plumber
        import json as _json
        from collections import defaultdict as _dd

        with database.get_db() as session:
            invoice = session.get(database.Invoice, invoice_id)

            if request.method == 'POST':
                rows_json = request.form.get('rows_json', '[]')
                mapping = {
                    'codigo_barra':    int(request.form.get('col_codigo',  -1)),
                    'descripcion':     int(request.form.get('col_desc',    -1)),
                    'cantidad':        int(request.form.get('col_cant',    -1)),
                    'precio_unitario': int(request.form.get('col_precio',  -1)),
                    'dto':             int(request.form.get('col_dto',     -1)),
                    'importe':         int(request.form.get('col_importe', -1)),
                    'lote':            int(request.form.get('col_lote',    -1)),
                }
                header_row = int(request.form.get('header_row', 0))
                rows = _json.loads(rows_json)

                def _f(s):
                    if not s:
                        return None
                    try:
                        return float(str(s).replace('.', '').replace(',', '.'))
                    except Exception:
                        return None

                def _col(row, idx):
                    if idx < 0 or idx >= len(row):
                        return None
                    v = row[idx]
                    return str(v).strip() if v else None

                tipo = invoice.tipo_comprobante or 'FAC'
                sign = -1 if tipo == 'NCR' else 1
                saved = 0
                for i, row in enumerate(rows):
                    if i <= header_row:
                        continue
                    if not any(row):
                        continue
                    desc    = _col(row, mapping['descripcion'])
                    codigo  = _col(row, mapping['codigo_barra'])
                    if not desc and not codigo:
                        continue
                    cant_s  = _col(row, mapping['cantidad'])
                    precio  = _f(_col(row, mapping['precio_unitario']))
                    dto     = _f(_col(row, mapping['dto']))
                    importe = _f(_col(row, mapping['importe']))
                    lote    = _col(row, mapping['lote'])
                    try:
                        cant = int(float(cant_s)) if cant_s else 0
                    except Exception:
                        cant = 0
                    session.add(database.InvoiceItem(
                        factura_id=invoice_id, codigo_barra=codigo, descripcion=desc,
                        cantidad=cant,
                        precio_unitario=sign * precio if precio is not None else None,
                        dto=dto,
                        importe=sign * importe if importe is not None else None,
                        lote=lote,
                    ))
                    saved += 1

                if saved > 0:
                    invoice.total_articulos = saved
                    session.commit()
                    differences = compare_invoice_vs_erp(session, invoice_id)
                    save_differences(session, invoice_id, differences)
                    flash(f'{saved} artículos guardados desde el mapeo de columnas.')
                    return redirect(url_for('compare_view', invoice_id=invoice_id))
                flash('No se pudieron extraer artículos con esa configuración.')
                return redirect(url_for('map_columns', invoice_id=invoice_id))

            # GET
            pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], invoice.pdf_filename or '')
            rows_preview = []
            all_rows = []
            source = 'none'

            if os.path.exists(pdf_path):
                tables = []
                with _plumber.open(pdf_path) as pdf:
                    for page in pdf.pages:
                        for tbl in (page.extract_tables() or []):
                            if tbl and len(tbl) > 2:
                                tables.append(tbl)
                if tables:
                    all_rows = max(tables, key=lambda t: len(t))
                    source = 'table'
                else:
                    all_words = []
                    with _plumber.open(pdf_path) as pdf:
                        for page in pdf.pages:
                            all_words.extend(page.extract_words(x_tolerance=3, y_tolerance=3) or [])
                    rows_dict = _dd(list)
                    for w in all_words:
                        y_key = round(w['top'] / 4) * 4
                        rows_dict[y_key].append(w)
                    for y in sorted(rows_dict.keys()):
                        row = sorted(rows_dict[y], key=lambda w: w['x0'])
                        all_rows.append([w['text'] for w in row])
                    source = 'words'
                rows_preview = all_rows[:60]

            num_cols = max((len(r) for r in rows_preview[:10] if r), default=0)
            import json as _j
            return render_template('invoice_map_columns.html', invoice=invoice,
                                   rows=rows_preview, rows_json=_j.dumps(all_rows),
                                   source=source, num_cols=num_cols)

    @app.route('/invoice/<int:invoice_id>/pick-items', methods=['GET'])
    def pick_items(invoice_id):
        import pdfplumber as _plumber
        with database.get_db() as session:
            invoice = session.get(database.Invoice, invoice_id)
        if not invoice:
            flash('Factura no encontrada.')
            return redirect(url_for('index'))
        pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], invoice.pdf_filename or '')
        pdf_text = ''
        if os.path.exists(pdf_path):
            with _plumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    pdf_text += (page.extract_text() or '') + '\n\n'
        return render_template('invoice_pick_items.html', invoice=invoice, pdf_text=pdf_text)

    @app.route('/invoice/<int:invoice_id>/pick-items/infer', methods=['POST'])
    def pick_items_infer(invoice_id):
        import re as _re
        import pdfplumber as _plumber

        body = request.get_json(silent=True) or {}
        example_line = body.get('example_line', '')
        selections = body.get('selections', [])

        with database.get_db() as session:
            invoice = session.get(database.Invoice, invoice_id)
        if not invoice:
            return jsonify({'error': 'Factura no encontrada'}), 404

        if not example_line or not selections:
            return jsonify({'error': 'Faltan datos'}), 400

        pattern, fields, base_fields, _base = _build_item_pattern(example_line, selections)

        pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], invoice.pdf_filename or '')
        pdf_text = ''
        if os.path.exists(pdf_path):
            with _plumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    pdf_text += (page.extract_text() or '') + '\n'

        rx = _re.compile(pattern, _re.MULTILINE)

        rows = []
        for m in rx.finditer(pdf_text):
            row = {b: [] for b in base_fields}
            for i, f in enumerate(fields):
                val = m.group(i+1)
                if val:
                    row[_base(f)].append(val)
            rows.append({b: _re.sub(r'\s+', ' ', ' '.join(row[b]).strip()) for b in base_fields})

        return jsonify({'pattern': pattern, 'fields': base_fields, 'rows': rows})

    @app.route('/invoice/<int:invoice_id>/pick-items/save', methods=['POST'])
    def pick_items_save(invoice_id):
        import re as _re
        import datetime as _dt
        body = request.get_json(silent=True) or {}
        rows = body.get('rows', [])
        header = body.get('header', {}) or {}

        with database.get_db() as session:
            invoice = session.get(database.Invoice, invoice_id)
            if not invoice:
                return jsonify({'error': 'Factura no encontrada'}), 404

            tipo = invoice.tipo_comprobante or 'FAC'
            sign = -1 if tipo == 'NCR' else 1

            def _f(s):
                if s is None or s == '':
                    return None
                try:
                    return float(str(s).replace('.', '').replace(',', '.'))
                except Exception:
                    return None

            if header.get('razon_social'):
                invoice.proveedor_razon = header['razon_social'].strip()
            if header.get('numero_factura'):
                invoice.numero_factura = header['numero_factura'].strip()
            if header.get('total'):
                t = _f(header['total'])
                if t is not None:
                    invoice.total = sign * t
            if header.get('fecha'):
                raw = header['fecha'].strip()
                m = _re.search(r'(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})', raw)
                if m:
                    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    if y < 100:
                        y += 2000
                    try:
                        invoice.fecha = _dt.date(y, mo, d)
                    except Exception:
                        pass

            aliases = {
                'descripcion': ['descripcion', 'concepto', 'detalle', 'producto', 'articulo', 'descripción'],
                'codigo_barra': ['codigo_barra', 'codigo', 'código', 'ean', 'cod'],
                'cantidad': ['cantidad', 'cant', 'qty'],
                'precio_unitario': ['precio_unitario', 'pcio_unit', 'pcio', 'precio', 'unitario'],
                'importe': ['importe', 'total', 'subtotal', 'monto'],
                'dto': ['dto', 'descto', 'descuento'],
                'lote': ['lote'],
            }
            def _pick(r, std):
                for k in aliases.get(std, [std]):
                    if r.get(k):
                        return r.get(k)
                return None

            saved = 0
            for r in rows:
                desc = str(_pick(r, 'descripcion') or '').strip()[:150]
                if not desc:
                    continue
                precio  = _f(_pick(r, 'precio_unitario'))
                importe = _f(_pick(r, 'importe'))
                dto     = _f(_pick(r, 'dto'))
                try:
                    cant = int(float(str(_pick(r, 'cantidad') or 0).replace(',', '.')))
                except Exception:
                    cant = 0
                session.add(database.InvoiceItem(
                    factura_id=invoice_id,
                    codigo_barra=(_pick(r, 'codigo_barra') or None),
                    descripcion=desc, cantidad=cant,
                    precio_unitario=sign * precio if precio is not None else None,
                    dto=dto,
                    importe=sign * importe if importe is not None else None,
                    lote=(_pick(r, 'lote') or None),
                ))
                saved += 1

            try:
                if saved > 0:
                    invoice.total_articulos = saved
                session.commit()
                if saved > 0:
                    differences = compare_invoice_vs_erp(session, invoice_id)
                    save_differences(session, invoice_id, differences)
            except Exception as e:
                session.rollback()
                import traceback as _tb
                return jsonify({'error': str(e), 'trace': _tb.format_exc()}), 500
        return jsonify({'saved': saved, 'redirect': url_for('compare_view', invoice_id=invoice_id)})

    @app.route('/invoice/<int:invoice_id>/manual-items', methods=['GET', 'POST'])
    def manual_items(invoice_id):
        import pdfplumber as _plumber
        import json as _json

        with database.get_db() as session:
            invoice = session.get(database.Invoice, invoice_id)

            if request.method == 'POST':
                items_data = _json.loads(request.form.get('items_json', '[]'))
                tipo = invoice.tipo_comprobante or 'FAC'
                sign = -1 if tipo == 'NCR' else 1

                def _f(s):
                    if not s:
                        return None
                    try:
                        return float(str(s).replace('.', '').replace(',', '.'))
                    except Exception:
                        return None

                saved = 0
                for item in items_data:
                    desc = str(item.get('descripcion', '')).strip()
                    if not desc:
                        continue
                    precio  = _f(item.get('precio_unitario'))
                    importe = _f(item.get('importe'))
                    dto     = _f(item.get('dto'))
                    try:
                        cant = int(float(item.get('cantidad') or 0))
                    except Exception:
                        cant = 0
                    session.add(database.InvoiceItem(
                        factura_id=invoice_id,
                        codigo_barra=item.get('codigo_barra') or None,
                        descripcion=desc, cantidad=cant,
                        precio_unitario=sign * precio if precio is not None else None,
                        dto=dto,
                        importe=sign * importe if importe is not None else None,
                        lote=item.get('lote') or None,
                    ))
                    saved += 1

                if saved > 0:
                    invoice.total_articulos = saved
                    session.commit()
                    differences = compare_invoice_vs_erp(session, invoice_id)
                    save_differences(session, invoice_id, differences)
                    flash(f'{saved} artículos guardados manualmente.')
                    return redirect(url_for('compare_view', invoice_id=invoice_id))
                flash('No se ingresaron artículos.')
                return redirect(url_for('manual_items', invoice_id=invoice_id))

            # GET
            pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], invoice.pdf_filename or '')
            pdf_text = ''
            if os.path.exists(pdf_path):
                with _plumber.open(pdf_path) as pdf:
                    for page in pdf.pages:
                        pdf_text += (page.extract_text() or '') + '\n\n'
            return render_template('invoice_manual_items.html', invoice=invoice, pdf_text=pdf_text)

    @app.route('/api/invoice/<int:invoice_id>/differences', methods=['GET'])
    def invoice_differences(invoice_id):
        with database.get_db() as session:
            differences = session.query(database.StockDifference).filter_by(factura_id=invoice_id).all()
            return jsonify([
            {
                'id': d.id, 'codigo_barra': d.codigo_barra, 'descripcion': d.descripcion,
                'cantidad_factura': d.cantidad_factura, 'cantidad_erp': d.cantidad_erp,
                'diferencia': d.diferencia, 'observaciones': d.observaciones,
            }
            for d in differences
        ])

    @app.route('/invoice/<int:invoice_id>/header', methods=['POST'])
    def update_invoice_header(invoice_id):
        with database.get_db() as session:
            invoice = session.get(database.Invoice, invoice_id)
            if not invoice:
                flash('Factura no encontrada.')
                return redirect(url_for('index'))
            tipo = (request.form.get('tipo_comprobante') or '').upper()
            ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or (
                request.form.keys() and set(request.form.keys()) == {'tipo_comprobante'}
            )
            if tipo in ('FAC', 'NCR'):
                invoice.tipo_comprobante = tipo
            if 'numero_factura' in request.form:
                invoice.numero_factura = request.form.get('numero_factura', invoice.numero_factura).strip() or invoice.numero_factura
            if 'proveedor_razon' in request.form:
                invoice.proveedor_razon = request.form.get('proveedor_razon', invoice.proveedor_razon).strip() or invoice.proveedor_razon
            session.commit()
        if ajax:
            return jsonify({'ok': True})
        return redirect(url_for('show_results', invoice_id=invoice_id))

    @app.route('/results/<int:invoice_id>')
    def show_results(invoice_id):
        with database.get_db() as session:
            invoice = session.get(database.Invoice, invoice_id)
            saved_differences = get_saved_differences(session, invoice_id)
            differences = [
                {
                    'id': d.id, 'codigo_barra': d.codigo_barra, 'descripcion': d.descripcion,
                    'cantidad_factura': d.cantidad_factura, 'cantidad_erp': d.cantidad_erp,
                    'diferencia': d.diferencia, 'observaciones': d.observaciones,
                }
                for d in saved_differences
            ]
            total_unidades_calc = sum(
                item.cantidad for item in invoice.items if item.cantidad
            ) if invoice else 0
            return render_template('results.html', invoice=invoice, differences=differences,
                                   total_unidades_calc=total_unidades_calc)

    @app.route('/invoice/<int:invoice_id>/pick-fields', methods=['GET'])
    def pick_fields(invoice_id):
        with database.get_db() as session:
            invoice = session.get(database.Invoice, invoice_id)
        if not invoice:
            flash('Factura no encontrada.')
            return redirect(url_for('index'))

        pdf_text = ''
        if invoice.pdf_filename:
            pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], invoice.pdf_filename)
            if os.path.exists(pdf_path):
                import pdfplumber
                with pdfplumber.open(pdf_path) as pdf:
                    pdf_text = pdf.pages[0].extract_text() or ''

        return render_template('pick_fields.html', invoice=invoice, pdf_text=pdf_text)

    @app.route('/invoice/<int:invoice_id>/pick-fields', methods=['POST'])
    def pick_fields_save(invoice_id):
        with database.get_db() as session:
            invoice = session.get(database.Invoice, invoice_id)
            if not invoice:
                flash('Factura no encontrada.')
                return redirect(url_for('index'))
            fields = ('numero_factura', 'proveedor_razon', 'proveedor_cuit', 'fecha', 'total')
            for field in fields:
                val = request.form.get(field, '').strip()
                if val:
                    setattr(invoice, field, val)
            session.commit()
        return redirect(url_for('show_results', invoice_id=invoice_id))

    @app.route('/invoice/<int:invoice_id>/items')
    def invoice_items(invoice_id):
        with database.get_db() as session:
            invoice = session.get(database.Invoice, invoice_id)
            if not invoice:
                flash('Factura no encontrada.')
                return redirect(url_for('index'))
            items = session.query(database.InvoiceItem).filter_by(factura_id=invoice_id).all()

            # Build barcode → {monodroga, presentacion} map from Producto (incl. EANs alt)
            barcodes = [it.codigo_barra for it in items if it.codigo_barra]
            prod_info = {}
            if barcodes:
                from sqlalchemy import or_
                prods = session.query(database.Producto).filter(or_(
                    database.Producto.codigo_barra.in_(barcodes),
                    database.Producto.codigo_barra_alt1.in_(barcodes),
                    database.Producto.codigo_barra_alt2.in_(barcodes),
                    database.Producto.codigo_barra_alt3.in_(barcodes),
                )).all()
                for p in prods:
                    info = {'monodroga': p.monodroga or '', 'presentacion': p.presentacion or ''}
                    for bc in [p.codigo_barra, p.codigo_barra_alt1,
                               p.codigo_barra_alt2, p.codigo_barra_alt3]:
                        if bc:
                            prod_info[bc] = info
            return render_template('invoice_items.html', invoice=invoice,
                                   items=items, prod_info=prod_info)

    @app.route('/invoice/<int:invoice_id>/items/export')
    def invoice_items_export(invoice_id):
        import io
        import openpyxl
        with database.get_db() as session:
            invoice = session.get(database.Invoice, invoice_id)
            items = session.query(database.InvoiceItem).filter_by(factura_id=invoice_id).all()
        if not invoice:
            return 'Factura no encontrada', 404

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'Ítems'
        headers = ['Código de barra', 'Descripción', 'Cantidad', 'Precio unitario',
                   'Dto %', 'Importe', 'Lote', 'Vencimiento']
        ws.append(headers)
        for it in items:
            ws.append([it.codigo_barra, it.descripcion, it.cantidad,
                       float(it.precio_unitario or 0), float(it.dto or 0),
                       float(it.importe or 0), it.lote, it.vencimiento])

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        fname = f'items_{invoice.numero_factura}.xlsx'
        resp = make_response(buf.read())
        resp.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        resp.headers['Content-Disposition'] = f'attachment; filename="{fname}"'
        return resp

    @app.route('/invoice/<int:invoice_id>/differences/export')
    def invoice_differences_export(invoice_id):
        import io
        import openpyxl
        with database.get_db() as session:
            invoice = session.get(database.Invoice, invoice_id)
            diffs = session.query(database.StockDifference).filter_by(factura_id=invoice_id).all()
        if not invoice:
            return 'Factura no encontrada', 404

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'Diferencias'
        ws.append(['Código de barra', 'Descripción', 'Cant. factura', 'Cant. ERP',
                   'Diferencia', 'Observaciones'])
        for d in diffs:
            ws.append([d.codigo_barra, d.descripcion, d.cantidad_factura,
                       d.cantidad_erp, d.diferencia, d.observaciones])

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        fname = f'diferencias_{invoice.numero_factura}.xlsx'
        resp = make_response(buf.read())
        resp.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        resp.headers['Content-Disposition'] = f'attachment; filename="{fname}"'
        return resp

    @app.route('/invoice/<int:invoice_id>/compare')
    def compare_view(invoice_id):
        with database.get_db() as session:
            invoice = session.get(database.Invoice, invoice_id)
            if not invoice:
                flash('Factura no encontrada.')
                return redirect(url_for('index'))
            invoice_diffs = (session.query(database.StockDifference)
                             .filter_by(factura_id=invoice_id)
                             .order_by(database.StockDifference.descripcion).all())
            erp_items = get_erp_items_with_issues(session, invoice_id)
            inv_items = session.query(database.InvoiceItem).filter_by(factura_id=invoice_id).all()
            inv_prices = {
                item.codigo_barra: float(item.precio_unitario or 0)
                for item in inv_items if item.codigo_barra
            }
            return render_template('compare.html', invoice=invoice,
                                   invoice_diffs=invoice_diffs, erp_items=erp_items,
                                   inv_prices=inv_prices)

    @app.route('/invoice/<int:invoice_id>/apply-mapping', methods=['POST'])
    def apply_mapping(invoice_id):
        with database.get_db() as session:
            invoice = session.get(database.Invoice, invoice_id)
            diffs = (session.query(database.StockDifference)
                     .filter_by(factura_id=invoice_id)
                     .order_by(database.StockDifference.descripcion).all())

            proveedor_id = None
            if invoice and invoice.proveedor_cuit:
                prov = session.query(database.Provider).filter_by(cuit=invoice.proveedor_cuit).first()
                if prov:
                    proveedor_id = prov.id
            if proveedor_id is None and invoice and invoice.proveedor_razon:
                prov = session.query(database.Provider).filter_by(razon_social=invoice.proveedor_razon).first()
                if prov:
                    proveedor_id = prov.id

            to_delete = []
            for key, value in request.form.items():
                if not key.startswith('mapping_') or not value.strip():
                    continue
                try:
                    erp_id = int(key.replace('mapping_', ''))
                    inv_num = int(value.strip())
                except ValueError:
                    continue
                if inv_num < 1 or inv_num > len(diffs):
                    continue

                target_diff = diffs[inv_num - 1]
                erp_item = session.get(database.ErpStock, erp_id)
                if not erp_item:
                    continue

                if proveedor_id and target_diff.codigo_barra and erp_item.codigo_barra:
                    save_barcode_mapping(
                        session,
                        proveedor_id=proveedor_id,
                        codigo_barra_factura=target_diff.codigo_barra,
                        codigo_barra_erp=erp_item.codigo_barra,
                        descripcion_factura=target_diff.descripcion,
                        descripcion_erp=erp_item.descripcion,
                    )
                    _upsert_producto(session, erp_item.codigo_barra, erp_item.descripcion,
                                     fecha_compra=invoice.fecha if invoice else None)
                    _add_alt_barcode(session, erp_item.codigo_barra, target_diff.codigo_barra)

                target_diff.cantidad_erp = erp_item.cantidad
                target_diff.diferencia = target_diff.cantidad_factura - erp_item.cantidad
                target_diff.observaciones = (
                    f'Cruce manual con ERP: {erp_item.descripcion} ({erp_item.codigo_barra})'
                )
                if target_diff.diferencia == 0:
                    to_delete.append(target_diff)

            for diff in to_delete:
                session.delete(diff)

            session.commit()
        return redirect(url_for('show_results', invoice_id=invoice_id))
