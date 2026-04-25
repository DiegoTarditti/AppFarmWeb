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

    @app.route('/api/ofertas/import-validar', methods=['POST'])
    @login_required
    def api_ofertas_import_validar():
        """Cruza los items contra el catálogo de productos local.

        Cascada de match:
        1. Exacto por EAN (codigo_barra + alts).
        2. Exacto por codigo_alfabeta.
        3. Fuzzy por descripción + laboratorio_id (Jaccard, threshold 0.80).
        4. Sin match → 'not_found'.

        Para los matched: si hay precio_pvp previo y precio nuevo, comparar
        variación. > umbral → 'warning'.

        Body JSON: { items: [...], laboratorio_id?, umbral_variacion?: 30 }
        """
        from sqlalchemy import or_
        from observer_matcher import _normalize, _tokens, _jaccard
        data = request.get_json(silent=True) or {}
        items = data.get('items') or []
        lab_id = data.get('laboratorio_id')
        try:
            lab_id = int(lab_id) if lab_id else None
        except (TypeError, ValueError):
            lab_id = None
        try:
            umbral = float(data.get('umbral_variacion', 30))
        except (ValueError, TypeError):
            umbral = 30

        if not items:
            return jsonify({'items': [], 'stats': {}})

        # Recolectar todos los códigos a buscar
        eans = {str(it.get('ean')).strip() for it in items if it.get('ean')}
        codigos = {str(it.get('codigo')).strip() for it in items if it.get('codigo')}
        eans.discard('')
        codigos.discard('')

        # Buscar productos locales que matcheen alguno
        prod_por_ean = {}
        prod_por_alfabeta = {}
        with database.get_db() as session:
            P = database.Producto
            cond = []
            if eans:
                cond.append(P.codigo_barra.in_(eans))
                for col_name in ('codigo_barra_alt1', 'codigo_barra_alt2', 'codigo_barra_alt3'):
                    col = getattr(P, col_name, None)
                    if col is not None:
                        cond.append(col.in_(eans))
            if codigos:
                cond.append(P.codigo_alfabeta.in_(codigos))
            if cond:
                for p in session.query(P).filter(or_(*cond)).all():
                    for cb in (p.codigo_barra,
                               getattr(p, 'codigo_barra_alt1', None),
                               getattr(p, 'codigo_barra_alt2', None),
                               getattr(p, 'codigo_barra_alt3', None)):
                        if cb:
                            prod_por_ean[cb] = p
                    if p.codigo_alfabeta:
                        prod_por_alfabeta[p.codigo_alfabeta] = p

            # Para fuzzy: precargar productos del lab (si está) con sus tokens
            productos_del_lab = []
            if lab_id:
                rows = session.query(P).filter(P.laboratorio_id == lab_id).all()
                productos_del_lab = [(p, _tokens(p.descripcion or '')) for p in rows]

        validados = []
        stats = {'ok': 0, 'warning': 0, 'fuzzy': 0, 'not_found': 0, 'sin_precio_previo': 0}
        for it in items:
            ean = str(it.get('ean') or '').strip()
            cod = str(it.get('codigo') or '').strip()
            descr = str(it.get('descripcion') or '').strip()
            prod = (prod_por_ean.get(ean) if ean else None) or \
                   (prod_por_alfabeta.get(cod) if cod else None)
            estrategia = 'exacto' if prod else None

            # Fallback: fuzzy por descripción + lab si no matchó exacto
            if not prod and descr and productos_del_lab:
                toks_in = _tokens(descr)
                if len(toks_in) >= 2:  # evitar matches con descripciones muy cortas
                    mejor = None
                    mejor_score = 0.0
                    empate = False
                    for p, toks_p in productos_del_lab:
                        if not toks_p:
                            continue
                        score = _jaccard(toks_in, toks_p)
                        if score > mejor_score:
                            mejor = p
                            mejor_score = score
                            empate = False
                        elif score == mejor_score and score > 0:
                            empate = True
                    if mejor and mejor_score >= 0.80 and not empate:
                        prod = mejor
                        estrategia = 'fuzzy'

            entry = dict(it)
            if not prod:
                entry['_status'] = 'not_found'
                entry['_motivo'] = 'No está en el catálogo local'
                stats['not_found'] += 1
            else:
                entry['_match_descripcion_local'] = prod.descripcion or ''
                entry['_producto_id'] = prod.id
                entry['_estrategia'] = estrategia
                # Comparar precio si hay
                precio_nuevo = it.get('precio')
                precio_local = float(prod.precio_pvp) if prod.precio_pvp else None
                base_status = 'fuzzy' if estrategia == 'fuzzy' else 'ok'

                if precio_nuevo is not None and precio_local and precio_local > 0:
                    try:
                        variacion = (float(precio_nuevo) - precio_local) / precio_local * 100
                        entry['_variacion_pct'] = round(variacion, 1)
                        entry['_precio_anterior'] = precio_local
                        if abs(variacion) > umbral:
                            entry['_status'] = 'warning'
                            entry['_motivo'] = (
                                f'Variación {variacion:+.1f}% '
                                f'(${precio_local:.2f} → ${precio_nuevo:.2f})'
                            )
                            stats['warning'] += 1
                        else:
                            entry['_status'] = base_status
                            stats[base_status] = stats.get(base_status, 0) + 1
                    except (ValueError, TypeError):
                        entry['_status'] = base_status
                        stats[base_status] = stats.get(base_status, 0) + 1
                else:
                    entry['_status'] = base_status
                    if precio_local is None or precio_local == 0:
                        entry['_motivo_info'] = 'Sin precio previo en catálogo (no se puede comparar)'
                        stats['sin_precio_previo'] += 1
                    stats[base_status] = stats.get(base_status, 0) + 1

                if estrategia == 'fuzzy':
                    entry['_motivo'] = (
                        f'Match por descripción (no había EAN/alfabeta exacto). '
                        f'Local: "{prod.descripcion[:80] if prod.descripcion else ""}"'
                    )
            validados.append(entry)

        return jsonify({
            'items': validados,
            'stats': stats,
            'umbral_variacion': umbral,
            'total': len(validados),
        })

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
