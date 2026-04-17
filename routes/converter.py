"""Converter routes: generic document conversion tool."""

import os
from flask import render_template, request, redirect, url_for, flash, jsonify, send_file
from werkzeug.utils import secure_filename
from helpers import CONVERTER_DIR, _build_item_pattern


def _converter_meta_path(fname):
    return os.path.join(CONVERTER_DIR, fname + '.meta.json')

def _converter_read_meta(fname):
    import json as _json
    p = _converter_meta_path(fname)
    if os.path.exists(p):
        try:
            with open(p, 'r', encoding='utf-8') as fh:
                return _json.load(fh) or {}
        except Exception:
            return {}
    return {}

def _converter_write_meta(fname, data):
    import json as _json
    with open(_converter_meta_path(fname), 'w', encoding='utf-8') as fh:
        _json.dump(data, fh)


def init_app(app):

    @app.route('/converter', methods=['GET'])
    def converter_index():
        files = []
        try:
            for fn in sorted(os.listdir(CONVERTER_DIR), reverse=True):
                if fn.endswith('.meta.json'):
                    continue
                p = os.path.join(CONVERTER_DIR, fn)
                if os.path.isfile(p):
                    meta = _converter_read_meta(fn)
                    files.append({'name': fn, 'size': os.path.getsize(p), 'tipo': meta.get('tipo_doc', '')})
        except Exception:
            pass
        return render_template('converter_index.html', files=files[:20])

    @app.route('/converter/<token>/meta', methods=['POST'])
    def converter_meta(token):
        safe = secure_filename(token)
        path = os.path.join(CONVERTER_DIR, safe)
        if not os.path.exists(path):
            return jsonify({'error': 'Documento no encontrado'}), 404
        body = request.get_json(silent=True) or {}
        tipo = (body.get('tipo_doc') or '').strip()
        meta = _converter_read_meta(safe)
        meta['tipo_doc'] = tipo
        _converter_write_meta(safe, meta)
        return jsonify({'ok': True, 'tipo_doc': tipo})

    @app.route('/converter/upload', methods=['POST'])
    def converter_upload():
        import uuid as _uuid
        f = request.files.get('document')
        if not f or not f.filename:
            flash('Elegí un archivo.')
            return redirect(url_for('converter_index'))
        ext = os.path.splitext(f.filename)[1].lower() or '.pdf'
        if ext not in ('.pdf',):
            flash('Por ahora sólo PDF.')
            return redirect(url_for('converter_index'))
        token = _uuid.uuid4().hex[:12]
        fname = token + '_' + secure_filename(f.filename)
        f.save(os.path.join(CONVERTER_DIR, fname))
        return redirect(url_for('converter_helper', token=fname))

    @app.route('/converter/<token>/auto', methods=['GET'])
    def converter_auto(token):
        import pdfplumber as _plumber
        from collections import defaultdict as _dd
        safe = secure_filename(token)
        path = os.path.join(CONVERTER_DIR, safe)
        if not os.path.exists(path):
            flash('Documento no encontrado.')
            return redirect(url_for('converter_index'))

        tables = []
        with _plumber.open(path) as pdf:
            for page in pdf.pages:
                for tbl in (page.extract_tables() or []):
                    if tbl and len(tbl) > 2:
                        tables.append(tbl)

        if tables:
            best = max(tables, key=lambda t: len(t))
            rows = best
            source = 'Tabla detectada automáticamente'
        else:
            all_words = []
            with _plumber.open(path) as pdf:
                for page in pdf.pages:
                    all_words.extend(page.extract_words(x_tolerance=3, y_tolerance=3) or [])
            rows_dict = _dd(list)
            for w in all_words:
                y_key = round(w['top'] / 4) * 4
                rows_dict[y_key].append(w)
            rows = []
            for y in sorted(rows_dict.keys()):
                row = sorted(rows_dict[y], key=lambda w: w['x0'])
                rows.append([w['text'] for w in row])
            source = 'Palabras agrupadas por posición Y'

        return render_template('converter_auto.html', token=safe, filename=safe, rows=rows, source=source)

    @app.route('/converter/<token>/delete', methods=['POST'])
    def converter_delete(token):
        safe = secure_filename(token)
        path = os.path.join(CONVERTER_DIR, safe)
        if os.path.exists(path):
            try:
                os.remove(path)
                mp = _converter_meta_path(safe)
                if os.path.exists(mp):
                    os.remove(mp)
                flash('Documento eliminado.')
            except Exception as e:
                flash(f'Error al eliminar: {e}')
        return redirect(url_for('converter_index'))

    @app.route('/converter/<token>/helper', methods=['GET'])
    def converter_helper(token):
        safe = secure_filename(token)
        path = os.path.join(CONVERTER_DIR, safe)
        if not os.path.exists(path):
            flash('Documento no encontrado.')
            return redirect(url_for('converter_index'))
        import pdfplumber as _plumber
        pdf_text = ''
        with _plumber.open(path) as pdf:
            for page in pdf.pages:
                pdf_text += (page.extract_text() or '') + '\n\n'
        meta = _converter_read_meta(safe)
        return render_template('converter_helper.html', token=safe, filename=safe, pdf_text=pdf_text, tipo_doc=meta.get('tipo_doc', ''))

    @app.route('/converter/<token>/pick', methods=['GET'])
    def converter_pick(token):
        import pdfplumber as _plumber
        safe = secure_filename(token)
        path = os.path.join(CONVERTER_DIR, safe)
        if not os.path.exists(path):
            flash('Documento no encontrado.')
            return redirect(url_for('converter_index'))
        pdf_text = ''
        with _plumber.open(path) as pdf:
            for page in pdf.pages:
                pdf_text += (page.extract_text() or '') + '\n\n'
        return render_template('converter_pick.html', token=safe, pdf_text=pdf_text, filename=safe)

    @app.route('/converter/<token>/infer', methods=['POST'])
    def converter_infer(token):
        import re as _re
        import pdfplumber as _plumber

        body = request.get_json(silent=True) or {}
        example_line = body.get('example_line', '')
        selections = body.get('selections', [])

        safe = secure_filename(token)
        path = os.path.join(CONVERTER_DIR, safe)
        if not os.path.exists(path):
            return jsonify({'error': 'Documento no encontrado'}), 404
        if not example_line or not selections:
            return jsonify({'error': 'Faltan datos'}), 400

        pattern, fields, base_fields, _base = _build_item_pattern(example_line, selections)

        pdf_text = ''
        with _plumber.open(path) as pdf:
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

    @app.route('/converter/<token>/export', methods=['POST'])
    def converter_export(token):
        import io as _io
        import openpyxl as _ox
        body = request.get_json(silent=True) or {}
        rows = body.get('rows', [])
        header = body.get('header', {}) or {}
        fields = body.get('fields', [])
        if not rows:
            return jsonify({'error': 'No hay filas para exportar'}), 400

        wb = _ox.Workbook()
        ws = wb.active
        ws.title = 'Datos'

        r = 1
        if header:
            for k, v in header.items():
                ws.cell(row=r, column=1, value=k)
                ws.cell(row=r, column=2, value=v)
                r += 1
            r += 1

        cols = fields or (list(rows[0].keys()) if rows else [])
        for ci, c in enumerate(cols, start=1):
            ws.cell(row=r, column=ci, value=c)
        r += 1
        for row in rows:
            for ci, c in enumerate(cols, start=1):
                ws.cell(row=r, column=ci, value=row.get(c, ''))
            r += 1

        buf = _io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        out_name = os.path.splitext(token)[0] + '.xlsx'
        return send_file(buf, as_attachment=True, download_name=out_name,
                   mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
