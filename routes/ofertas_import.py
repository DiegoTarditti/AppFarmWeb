"""Importador unificado de docs de ofertas (Fase B del roadmap).

Soporta XLSX hoy. PDF/OCR a futuro (Fase B Parte 2).

Flujo:
1. POST /api/ofertas/import-preview — sube archivo, parsea, devuelve preview JSON.
2. POST /api/ofertas/import-validar — valida items contra catálogo via producto_matcher.
3. POST /api/ofertas/import-candidatos — buscar productos similares para match manual.
4. POST /api/ofertas/import-guardar — recibe items mapeados + lab_id, upsertea en OfertaMinimo.
5. GET /ofertas/import — pantalla del wizard.
"""
import os
import tempfile

from flask import jsonify, render_template, request
from flask_login import login_required

import database
from database import Laboratorio, OfertaMinimo


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


def _previsualizar_pdf(path):
    """Devuelve datos crudos de un PDF de ofertas + mapping propuesto.

    Estrategia:
    1. Intenta `pdfplumber.extract_tables()` (funciona en PDFs digitales
       tabulares como Baliarda). Si extrae filas → usa eso.
    2. Si NO extrae nada (PDF escaneado o sin estructura de tabla), cae a:
       - `helpers.extract_text_with_ocr_fallback()` para sacar texto plano
         (con OCR si hace falta).
       - Tokeniza las líneas que parezcan filas de oferta y devuelve una
         matriz best-effort.

    En ambos casos:
    - Aplana headers multilínea ("DESCUENTO\\nMOD 1" → "DESCUENTO MOD 1").
    - Mapping inicial via `field_inference.inferir_columnas`.
    """
    import pdfplumber

    import field_inference as fi

    headers = []
    rows = []
    fuente = 'tablas'   # para informar a la UI cómo se parseó
    with pdfplumber.open(path) as pdf:
        primera = True
        for page in pdf.pages:
            tablas = page.extract_tables() or []
            for tabla in tablas:
                if not tabla:
                    continue
                tabla = [
                    [(c.replace('\n', ' ').strip() if c else '') for c in fila]
                    for fila in tabla
                ]
                primera_fila = tabla[0]
                if primera:
                    headers = primera_fila
                    rows.extend(tabla[1:])
                    primera = False
                else:
                    if primera_fila == headers:
                        rows.extend(tabla[1:])
                    else:
                        rows.extend(tabla)

    rows = [r for r in rows if any((c or '').strip() for c in r)]

    # Si extract_tables NO encontró nada, fallback a OCR + tokenización por línea.
    if not rows:
        fuente = 'ocr_lineas'
        headers, rows = _extraer_filas_por_ocr(path)

    if not headers and not rows:
        return {'headers': [], 'rows': [], 'mapping': {}, 'header_row': None,
                'count_filas': 0, 'fuente': fuente}

    mapping = fi.inferir_columnas(
        headers,
        sample_rows=rows[:10] if rows else None,
        candidatos=['ean', 'codigo', 'descripcion', 'unidades_minima',
                    'precio', 'descuento_psl', 'rentabilidad', 'plazo_pago',
                    'grupo_id'],
    )

    return {
        'headers': headers,
        'rows': rows,
        'mapping': mapping,
        'header_row': 0 if headers else None,
        'count_filas': len(rows),
        'fuente': fuente,
    }


def _extraer_filas_por_ocr(path):
    """Fallback para PDFs escaneados o sin estructura de tabla detectable.

    Usa `helpers.extract_text_with_ocr_fallback` para sacar texto plano
    (con OCR si hace falta). Después tokeniza cada línea por whitespace y
    devuelve filas como matriz uniforme. Header: una fila genérica
    'Col 1, Col 2...' — el user mapea manualmente desde el wizard.
    """
    from helpers import extract_text_with_ocr_fallback
    texto = extract_text_with_ocr_fallback(path, min_chars=50)
    return _texto_a_matriz(texto)


def _previsualizar_imagen(path):
    """Procesa una foto/escaneo de una lista de ofertas.

    Estrategia:
    - PIL para abrir.
    - Preprocess (grayscale + binarización) reusando `_preprocess_image_for_ocr`.
    - pytesseract para extraer texto.
    - Misma tokenización por línea que el fallback de PDF escaneado.
    """
    from helpers import _clean_ocr_text, _preprocess_image_for_ocr
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return {'headers': [], 'rows': [], 'mapping': {}, 'header_row': None,
                'count_filas': 0, 'fuente': 'sin_ocr',
                'error': 'pytesseract o PIL no disponibles'}

    try:
        img = Image.open(path)
        # Si la imagen tiene transparencia o paleta, convertimos a RGB
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        img = _preprocess_image_for_ocr(img)
        texto = pytesseract.image_to_string(img, lang='spa', config='--psm 6') or ''
        texto = _clean_ocr_text(texto)
    except Exception as e:
        return {'headers': [], 'rows': [], 'mapping': {}, 'header_row': None,
                'count_filas': 0, 'fuente': 'ocr_imagen',
                'error': f'Error OCR: {e}'}

    headers, rows = _texto_a_matriz(texto)

    if not headers and not rows:
        return {'headers': [], 'rows': [], 'mapping': {}, 'header_row': None,
                'count_filas': 0, 'fuente': 'ocr_imagen'}

    import field_inference as fi
    mapping = fi.inferir_columnas(
        headers,
        sample_rows=rows[:10] if rows else None,
        candidatos=['ean', 'codigo', 'descripcion', 'unidades_minima',
                    'precio', 'descuento_psl', 'rentabilidad', 'plazo_pago',
                    'grupo_id'],
    )

    return {
        'headers': headers,
        'rows': rows,
        'mapping': mapping,
        'header_row': 0 if headers else None,
        'count_filas': len(rows),
        'fuente': 'ocr_imagen',
    }


def _texto_a_matriz(texto):
    """Tokeniza un bloque de texto OCR en una matriz best-effort.

    Cada línea con ≥3 tokens (probablemente datos, no encabezados sueltos)
    se convierte en una fila. Padding a la derecha al ancho máximo.
    Devuelve (headers, rows) con headers genéricos 'Col N'.
    """
    if not texto or not texto.strip():
        return [], []

    filas_tokens = []
    for linea in texto.split('\n'):
        toks = [t for t in linea.split() if t.strip()]
        if len(toks) < 3:
            continue
        filas_tokens.append(toks)

    if not filas_tokens:
        return [], []

    n_cols = max(len(r) for r in filas_tokens)
    matriz = [r + [''] * (n_cols - len(r)) for r in filas_tokens]
    headers = [f'Col {i + 1}' for i in range(n_cols)]
    return headers, matriz


def _previsualizar_xlsx(path):
    """Devuelve datos CRUDOS del Excel + mapping propuesto.

    Para los valores: detecta el number_format de cada celda y si es porcentaje
    (contiene '%'), multiplica por 100 antes de serializar (snapshot muestra
    "25%" en vez de "0.25").
    """
    import openpyxl

    from parsers.ofertas_xlsx import _detectar_columnas

    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active

    all_rows_cells = list(ws.iter_rows())
    if not all_rows_cells:
        return {'headers': [], 'rows': [], 'mapping': {}, 'header_row': None,
                'count_filas': 0}

    rows_values = [tuple(c.value for c in r) for r in all_rows_cells]
    mapping, header_idx = _detectar_columnas(rows_values)

    if header_idx is not None:
        headers = [str(c) if c is not None else '' for c in rows_values[header_idx]]
        data_rows_cells = all_rows_cells[header_idx + 1:]
    else:
        first_row = next((r for r in rows_values if r), tuple())
        headers = [f'Col {i+1}' for i in range(len(first_row))]
        data_rows_cells = all_rows_cells

    def _format_cell(cell):
        v = cell.value
        if v is None or v == '':
            return ''
        fmt = (cell.number_format or '').strip()
        if '%' in fmt and isinstance(v, (int, float)):
            scaled = v * 100
            if scaled == int(scaled):
                return f'{int(scaled)}%'
            return f'{scaled:.2f}%'
        if isinstance(v, float):
            if v == int(v):
                return str(int(v))
            return f'{v:g}'
        return str(v)

    def _es_amarillo(fg_hex):
        if not fg_hex or len(str(fg_hex)) < 6:
            return False
        rgb = str(fg_hex).upper()[-6:]
        try:
            r = int(rgb[0:2], 16); g = int(rgb[2:4], 16); b = int(rgb[4:6], 16)
        except ValueError:
            return False
        return r >= 200 and g >= 200 and b <= 180 and not (r == 255 and g == 255 and b >= 230)

    def _row_destacada(row_cells):
        for c in row_cells:
            if c.value is None:
                continue
            fill = c.fill
            if not fill or fill.patternType in (None, 'none'):
                continue
            fg = getattr(fill.fgColor, 'rgb', None)
            if fg and _es_amarillo(str(fg)):
                return True
        return False

    rows_serializadas = []
    rows_destacadas = []
    for row_cells in data_rows_cells:
        if not row_cells or all(c.value is None or c.value == '' for c in row_cells):
            continue
        rows_serializadas.append([_format_cell(c) for c in row_cells])
        rows_destacadas.append(_row_destacada(row_cells))

    return {
        'headers': headers,
        'rows': rows_serializadas,
        'rows_destacadas': rows_destacadas,
        'mapping': mapping,
        'header_row': header_idx,
        'count_filas': len(rows_serializadas),
    }


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
        """Recibe un archivo XLSX, lo parsea, devuelve preview de items detectados."""
        if 'archivo' not in request.files:
            return jsonify({'error': 'Falta archivo'}), 400
        f = request.files['archivo']
        if not f.filename:
            return jsonify({'error': 'Archivo sin nombre'}), 400

        ext = os.path.splitext(f.filename)[1].lower()
        IMG_EXTS = ('.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tiff', '.tif')
        if ext not in ('.xlsx', '.xls', '.pdf', *IMG_EXTS):
            return jsonify({
                'error': f'Formato {ext} no soportado. Aceptamos XLSX, XLS, PDF '
                         'y fotos (JPG, PNG, WEBP).'
            }), 400

        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            f.save(tmp.name)
            tmp_path = tmp.name
        try:
            if ext == '.pdf':
                preview = _previsualizar_pdf(tmp_path)
            elif ext in IMG_EXTS:
                preview = _previsualizar_imagen(tmp_path)
            else:
                preview = _previsualizar_xlsx(tmp_path)
        except Exception as e:
            return jsonify({'error': f'Error al parsear: {e}'}), 500
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        return jsonify({**preview, 'filename': f.filename})

    @app.route('/api/ofertas/import-validar', methods=['POST'])
    @login_required
    def api_ofertas_import_validar():
        """Cruza los items contra el catálogo de productos local usando el
        matcher central (producto_matcher.match_productos_bulk).

        Body JSON: { items: [...], laboratorio_id?, umbral_variacion?: 30 }
        """
        import producto_matcher as pm
        data = request.get_json(silent=True) or {}
        items = data.get('items') or []
        lab_id = data.get('laboratorio_id')
        try:
            lab_id = int(lab_id) if lab_id else None
        except (TypeError, ValueError):
            lab_id = None

        if not items:
            return jsonify({'items': [], 'stats': {}})

        items_para_match = [
            {
                'ean': it.get('ean'),
                'codigo_alfabeta': it.get('codigo'),
                'descripcion': it.get('descripcion'),
                'precio': it.get('precio'),
            }
            for it in items
        ]

        with database.get_db() as session:
            results = pm.match_productos_bulk(items_para_match, laboratorio_id=lab_id, session=session)

        validados = []
        stats = {'ok': 0, 'warning': 0, 'fuzzy': 0, 'not_found': 0, 'sin_precio_previo': 0}
        for it, res in zip(items, results):
            entry = dict(it)
            if res.producto is None:
                entry['_status'] = 'not_found'
                entry['_motivo'] = 'No está en el catálogo local'
                entry['_candidatos_top'] = res.candidatos_top
                stats['not_found'] += 1
            else:
                p = res.producto
                entry['_match_descripcion_local'] = p.descripcion or ''
                # Producto local tiene `id`; ObsProducto tiene `observer_id`.
                entry['_producto_id'] = getattr(p, 'id', None)
                entry['_observer_id'] = getattr(p, 'observer_id', None)
                entry['_estrategia'] = res.estrategia
                entry['_score'] = res.score
                entry['_confianza'] = res.confianza
                # Si matcheó contra obs y no había codigo_alfabeta, lo adoptamos
                # para que el guardado lo use como `codigo`.
                if 'match_observer' in res.warnings:
                    if not entry.get('codigo') and getattr(p, 'codigo_alfabeta', None):
                        entry['codigo'] = p.codigo_alfabeta

                if 'precio_variacion_alta' in res.warnings:
                    entry['_status'] = 'warning'
                    var = res.debug.get('variacion_precio')
                    entry['_motivo'] = f'Variación de precio {var:+.1f}%' if var is not None else 'Variación alta'
                    stats['warning'] += 1
                elif (res.estrategia in ('fuzzy_lab', 'fuzzy_global',
                                          'fuzzy_otro_lab', 'tokens_superset')
                      or res.estrategia.endswith('_obs')):
                    entry['_status'] = 'fuzzy'
                    nota = ''
                    if res.estrategia == 'fuzzy_otro_lab':
                        nota = ' [otro lab]'
                    elif res.estrategia.endswith('_obs'):
                        nota = ' [ObServer/Alfabeta]'
                    entry['_motivo'] = (
                        f'Match por descripción{nota} (score {res.score:.2f}). '
                        f'Local: "{(res.producto.descripcion or "")[:80]}"'
                    )
                    stats['fuzzy'] += 1
                else:
                    entry['_status'] = 'ok'
                    stats['ok'] += 1

                pp = getattr(res.producto, 'precio_pvp', None)
                if pp in (None, 0) and it.get('precio') is not None:
                    entry['_motivo_info'] = 'Sin precio previo en catálogo'
                    stats['sin_precio_previo'] += 1

            validados.append(entry)

        return jsonify({'items': validados, 'stats': stats, 'total': len(validados)})

    @app.route('/api/ofertas/import-candidatos', methods=['POST'])
    @login_required
    def api_ofertas_import_candidatos():
        """Para un item 'no encontrado', devuelve top-N productos similares.
        Reusa producto_matcher.buscar_candidatos."""
        import producto_matcher as pm
        data = request.get_json(silent=True) or {}
        descr = (data.get('descripcion') or '').strip()
        try:
            lab_id = int(data.get('laboratorio_id')) if data.get('laboratorio_id') else None
        except (ValueError, TypeError):
            lab_id = None
        try:
            top = max(1, min(20, int(data.get('top', 8))))
        except (ValueError, TypeError):
            top = 8
        return jsonify({'candidatos': pm.buscar_candidatos(descr, laboratorio_id=lab_id, top=top)})

    @app.route('/api/ofertas/import-guardar', methods=['POST'])
    @login_required
    def api_ofertas_import_guardar():
        """Recibe items mapeados + laboratorio_id, upsertea en OfertaMinimo.

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

        # Normalizar % de Excel
        for it in items:
            for k in ('descuento_psl', 'rentabilidad'):
                v = it.get(k)
                if v is not None and v != '':
                    try:
                        fv = float(str(v).replace(',', '.'))
                        if 0 < fv < 1:
                            it[k] = fv * 100
                    except (ValueError, TypeError):
                        pass

        reemplazar = bool(data.get('reemplazar'))

        with database.get_db() as session:
            lab = session.get(Laboratorio, lab_id)
            if not lab:
                return jsonify({'error': 'Laboratorio no encontrado'}), 404

            if reemplazar:
                session.query(OfertaMinimo).filter_by(laboratorio_id=lab_id).delete()
                session.flush()

            from helpers import now_ar
            insertados = actualizados = saltados = 0
            for it in items:
                ean = (str(it.get('ean') or '').strip()) or None
                codigo = (str(it.get('codigo') or '').strip()) or None
                if not ean and not codigo:
                    saltados += 1
                    continue

                q = session.query(OfertaMinimo).filter_by(laboratorio_id=lab_id)
                if ean:
                    q = q.filter(OfertaMinimo.ean == ean)
                else:
                    q = q.filter(OfertaMinimo.codigo == codigo)
                existing = q.first()

                # Catalogar tipo: si tiene mínimo > 1 → 'con_minimo', sino 'simple'.
                um = _to_int(it.get('unidades_minima'))
                tipo_desc = 'con_minimo' if (um is not None and um > 1) else 'simple'

                if existing:
                    if it.get('descripcion'):
                        existing.descripcion = str(it['descripcion'])[:300]
                    if codigo and not existing.codigo:
                        existing.codigo = codigo[:50]
                    if it.get('unidades_minima') is not None:
                        existing.unidades_minima = um
                    if it.get('descuento_psl') is not None:
                        existing.descuento_psl = _to_float(it['descuento_psl'])
                    if it.get('rentabilidad') is not None:
                        existing.rentabilidad = _to_float(it['rentabilidad'])
                    if it.get('plazo_pago'):
                        existing.plazo_pago = str(it['plazo_pago'])[:100]
                    if it.get('grupo_id') is not None:
                        existing.grupo_id = _to_int(it['grupo_id'])
                    existing.tipo_descuento = tipo_desc
                    existing.actualizado_en = now_ar()
                    actualizados += 1
                else:
                    session.add(OfertaMinimo(
                        laboratorio_id=lab_id,
                        ean=(ean or '')[:20],
                        codigo=(codigo or None) and codigo[:50],
                        descripcion=(str(it.get('descripcion') or ''))[:300] or None,
                        unidades_minima=um,
                        descuento_psl=_to_float(it.get('descuento_psl')),
                        rentabilidad=_to_float(it.get('rentabilidad')),
                        plazo_pago=(str(it.get('plazo_pago') or ''))[:100] or None,
                        grupo_id=_to_int(it.get('grupo_id')),
                        tipo_descuento=tipo_desc,
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
