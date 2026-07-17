"""Provider routes: CRUD, parser-preview, mappings, peek, API invoices."""

import os

from flask import flash, jsonify, make_response, redirect, render_template, request, url_for
from flask_login import login_required
from werkzeug.utils import secure_filename

import database
from data_extract import extract_provider_name_from_pdf, parse_invoice_pdf
from database import Producto
from helpers import (
    UPLOAD_FOLDER,
    _ensure_parser_file,
    _make_parser_slug,
    allowed_file,
    drogueria_defaults,
    get_providers,
    pdf_de_carpeta_proveedor,
)


def init_app(app):

    @app.route('/api/proveedores/drog-activas')
    @login_required
    def api_drog_activas():
        """Droguerías que el operador activó para aparecer en el dropdown
        'Pedido a' (modal Cerrar TX en /atencion y /pedido/nuevo).
        Solo id + nombre, ordenadas alfabéticamente."""
        with database.get_db() as session:
            rows = (session.query(database.Provider.id,
                                  database.Provider.razon_social)
                    .filter(database.Provider.tipo == 'drogueria',
                            database.Provider.activo.is_(True),
                            database.Provider.activa_ped.is_(True))
                    .order_by(database.Provider.razon_social).all())
            return jsonify({'drog': [{'id': r[0], 'nombre': r[1]} for r in rows]})

    @app.route('/api/provider/<int:provider_id>/activa-ped', methods=['PATCH'])
    @login_required
    def api_provider_activa_ped(provider_id):
        """Toggle del flag activa_ped. Body JSON {activa: true|false}."""
        data = request.get_json(silent=True) or {}
        activa = bool(data.get('activa'))
        with database.get_db() as session:
            prov = session.get(database.Provider, provider_id)
            if not prov:
                return jsonify({'ok': False, 'error': 'Proveedor no encontrado'}), 404
            prov.activa_ped = activa
            session.commit()
        return jsonify({'ok': True, 'activa_ped': activa})

    @app.route('/api/provider/<int:provider_id>/descuento-sin-transfer', methods=['PATCH'])
    @login_required
    def api_provider_descuento_sin_transfer(provider_id):
        """Edita Provider.descuento_sin_transfer. Body JSON {descuento_base: 0..100|null}."""
        data = request.get_json(silent=True) or {}
        raw = data.get('descuento_base')
        dto = None
        if raw not in (None, ''):
            try:
                dto = float(raw)
                if dto < 0 or dto > 100:
                    return jsonify({'ok': False, 'error': 'Debe estar entre 0 y 100'}), 400
            except (TypeError, ValueError):
                return jsonify({'ok': False, 'error': 'Valor inválido'}), 400
        with database.get_db() as session:
            prov = session.get(database.Provider, provider_id)
            if not prov:
                return jsonify({'ok': False, 'error': 'Proveedor no encontrado'}), 404
            prov.descuento_sin_transfer = dto
            session.commit()
        return jsonify({'ok': True, 'descuento_base': dto})

    @app.route('/api/provider/<int:provider_id>/invoices')
    def api_provider_invoices(provider_id):
        with database.get_db() as session:
            provider = session.get(database.Provider, provider_id)
            if not provider:
                return jsonify([])
            invoices = (session.query(database.Invoice)
                        .filter(
                            (database.Invoice.proveedor_cuit == provider.cuit) |
                            (database.Invoice.proveedor_razon == provider.razon_social)
                        )
                        .order_by(database.Invoice.fecha.desc())
                        .limit(50).all())
            result = []
            for inv in invoices:
                result.append({
                    'id': inv.id,
                    'numero_factura': inv.numero_factura,
                    'fecha': inv.fecha.strftime('%d/%m/%Y') if inv.fecha else '—',
                    'tipo_comprobante': inv.tipo_comprobante,
                    'total_articulos': inv.total_articulos or 0,
                    'total': float(inv.total or 0),
                })
        return jsonify(result)

    @app.route('/provider/peek', methods=['POST'])
    def provider_peek():
        """Recibe el PDF, lee el encabezado y devuelve nombre propuesto + provider_id si ya existe."""
        pdf_file = request.files.get('invoice_pdf')
        if not pdf_file or not allowed_file(pdf_file.filename):
            return jsonify({'error': 'Archivo PDF inválido.'}), 400

        filename = secure_filename(pdf_file.filename)
        path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        pdf_file.save(path)

        proposed_name = extract_provider_name_from_pdf(path)

        provider_id = None
        if proposed_name:
            with database.get_db() as session:
                existing = session.query(database.Provider).filter(
                    database.Provider.razon_social.ilike(f'%{proposed_name}%')
                ).first()
                if existing and existing.parser_file:
                    provider_id = existing.id

        return jsonify({'proposed_name': proposed_name, 'pdf_filename': filename,
                        'provider_id': provider_id})

    @app.route('/api/invoice/probe-create', methods=['POST'])
    def invoice_probe_create():
        """Crea una factura mínima (sin ítems, sin ERP) para abrir el asistente de parsing."""
        data = request.get_json(silent=True) or {}
        provider_id = data.get('provider_id')
        pdf_filename = data.get('pdf_filename', '').strip()
        if not pdf_filename:
            return jsonify({'error': 'pdf_filename requerido'}), 400

        pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], pdf_filename)
        if not os.path.exists(pdf_path):
            return jsonify({'error': 'PDF no encontrado en el servidor'}), 404

        parser_file = None
        if provider_id:
            with database.get_db() as _s:
                prov = _s.get(database.Provider, int(provider_id))
                if prov:
                    parser_file = prov.parser_file

        from data_extract import save_invoice_to_db
        invoice_data = {
            'numero_factura': 'SIN_NUMERO', 'fecha': __import__('datetime').date.today(),
            'proveedor_razon': 'NUEVO PROVEEDOR', 'proveedor_cuit': None,
            'proveedor_domicilio': None, 'total': 0.0, 'total_articulos': 0, 'items': []
        }
        if parser_file:
            try:
                parsed = parse_invoice_pdf(pdf_path, parser_file)
                invoice_data = {**parsed, 'items': []}
            except Exception as e:
                # Si el parser falla, seguimos con el invoice_data inicial
                # (sin items). El user va a tener que cargar los datos a mano.
                app.logger.warning('Parser %s falló sobre %s: %s',
                                   parser_file, pdf_path, e)

        with database.get_db() as _s:
            try:
                inv = save_invoice_to_db(_s, invoice_data,
                                         pdf_filename=pdf_filename, tipo_comprobante='FAC')
                _s.commit()
                inv_id = inv.id
            except Exception as e:
                _s.rollback()
                return jsonify({'error': str(e)}), 500
        return jsonify({'invoice_id': inv_id})

    @app.route('/provider/create-from-peek', methods=['POST'])
    def provider_create_from_peek():
        """Crea o recupera un proveedor desde el flujo peek/batch."""
        data = request.get_json(silent=True) or {}
        name = (data.get('provider_name') or '').strip()
        peek_id = data.get('peek_provider_id')
        if not name:
            return jsonify({'error': 'Nombre requerido.'}), 400

        with database.get_db() as session:
            try:
                if peek_id:
                    prov = session.get(database.Provider, int(peek_id))
                    if prov:
                        return jsonify({'provider_id': prov.id})

                from helpers import _normalizar_nombre_entidad, get_or_create_proveedor
                # Match por nombre normalizado profundo (no solo ilike '%X%')
                norm_buscado = _normalizar_nombre_entidad(name)
                if norm_buscado:
                    for c in session.query(database.Provider).all():
                        if _normalizar_nombre_entidad(c.razon_social) == norm_buscado:
                            return jsonify({'provider_id': c.id})
                prov = get_or_create_proveedor(session, name)
                session.commit()
                if prov:
                    session.refresh(prov)
                return jsonify({'provider_id': prov.id if prov else None})
            except Exception as e:
                session.rollback()
                return jsonify({'error': str(e)}), 500

    @app.route('/provider/<int:provider_id>/parser-preview/export', methods=['POST'])
    def provider_parser_preview_export(provider_id):
        """Recibe JSON con datos del preview y devuelve XLS."""
        import io

        import openpyxl
        data = request.get_json(silent=True) or {}
        items = data.get('items', [])

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'Parser preview'
        ws.append(['Código de barra', 'Descripción', 'Cantidad', 'Precio unitario',
                   'Dto %', 'Importe', 'Lote', 'Vencimiento'])
        for it in items:
            ws.append([it.get('codigo_barra'), it.get('descripcion'), it.get('cantidad'),
                       it.get('precio_unitario'), it.get('dto'), it.get('importe'),
                       it.get('lote'), it.get('vencimiento')])

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        resp = make_response(buf.read())
        resp.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        resp.headers['Content-Disposition'] = f'attachment; filename="parser_preview_{provider_id}.xlsx"'
        return resp

    @app.route('/api/provider/<int:provider_id>/folder-file/stage', methods=['POST'])
    def provider_folder_file_stage(provider_id):
        """Copia a uploads un PDF elegido de la carpeta del proveedor y devuelve su nombre.

        Es lo que le faltaba a "Probar parser" cuando el PDF se elige de la carpeta:
        el preview (parser-preview-saved) sólo mira uploads, así que el botón quedaba
        habilitado y no hacía nada.
        """
        import shutil
        data = request.get_json(silent=True) or {}
        nombre = (data.get('name') or '').strip()
        with database.get_db() as session:
            prov = session.get(database.Provider, provider_id)
            if not prov:
                return jsonify({'error': 'Proveedor no encontrado.'}), 404
            ruta = (prov.ruta_facturas or '').strip()
        src = pdf_de_carpeta_proveedor(ruta, nombre)
        if not src:
            return jsonify({'error': 'El PDF elegido no existe en la carpeta del proveedor.'}), 400
        dest_name = secure_filename(nombre) or 'factura.pdf'
        try:
            shutil.copy2(src, os.path.join(UPLOAD_FOLDER, dest_name))
        except OSError as e:
            return jsonify({'error': f'No se pudo copiar el PDF: {e}'}), 400
        return jsonify({'pdf_filename': dest_name})

    @app.route('/provider/<int:provider_id>/parser-preview-saved', methods=['POST'])
    def provider_parser_preview_saved(provider_id):
        """Preview del parser usando un PDF ya guardado en uploads."""
        data = request.get_json(silent=True) or {}
        pdf_filename = data.get('pdf_filename', '').strip()
        if not pdf_filename:
            return jsonify({'error': 'Falta pdf_filename.'}), 400

        with database.get_db() as session:
            provider = session.get(database.Provider, provider_id)
        if not provider or not provider.parser_file:
            return jsonify({'error': 'El proveedor no tiene parser configurado.'}), 400

        path = os.path.join(UPLOAD_FOLDER, secure_filename(pdf_filename))
        if not os.path.exists(path):
            return jsonify({'error': 'El archivo PDF ya no está disponible. Volvé a cargarlo.'}), 404

        try:
            result = parse_invoice_pdf(path, provider.parser_file)
        except Exception as e:
            return jsonify({'error': f'Error en el parser: {e}'}), 500

        items = [{'codigo_barra': it.get('codigo_barra') or '',
                  'descripcion': it.get('descripcion') or '',
                  'cantidad': it.get('cantidad') or '',
                  'precio_unitario': it.get('precio_unitario') or '',
                  'dto': it.get('dto') or '',
                  'importe': it.get('importe') or '',
                  'lote': it.get('lote') or '',
                  'vencimiento': it.get('vencimiento') or ''}
                 for it in (result.get('items') or [])]

        return jsonify({'numero_factura': result.get('numero_factura'),
                        'fecha': str(result.get('fecha') or ''),
                        'proveedor': result.get('proveedor_razon'),
                        'total': str(result.get('total') or ''),
                        'items': items})

    @app.route('/providers')
    def providers_list():
        tipo_filter = (request.args.get('tipo') or '').strip().lower()
        with database.get_db() as session:
            q = session.query(database.Provider).filter(database.Provider.activo == True)
            if tipo_filter in ('drogueria', 'laboratorio', 'otro'):
                q = q.filter(database.Provider.tipo == tipo_filter)
            providers = q.order_by(database.Provider.razon_social).all()
            plantilla_ids = {pid for (pid,) in session.query(database.PlantillaExportacion.proveedor_id).all()}
            provider_data = []
            for p in providers:
                q = session.query(database.Invoice)
                if p.cuit:
                    q = q.filter(
                        (database.Invoice.proveedor_cuit == p.cuit) |
                        (database.Invoice.proveedor_razon == p.razon_social)
                    )
                else:
                    q = q.filter(database.Invoice.proveedor_razon == p.razon_social)
                invoice_count = q.count()
                claim_count = session.query(database.Claim).filter_by(proveedor_id=p.id).count()
                horario_count = (session.query(database.ProveedorHorarioReparto)
                                 .filter_by(proveedor_id=p.id).count())
                _dd = drogueria_defaults(p.razon_social) if (p.tipo or 'drogueria') == 'drogueria' else {}
                provider_data.append({
                    'id': p.id,
                    'razon_social': p.razon_social,
                    'cuit': p.cuit or '',
                    'parser_file': p.parser_file or '',
                    'ruta_facturas': p.ruta_facturas or '',
                    'match_strategy': p.match_strategy,
                    'grabar_productos': p.grabar_productos if p.grabar_productos is not None else 1,
                    'tipo': p.tipo or 'drogueria',
                    'invoice_count': invoice_count,
                    'claim_count': claim_count,
                    'has_plantilla': p.id in plantilla_ids,
                    'tiene_horarios': horario_count > 0,
                    'horario_count': horario_count,
                    'matriz_visible': p.matriz_visible if p.tipo == 'drogueria' else None,
                    'codcli': p.codcli or '',
                    # formato/sufijo: lo guardado o el default por nombre de droguería.
                    'formato_archivo': p.formato_archivo or _dd.get('formato_archivo', ''),
                    'sufijo': p.sufijo or _dd.get('sufijo', ''),
                    'carpeta_filtro': p.carpeta_filtro or '',
                })
        return render_template('providers.html', providers=provider_data, tipo_filter=tipo_filter)

    @app.route('/provider/<int:provider_id>/parser-preview', methods=['POST'])
    def provider_parser_preview(provider_id):
        with database.get_db() as session:
            provider = session.get(database.Provider, provider_id)
        if not provider or not provider.parser_file:
            return {'error': 'El proveedor no tiene parser configurado.'}, 400

        f = request.files.get('pdf')
        if not f or not f.filename.lower().endswith('.pdf'):
            return {'error': 'Seleccioná un archivo PDF.'}, 400

        tmp_path = os.path.join(UPLOAD_FOLDER, f'preview_{secure_filename(f.filename)}')
        f.save(tmp_path)
        try:
            data = parse_invoice_pdf(tmp_path, provider.parser_file)
        except Exception as e:
            return {'error': f'Error en el parser: {e}'}, 500
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                # Cleanup best-effort. El SO va a limpiarlo igual al reiniciar.
                pass

        items = []
        for it in (data.get('items') or []):
            items.append({
                'codigo_barra': it.get('codigo_barra') or '',
                'descripcion': it.get('descripcion') or '',
                'cantidad': it.get('cantidad') or '',
                'precio_unitario': it.get('precio_unitario') or '',
                'dto': it.get('dto') or '',
                'importe': it.get('importe') or '',
                'lote': it.get('lote') or '',
                'vencimiento': it.get('vencimiento') or '',
            })
        return {
            'parser': provider.parser_file,
            'numero_factura': data.get('numero_factura') or '',
            'fecha': str(data.get('fecha') or ''),
            'proveedor': data.get('proveedor_razon') or '',
            'total': str(data.get('total') or ''),
            'items': items,
        }

    @app.route('/provider/create', methods=['POST'])
    def provider_create_manual():
        """Crea un Provider (drogueria/laboratorio/otro) desde el form de
        /providers. Si ya existe uno con razon_social normalizada igual,
        no duplica."""
        razon_social = (request.form.get('razon_social') or '').strip()
        cuit = (request.form.get('cuit') or '').strip()
        tipo = (request.form.get('tipo') or 'drogueria').strip()
        if tipo not in ('drogueria', 'laboratorio', 'otro'):
            tipo = 'drogueria'
        if not razon_social:
            flash('Razón social obligatoria.')
            return redirect(url_for('providers_list'))
        with database.get_db() as session:
            from helpers import _normalizar_nombre_entidad
            norm_nuevo = _normalizar_nombre_entidad(razon_social)
            for p in session.query(database.Provider).all():
                if _normalizar_nombre_entidad(p.razon_social) == norm_nuevo:
                    flash(f'Ya existe el proveedor: "{p.razon_social}". No se creó duplicado.')
                    return redirect(url_for('providers_list', tipo=tipo))
            prov = database.Provider(
                razon_social=razon_social,
                cuit=cuit or None,
                tipo=tipo,
                activo=True,
                matriz_visible=(tipo == 'drogueria'),
                match_strategy='barcode',
            )
            session.add(prov)
            session.commit()
            flash(f'Creado: {prov.razon_social} ({tipo}).')
        return redirect(url_for('providers_list', tipo=tipo))

    @app.route('/provider/<int:provider_id>/edit', methods=['POST'])
    def provider_edit(provider_id):
        with database.get_db() as session:
            provider = session.get(database.Provider, provider_id)
            if not provider:
                flash('Proveedor no encontrado.')
                return redirect(url_for('providers_list'))
            provider.razon_social = request.form.get('razon_social', provider.razon_social).strip() or provider.razon_social
            provider.cuit = request.form.get('cuit', provider.cuit or '').strip() or None
            provider.parser_file = request.form.get('parser_file', provider.parser_file or '').strip() or None
            provider.ruta_facturas = request.form.get('ruta_facturas', '').strip() or None
            ms = request.form.get('match_strategy', 'barcode')
            provider.match_strategy = ms if ms in ('barcode', 'descripcion') else 'barcode'
            provider.grabar_productos = 1 if request.form.get('grabar_productos') == '1' else 0
            tipo = (request.form.get('tipo') or '').strip().lower()
            if tipo in ('drogueria', 'laboratorio', 'otro'):
                provider.tipo = tipo
            for field in ('descuento_con_transfer', 'descuento_sin_transfer'):
                raw = (request.form.get(field) or '').replace(',', '.').strip()
                try:
                    setattr(provider, field, float(raw) if raw else None)
                except ValueError:
                    pass
            # Filtro droguería: config del archivo de pedido.
            provider.codcli = (request.form.get('codcli') or '').strip() or None
            fmt = (request.form.get('formato_archivo') or '').strip().lower()
            provider.formato_archivo = fmt if fmt in ('ped', 'txt20j') else None
            provider.sufijo = (request.form.get('sufijo') or '').strip() or None
            provider.carpeta_filtro = (request.form.get('carpeta_filtro') or '').strip() or None
            session.commit()
        return redirect(url_for('providers_list', tipo=request.form.get('tipo_filter') or None))

    @app.route('/provider/<int:provider_id>/delete', methods=['POST'])
    def provider_delete(provider_id):
        with database.get_db() as session:
            provider = session.get(database.Provider, provider_id)
            if provider:
                claim_ids = [c.id for c in session.query(database.Claim).filter_by(proveedor_id=provider_id).all()]
                if claim_ids:
                    session.query(database.ClaimItem).filter(
                        database.ClaimItem.reclamo_id.in_(claim_ids)
                    ).delete(synchronize_session=False)
                session.query(database.Claim).filter_by(proveedor_id=provider_id).delete()
                session.query(database.BarcodeMapping).filter_by(proveedor_id=provider_id).delete()
                batch_ids = [b.id for b in session.query(database.InvoiceBatch).filter_by(proveedor_id=provider_id).all()]
                if batch_ids:
                    session.query(database.Invoice).filter(
                        database.Invoice.batch_id.in_(batch_ids)
                    ).update({'batch_id': None}, synchronize_session=False)
                    session.query(database.InvoiceBatch).filter(
                        database.InvoiceBatch.id.in_(batch_ids)
                    ).delete(synchronize_session=False)
                session.delete(provider)
                session.commit()
        return redirect(url_for('providers_list'))

    @app.route('/provider/<int:provider_id>/invoices')
    def provider_invoices(provider_id):
        with database.get_db() as session:
            provider = session.get(database.Provider, provider_id)
            if not provider:
                flash('Proveedor no encontrado.')
                return redirect(url_for('providers_list'))
            invoices = session.query(database.Invoice).filter(
                (database.Invoice.proveedor_cuit == provider.cuit) |
                (database.Invoice.proveedor_razon == provider.razon_social)
            ).order_by(database.Invoice.fecha.desc()).all()
            return render_template('provider_invoices.html', provider=provider, invoices=invoices)

    @app.route('/invoice/<int:invoice_id>/delete', methods=['POST'])
    def delete_invoice(invoice_id):
        with database.get_db() as session:
            invoice = session.get(database.Invoice, invoice_id)
            if not invoice:
                flash('Factura no encontrada.')
                return redirect(url_for('providers_list'))

            provider = None
            if invoice.proveedor_cuit:
                provider = session.query(database.Provider).filter_by(cuit=invoice.proveedor_cuit).first()
            if not provider and invoice.proveedor_razon:
                provider = session.query(database.Provider).filter_by(razon_social=invoice.proveedor_razon).first()
            provider_id = provider.id if provider else None

            diff_ids = [d.id for d in session.query(database.StockDifference).filter_by(factura_id=invoice_id).all()]
            if diff_ids:
                session.query(database.ClaimItem).filter(
                    database.ClaimItem.diferencia_id.in_(diff_ids)
                ).delete(synchronize_session=False)
            session.query(database.StockDifference).filter_by(factura_id=invoice_id).delete()
            session.query(database.ClaimItem).filter(
                database.ClaimItem.reclamo_id.in_(
                    session.query(database.Claim.id).filter_by(factura_id=invoice_id)
                )
            ).delete(synchronize_session=False)
            session.query(database.Claim).filter_by(factura_id=invoice_id).delete()
            session.query(database.InvoiceItem).filter_by(factura_id=invoice_id).delete()
            session.delete(invoice)
            session.commit()

        if provider_id:
            return redirect(url_for('provider_invoices', provider_id=provider_id))
        return redirect(url_for('providers_list'))

    @app.route('/provider/<int:provider_id>/mappings')
    def provider_mappings(provider_id):
        with database.get_db() as session:
            provider = session.get(database.Provider, provider_id)
            if not provider:
                flash('Proveedor no encontrado.')
                return redirect(url_for('providers_list'))
            mappings = (session.query(database.BarcodeMapping)
                        .filter_by(proveedor_id=provider_id)
                        .order_by(database.BarcodeMapping.creado_en.desc()).all())
            return render_template('provider_mappings.html', provider=provider, mappings=mappings)

    @app.route('/provider/<int:provider_id>/mappings/<int:mapping_id>/delete', methods=['POST'])
    def delete_mapping(provider_id, mapping_id):
        with database.get_db() as session:
            mapping = session.get(database.BarcodeMapping, mapping_id)
            if mapping and mapping.proveedor_id == provider_id:
                session.delete(mapping)
                session.commit()
        return redirect(url_for('provider_mappings', provider_id=provider_id))

    @app.route('/providers/activos', methods=['GET', 'POST'])
    def providers_activos():
        """Pantalla admin para activar/desactivar proveedores en bulk."""
        with database.get_db() as session:
            if request.method == 'POST':
                activos_ids = set(int(x) for x in request.form.getlist('activo_ids') if x.isdigit())
                todos = session.query(database.Provider).all()
                cambios = 0
                for prov in todos:
                    nuevo = prov.id in activos_ids
                    if prov.activo != nuevo:
                        prov.activo = nuevo
                        cambios += 1
                session.commit()
                flash(f'{cambios} proveedor(es) actualizado(s).')
                return redirect(url_for('providers_activos'))

            provs = session.query(database.Provider).order_by(database.Provider.razon_social).all()
            n_activos = sum(1 for p in provs if p.activo)
            data = [{
                'id': p.id, 'razon_social': p.razon_social,
                'cuit': p.cuit or '', 'tipo': p.tipo or 'drogueria',
                'activo': bool(p.activo),
            } for p in provs]
        return render_template('providers_activos.html',
                               providers=data, n_total=len(data), n_activos=n_activos)

    @app.route('/provider/<int:provider_id>/mappings/delete-all', methods=['POST'])
    def delete_all_mappings(provider_id):
        with database.get_db() as session:
            session.query(database.BarcodeMapping).filter_by(proveedor_id=provider_id).delete()
            session.commit()
        flash('Todas las equivalencias fueron eliminadas.')
        return redirect(url_for('provider_mappings', provider_id=provider_id))

    # ── Plantillas de exportación por proveedor ──────────────────────────────

    @app.route('/api/provider/<int:provider_id>/folder-files', methods=['GET'])
    def provider_folder_files(provider_id):
        """Lista PDFs en la ruta_facturas configurada del proveedor."""
        with database.get_db() as session:
            prov = session.get(database.Provider, provider_id)
            if not prov:
                return jsonify({'error': 'Proveedor no encontrado.'}), 404
            ruta = (prov.ruta_facturas or '').strip()
            if not ruta:
                return jsonify({'error': 'El proveedor no tiene carpeta configurada.'}), 400
            if not os.path.isdir(ruta):
                return jsonify({'error': f'La carpeta no existe o no es accesible: {ruta}'}), 400
        try:
            files = []
            for name in os.listdir(ruta):
                if not name.lower().endswith('.pdf'):
                    continue
                full = os.path.join(ruta, name)
                try:
                    st = os.stat(full)
                    files.append({'name': name, 'size': st.st_size, 'mtime': int(st.st_mtime)})
                except OSError:
                    pass
            files.sort(key=lambda f: f['mtime'], reverse=True)
            return jsonify({'files': files, 'ruta': ruta})
        except OSError as e:
            return jsonify({'error': f'Error al leer carpeta: {e}'}), 500

    @app.route('/provider/<int:provider_id>/plantilla', methods=['GET', 'POST'])
    def provider_plantilla(provider_id):
        with database.get_db() as session:
            provider = session.get(database.Provider, provider_id)
            if not provider:
                flash('Proveedor no encontrado.')
                return redirect(url_for('providers_list'))

            if request.method == 'POST':
                action = request.form.get('action')

                if action == 'save_plantilla':
                    p = session.query(database.PlantillaExportacion).filter_by(proveedor_id=provider_id).first()
                    if not p:
                        p = database.PlantillaExportacion(proveedor_id=provider_id)
                        session.add(p)
                    p.nombre    = request.form.get('nombre', 'Plantilla').strip()
                    p.extension = request.form.get('extension', 'txt').strip().lstrip('.')
                    session.commit()

                elif action == 'add_campo':
                    p = session.query(database.PlantillaExportacion).filter_by(proveedor_id=provider_id).first()
                    if not p:
                        p = database.PlantillaExportacion(proveedor_id=provider_id, nombre='Plantilla')
                        session.add(p)
                        session.flush()
                    campo = database.PlantillaCampo(
                        plantilla_id  = p.id,
                        nombre        = request.form.get('campo_nombre', '').strip(),
                        campo_sistema = request.form.get('campo_sistema', 'espacio'),
                        col_inicio    = int(request.form.get('col_inicio', 0)),
                        longitud      = int(request.form.get('longitud', 1)),
                        valor_fijo    = request.form.get('valor_fijo', '').strip() or None,
                        alineacion    = request.form.get('alineacion', 'L'),
                        relleno       = (request.form.get('relleno', ' ') or ' ')[0],
                    )
                    session.add(campo)
                    session.commit()

                elif action == 'delete_campo':
                    campo_id = int(request.form.get('campo_id', 0))
                    c = session.get(database.PlantillaCampo, campo_id)
                    if c:
                        session.delete(c)
                        session.commit()

                elif action == 'delete_plantilla':
                    p = session.query(database.PlantillaExportacion).filter_by(proveedor_id=provider_id).first()
                    if p:
                        session.delete(p)
                        session.commit()

                return redirect(url_for('provider_plantilla', provider_id=provider_id))

            plantilla = session.query(database.PlantillaExportacion).filter_by(proveedor_id=provider_id).first()
            max_line_len = 0
            if plantilla and plantilla.campos:
                max_line_len = max((c.col_inicio + c.longitud) for c in plantilla.campos)
            return render_template('provider_plantilla.html',
                                   provider=provider,
                                   plantilla=plantilla,
                                   campos_sistema=database.CAMPOS_SISTEMA,
                                   max_line_len=max_line_len)

    # -----------------------------------------------------------------------
    # Horarios de reparto
    # -----------------------------------------------------------------------

    DIAS = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']

    def _proximo_cierre(session, proveedor_id):
        """Devuelve el próximo datetime de cierre de la droguería, o None si no tiene horarios."""
        from datetime import datetime, timedelta

        from database import ProveedorHorarioReparto
        horarios = (session.query(ProveedorHorarioReparto)
                    .filter_by(proveedor_id=proveedor_id, activo=True)
                    .order_by(ProveedorHorarioReparto.dia_semana, ProveedorHorarioReparto.hora).all())
        if not horarios:
            return None
        ahora = datetime.now()
        dia_hoy = ahora.weekday()  # 0=lun
        # Probar los próximos 7 días desde hoy
        for delta in range(8):
            dia = (dia_hoy + delta) % 7
            slots_dia = [h for h in horarios if h.dia_semana == dia]
            for slot in sorted(slots_dia, key=lambda h: h.hora):
                hh, mm = map(int, slot.hora.split(':'))
                candidate = ahora.replace(hour=hh, minute=mm, second=0, microsecond=0)
                if delta > 0:
                    candidate += timedelta(days=delta)
                elif delta == 0 and candidate <= ahora:
                    continue  # ya pasó hoy
                return candidate
        return None

    @app.route('/provider/<int:provider_id>/horarios', methods=['GET', 'POST'])
    @login_required
    def provider_horarios(provider_id):
        with database.get_db() as session:
            provider = session.get(database.Provider, provider_id)
            if not provider:
                flash('Proveedor no encontrado.')
                return redirect(url_for('providers_list'))

            if request.method == 'POST':
                action = request.form.get('action')
                if action == 'add':
                    dia = int(request.form.get('dia_semana', 0))
                    hora = request.form.get('hora', '').strip()
                    if hora and 0 <= dia <= 6:
                        try:
                            existing = (session.query(database.ProveedorHorarioReparto)
                                        .filter_by(proveedor_id=provider_id, dia_semana=dia, hora=hora)
                                        .first())
                            if not existing:
                                session.add(database.ProveedorHorarioReparto(
                                    proveedor_id=provider_id, dia_semana=dia, hora=hora))
                                session.commit()
                        except Exception:
                            session.rollback()
                elif action == 'delete':
                    horario_id = int(request.form.get('horario_id', 0))
                    h = session.get(database.ProveedorHorarioReparto, horario_id)
                    if h and h.proveedor_id == provider_id:
                        session.delete(h)
                        session.commit()
                return redirect(url_for('provider_horarios', provider_id=provider_id))

            horarios = (session.query(database.ProveedorHorarioReparto)
                        .filter_by(proveedor_id=provider_id)
                        .order_by(database.ProveedorHorarioReparto.dia_semana,
                                  database.ProveedorHorarioReparto.hora).all())
            # Grilla dia x hora para el template
            grilla = {d: [] for d in range(7)}
            for h in horarios:
                grilla[h.dia_semana].append(h)

            return render_template('provider_horarios.html',
                                   provider=provider,
                                   horarios=horarios,
                                   grilla=grilla,
                                   dias=DIAS)

    @app.route('/api/proveedor/<int:provider_id>/proximo-cierre')
    @login_required
    def api_proximo_cierre(provider_id):
        with database.get_db() as session:
            provider = session.get(database.Provider, provider_id)
            if not provider:
                return jsonify({'ok': False, 'error': 'no encontrado'}), 404
            proximo = _proximo_cierre(session, provider_id)
            if not proximo:
                return jsonify({'ok': True, 'proximo': None, 'segundos': None})
            from datetime import datetime
            segundos = int((proximo - datetime.now()).total_seconds())
            return jsonify({
                'ok': True,
                'proximo': proximo.isoformat(),
                'segundos': max(0, segundos),
                'label': proximo.strftime('%a %H:%M'),
            })
