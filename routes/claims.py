"""Claims routes: CRUD, PDF generation, claims list."""

import re

from flask import flash, jsonify, make_response, redirect, render_template, request, url_for

import database
from data_extract import complete_claim, create_claim
from database import Claim
from helpers import get_config


def init_app(app):

    @app.route('/claim/<int:claim_id>')
    def view_claim(claim_id):
        with database.get_db() as session:
            claim = session.get(Claim, claim_id)
            if not claim:
                return 'Reclamo no encontrado', 404
            _ = claim.provider
            _ = claim.items
            return render_template('claim.html', claim=claim)

    @app.route('/claim/create', methods=['POST'])
    def create_claim_route():
        try:
            invoice_id = int(request.form.get('invoice_id'))
        except (TypeError, ValueError):
            flash('Factura inválida para crear el reclamo.')
            return redirect(url_for('index'))

        selected_ids = request.form.getlist('selected_differences')
        if not selected_ids:
            flash('Seleccione al menos un registro para reclamo.')
            return redirect(request.referrer or url_for('index'))

        with database.get_db() as session:
            claim = create_claim(session, invoice_id, [int(i) for i in selected_ids])
            _ = claim.provider
            _ = claim.items
            return render_template('claim.html', claim=claim, auto_download=True)

    @app.route('/claim/<int:claim_id>/complete', methods=['POST'])
    def complete_claim_route(claim_id):
        with database.get_db() as session:
            claim = complete_claim(session, claim_id)
        if not claim:
            return 'Reclamo no encontrado', 404
        return redirect(url_for('view_claim', claim_id=claim.id))

    @app.route('/api/claims', methods=['POST'])
    def api_create_claim():
        payload = request.get_json() or {}
        factura_id = payload.get('factura_id')
        difference_ids = payload.get('difference_ids', [])
        if not factura_id or not difference_ids:
            return jsonify({'error': 'factura_id y difference_ids son obligatorios'}), 400

        try:
            with database.get_db() as session:
                claim = create_claim(session, int(factura_id), [int(i) for i in difference_ids])
        except Exception as exc:
            return jsonify({'error': str(exc)}), 400
        return jsonify({'claim_id': claim.id, 'estado': claim.estado}), 201

    @app.route('/claims')
    def claims_list():
        with database.get_db() as session:
            claims = (session.query(database.Claim)
                      .order_by(database.Claim.creado_en.desc()).all())
            for c in claims:
                _ = c.provider
            return render_template('claims_list.html', claims=claims)

    @app.route('/claim/<int:claim_id>/pdf')
    def claim_pdf(claim_id):
        from io import BytesIO

        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

        with database.get_db() as session:
            claim = session.get(Claim, claim_id)
            if not claim:
                return 'Reclamo no encontrado', 404
            _ = claim.provider
            _ = claim.items

        buf = BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4,
                                leftMargin=2*cm, rightMargin=2*cm,
                                topMargin=2*cm, bottomMargin=2*cm)

        styles = getSampleStyleSheet()
        DARK   = colors.HexColor('#1a1a1a')
        BRAND  = colors.HexColor('#EAB308')
        GRAY   = colors.HexColor('#555555')
        LGRAY  = colors.HexColor('#f5f5f5')
        HBG    = colors.HexColor('#2c2c2e')

        title_style = ParagraphStyle('title', fontSize=18, textColor=DARK,
                                     fontName='Helvetica-Bold', spaceAfter=4)
        sub_style   = ParagraphStyle('sub',   fontSize=10, textColor=GRAY,
                                     fontName='Helvetica', spaceAfter=2)
        label_style = ParagraphStyle('lbl',   fontSize=8,  textColor=GRAY,
                                     fontName='Helvetica-Bold', spaceBefore=10, spaceAfter=1)
        value_style = ParagraphStyle('val',   fontSize=10, textColor=DARK,
                                     fontName='Helvetica')

        numero_factura = claim.numero_factura or '—'
        proveedor_razon = claim.provider.razon_social if claim.provider else '—'
        proveedor_cuit  = claim.provider.cuit if claim.provider else '—'

        cfg = get_config()
        story = []

        story.append(Paragraph('Reclamo de Faltantes', title_style))
        story.append(Paragraph(f'N° de reclamo: <b>#{claim.id}</b> · {cfg["farmacia_nombre"]}', sub_style))
        story.append(Spacer(1, 0.4*cm))

        info_data = [
            [Paragraph('<b>Proveedor</b>', label_style), Paragraph('<b>Factura</b>', label_style)],
            [Paragraph(proveedor_razon, value_style),    Paragraph(numero_factura, value_style)],
            [Paragraph('<b>CUIT</b>', label_style),       Paragraph('<b>Fecha factura</b>', label_style)],
            [Paragraph(proveedor_cuit or '—', value_style), Paragraph(str(claim.fecha), value_style)],
            [Paragraph('<b>Fecha reclamo</b>', label_style), Paragraph('<b>Estado</b>', label_style)],
            [Paragraph(claim.creado_en.strftime('%d/%m/%Y') if claim.creado_en else '—', value_style),
             Paragraph(claim.estado, value_style)],
        ]
        info_table = Table(info_data, colWidths=[8.5*cm, 8.5*cm])
        info_table.setStyle(TableStyle([
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('BOTTOMPADDING', (0,0), (-1,-1), 2),
            ('TOPPADDING', (0,0), (-1,-1), 2),
            ('LINEBELOW', (0,1), (-1,1), 0.5, colors.HexColor('#dddddd')),
            ('LINEBELOW', (0,3), (-1,3), 0.5, colors.HexColor('#dddddd')),
            ('LINEBELOW', (0,5), (-1,5), 0.5, colors.HexColor('#dddddd')),
        ]))
        story.append(info_table)
        story.append(Spacer(1, 0.6*cm))

        story.append(Paragraph(f'Detalle de ítems ({len(claim.items)} producto{"s" if len(claim.items) != 1 else ""})', label_style))
        story.append(Spacer(1, 0.2*cm))

        headers = ['#', 'Código', 'Descripción', 'Fact.', 'ERP', 'Dif.']
        col_w   = [0.8*cm, 3*cm, 8.2*cm, 1.5*cm, 1.5*cm, 1.5*cm]

        hdr_style = ParagraphStyle('hdr', fontSize=8, textColor=colors.white,
                                   fontName='Helvetica-Bold', alignment=TA_CENTER)
        cell_style = ParagraphStyle('cell', fontSize=8, textColor=DARK, fontName='Helvetica')
        num_style  = ParagraphStyle('num',  fontSize=8, textColor=DARK, fontName='Helvetica',
                                    alignment=TA_CENTER)

        rows = [[Paragraph(h, hdr_style) for h in headers]]
        for i, item in enumerate(claim.items, 1):
            dif = item.diferencia or 0
            dif_str = f'+{dif}' if dif > 0 else str(dif)
            rows.append([
                Paragraph(str(i), num_style),
                Paragraph(item.codigo_barra or '—', num_style),
                Paragraph(item.descripcion or '—', cell_style),
                Paragraph(str(item.cantidad_factura or 0), num_style),
                Paragraph(str(item.cantidad_erp or 0), num_style),
                Paragraph(dif_str, num_style),
            ])

        items_table = Table(rows, colWidths=col_w, repeatRows=1)
        items_table.setStyle(TableStyle([
            ('BACKGROUND',   (0,0), (-1,0), HBG),
            ('ROWBACKGROUNDS',(0,1),(-1,-1), [colors.white, colors.HexColor('#fafafa')]),
            ('GRID',         (0,0), (-1,-1), 0.4, colors.HexColor('#e0e0e0')),
            ('VALIGN',       (0,0), (-1,-1), 'MIDDLE'),
            ('TOPPADDING',   (0,0), (-1,-1), 4),
            ('BOTTOMPADDING',(0,0), (-1,-1), 4),
            ('LEFTPADDING',  (0,0), (-1,-1), 5),
            ('RIGHTPADDING', (0,0), (-1,-1), 5),
        ]))
        story.append(items_table)

        doc.build(story)
        buf.seek(0)

        safe_factura = re.sub(r'[^a-zA-Z0-9_-]', '_', numero_factura)
        filename = f'Reclamo_N{claim.id}_{safe_factura}.pdf'

        response = make_response(buf.read())
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
