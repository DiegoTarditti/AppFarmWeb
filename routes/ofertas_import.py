"""Importador unificado de docs de ofertas (Fase B del roadmap).

Soporta XLSX hoy. PDF/OCR a futuro (Fase B Parte 2).

Flujo:
1. POST /api/ofertas/import-preview — sube archivo, parsea, devuelve preview JSON
   con items detectados y columnas mapeadas.
2. POST /api/ofertas/import-guardar — recibe items editados + laboratorio_id,
   upsertea en OfertaMinimo.
3. GET /ofertas/import — pantalla con drag&drop, preview editable, asignar lab.
"""
import os
import tempfile

from flask import jsonify, render_template, request
from flask_login import login_required

import database
from database import Laboratorio, OfertaMinimo


def init_app(app):

    @app.route('/ofertas/import', methods=['GET'])
    @login_required
    def ofertas_import_page():
        with database.get_db() as session:
            labs = (session.query(Laboratorio)
                    .filter(Laboratorio.activo == True)  # noqa: E712
                    .order_by(Laboratorio.nombre).all())
            labs_data = [{'id': l.id, 'nombre': l.nombre} for l in labs]
        return render_template('ofertas_import.html', laboratorios=labs_data)

    @app.route('/api/ofertas/import-preview', methods=['POST'])
    @login_required
    def api_ofertas_import_preview():
        """Recibe un archivo, lo parsea, devuelve preview de items detectados."""
        if 'archivo' not in request.files:
            return jsonify({'error': 'Falta archivo'}), 400
        f = request.files['archivo']
        if not f.filename:
            return jsonify({'error': 'Archivo sin nombre'}), 400

        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in ('.xlsx', '.xls'):
            return jsonify({
                'error': f'Formato {ext} no soportado todavía. Solo XLSX por ahora. '
                         'PDF/foto vendrá en la próxima iteración.'
            }), 400

        # Guardar temporal y parsear
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            f.save(tmp.name)
            tmp_path = tmp.name
        try:
            preview = _previsualizar_xlsx(tmp_path)
        except Exception as e:
            return jsonify({'error': f'Error al parsear: {e}'}), 500
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        return jsonify({
            **preview,
            'filename': f.filename,
        })


def _previsualizar_xlsx(path):
    """Devuelve datos CRUDOS del Excel + mapping propuesto. El frontend hace
    el resto (remapeo manual + render de tabla)."""
    import openpyxl
    from parsers.ofertas_xlsx import _detectar_columnas, _norm

    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return {'headers': [], 'rows': [], 'mapping': {}, 'header_row': None,
                'count_filas': 0}

    mapping, header_idx = _detectar_columnas(rows)

    # Si encontramos header, esa es la fila de headers. Si no, generamos labels
    # genéricos "Col 1", "Col 2", ...
    if header_idx is not None:
        headers = [str(c) if c is not None else '' for c in rows[header_idx]]
        data_rows = rows[header_idx + 1:]
    else:
        # Sin header detectado — usar la primera fila NO vacía como referencia
        # para conocer cuántas columnas hay.
        first_row = next((r for r in rows if r), tuple())
        headers = [f'Col {i+1}' for i in range(len(first_row))]
        data_rows = rows

    # Limitar tamaño de la respuesta (preview, no todos los datos para guardar)
    # Para guardar después, el frontend re-mapea las rows que ya tiene.
    rows_serializadas = []
    for row in data_rows:
        if not row:
            continue
        rows_serializadas.append([
            (str(c) if c is not None else '') for c in row
        ])

    return {
        'headers': headers,
        'rows': rows_serializadas,
        'mapping': mapping,  # {'ean': 2, 'descripcion': 3, ...}
        'header_row': header_idx,
        'count_filas': len(rows_serializadas),
    }

    @app.route('/api/ofertas/import-guardar', methods=['POST'])
    @login_required
    def api_ofertas_import_guardar():
        """Recibe items mapeados + laboratorio_id, upsertea en OfertaMinimo.
        Body JSON: { laboratorio_id, items: [...], reemplazar?: bool }

        Items pueden venir con descuentos en formato decimal Excel (0.2 = 20%);
        el sistema lo normaliza a enteros (×100 si entre 0 y 1).
        """
        data = request.get_json(silent=True) or {}
        try:
            lab_id = int(data.get('laboratorio_id'))
        except (TypeError, ValueError):
            return jsonify({'error': 'laboratorio_id inválido'}), 400
        items = data.get('items') or []
        if not isinstance(items, list) or not items:
            return jsonify({'error': 'items vacío'}), 400

        # Normalizar % de Excel: si valor entre 0 y 1, asumir formato fracción y x100.
        for it in items:
            for k in ('descuento_psl', 'rentabilidad'):
                v = it.get(k)
                if v is not None and v != '':
                    try:
                        f = float(str(v).replace(',', '.'))
                        if 0 < f < 1:
                            it[k] = f * 100
                    except (ValueError, TypeError):
                        pass
        # Si reemplazar=True, primero borramos las ofertas vigentes del lab.
        reemplazar = bool(data.get('reemplazar'))

        with database.get_db() as session:
            lab = session.get(Laboratorio, lab_id)
            if not lab:
                return jsonify({'error': 'Laboratorio no encontrado'}), 404

            if reemplazar:
                session.query(OfertaMinimo).filter_by(laboratorio_id=lab_id).delete()
                session.flush()

            # Upsert por (laboratorio_id, ean). Si no hay ean, por (laboratorio_id, codigo).
            from helpers import now_ar
            insertados = actualizados = saltados = 0
            for it in items:
                ean = (str(it.get('ean') or '').strip()) or None
                codigo = (str(it.get('codigo') or '').strip()) or None
                if not ean and not codigo:
                    saltados += 1
                    continue

                # Buscar existente
                q = session.query(OfertaMinimo).filter_by(laboratorio_id=lab_id)
                if ean:
                    q = q.filter(OfertaMinimo.ean == ean)
                else:
                    q = q.filter(OfertaMinimo.codigo == codigo)
                existing = q.first()

                if existing:
                    if it.get('descripcion'):
                        existing.descripcion = str(it['descripcion'])[:300]
                    if codigo and not existing.codigo:
                        existing.codigo = codigo[:50]
                    if it.get('unidades_minima') is not None:
                        existing.unidades_minima = _to_int(it['unidades_minima'])
                    if it.get('descuento_psl') is not None:
                        existing.descuento_psl = _to_float(it['descuento_psl'])
                    if it.get('rentabilidad') is not None:
                        existing.rentabilidad = _to_float(it['rentabilidad'])
                    if it.get('plazo_pago'):
                        existing.plazo_pago = str(it['plazo_pago'])[:100]
                    if it.get('grupo_id') is not None:
                        existing.grupo_id = _to_int(it['grupo_id'])
                    existing.actualizado_en = now_ar()
                    actualizados += 1
                else:
                    session.add(OfertaMinimo(
                        laboratorio_id=lab_id,
                        ean=(ean or '')[:20],
                        codigo=(codigo or None) and codigo[:50],
                        descripcion=(str(it.get('descripcion') or ''))[:300] or None,
                        unidades_minima=_to_int(it.get('unidades_minima')),
                        descuento_psl=_to_float(it.get('descuento_psl')),
                        rentabilidad=_to_float(it.get('rentabilidad')),
                        plazo_pago=(str(it.get('plazo_pago') or ''))[:100] or None,
                        grupo_id=_to_int(it.get('grupo_id')),
                    ))
                    insertados += 1

            session.commit()

        return jsonify({
            'ok': True,
            'laboratorio': lab.nombre,
            'insertados': insertados,
            'actualizados': actualizados,
            'saltados': saltados,
            'total': insertados + actualizados,
        })


def _to_int(v):
    if v is None or v == '':
        return None
    try:
        return int(float(str(v).replace(',', '.')))
    except (ValueError, TypeError):
        return None


def _to_float(v):
    if v is None or v == '':
        return None
    try:
        return float(str(v).replace(',', '.'))
    except (ValueError, TypeError):
        return None
