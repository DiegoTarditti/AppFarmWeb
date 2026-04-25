"""Shared helpers, constants and utility functions used across route modules."""

import os
import re
from datetime import datetime, timedelta, timezone

import database
from database import Producto

AR_TZ = timezone(timedelta(hours=-3))

def now_ar():
    """Hora actual en Argentina (UTC-3), sin tzinfo para compatibilidad con SQLAlchemy DateTime."""
    return datetime.now(AR_TZ).replace(tzinfo=None)


def detectar_entorno():
    """Detecta dónde está corriendo la app. Retorna dict con:
      - codigo: 'render' | 'local' | 'local_render_db'
      - label:  'PROD' | 'LOCAL' | 'LOCAL→PROD'
      - color:  color hex para el badge/topbar
    """
    # Render setea estas env vars automáticamente en sus servicios
    if os.environ.get('RENDER') or os.environ.get('RENDER_INSTANCE_ID'):
        return {'codigo': 'render', 'label': 'PROD', 'color': '#16A34A',
                'descripcion': 'Producción (Render)'}
    # Docker local apuntando a la DB de Render (escenario dev remoto)
    db_url = os.environ.get('DATABASE_URL', '')
    if 'render.com' in db_url or 'oregon-postgres' in db_url:
        return {'codigo': 'local_render_db', 'label': 'LOCAL→PROD', 'color': '#EF4444',
                'descripcion': 'Local apuntando a DB de Render (cuidado)'}
    return {'codigo': 'local', 'label': 'LOCAL', 'color': '#F59E0B',
            'descripcion': 'Docker local'}

# ── Constants ────────────────────────────────────────────────────────────────

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
PARSERS_FOLDER = os.path.join(os.path.dirname(__file__), 'parsers')
ALLOWED_EXTENSIONS = {'pdf', 'xlsx', 'xls'}
CONVERTER_DIR = os.path.join(UPLOAD_FOLDER, 'converter')
PURCHASE_FOLDER = os.path.join(UPLOAD_FOLDER, 'purchase')

# Las 3 entidades unificadas (ver project_entidades.md)
PARTNER_TIPOS = ('laboratorio', 'drogueria', 'proveedor')
PLANTILLA_FORMATOS = ('xlsx', 'txt_fijo', 'csv')
PLANTILLA_TIPOS_DOC = ('pedido', 'recepcion', 'descuento')

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(CONVERTER_DIR, exist_ok=True)
os.makedirs(PURCHASE_FOLDER, exist_ok=True)


# ── Utility functions ────────────────────────────────────────────────────────

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_providers():
    with database.get_db() as session:
        providers = session.query(database.Provider).order_by(database.Provider.razon_social).all()
        return [{'id': p.id, 'razon_social': p.razon_social, 'cuit': p.cuit or '',
                 'parser_file': p.parser_file or '',
                 'ruta_facturas': p.ruta_facturas or '',
                 'grabar_productos': p.grabar_productos if p.grabar_productos is not None else 1} for p in providers]


def _make_parser_slug(name):
    """'DROGUERÍA EJEMPLO S.A.' → 'droguer_a_ejemplo_s_a'"""
    return re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')


def _ensure_parser_file(parser_name, razon_social, cuit=''):
    """Crea el archivo parser desde la plantilla si no existe."""
    parser_path = os.path.join(PARSERS_FOLDER, f'{parser_name}.py')
    if not os.path.exists(parser_path):
        with open(os.path.join(PARSERS_FOLDER, '_template.py'), encoding='utf-8') as f:
            template = f.read()
        content = (template
                   .replace('{{RAZON_SOCIAL}}', razon_social)
                   .replace('{{CUIT}}', cuit))
        with open(parser_path, 'w', encoding='utf-8') as f:
            f.write(content)


def _get_or_create_provider_by_name(razon_social, cuit='', parser_name=''):
    with database.get_db() as session:
        provider = None
        if cuit:
            provider = session.query(database.Provider).filter_by(cuit=cuit).first()
        if not provider:
            from sqlalchemy import func
            provider = session.query(database.Provider).filter(
                func.lower(database.Provider.razon_social) == razon_social.lower()
            ).first()
        if not provider:
            provider = database.Provider(razon_social=razon_social,
                                         cuit=cuit or None,
                                         parser_file=parser_name)
            session.add(provider)
            session.commit()
        elif not provider.parser_file and parser_name:
            provider.parser_file = parser_name
            session.commit()
        return provider.id, provider.parser_file


def get_config():
    with database.get_db() as session:
        cfg = session.get(database.Config, 1)
        if not cfg:
            cfg = database.Config(id=1, farmacia_nombre='Farmacia', ruta_facturas='')
            session.add(cfg)
            session.commit()
        return {
            'farmacia_nombre': cfg.farmacia_nombre,
            'ruta_facturas': cfg.ruta_facturas or '',
            'umbral_pico': float(cfg.umbral_pico or 1.30),
            'umbral_baja': float(cfg.umbral_baja or 0.70),
            'umbral_tendencia': float(cfg.umbral_tendencia or 0.20),
            'rot_alta_min': float(cfg.rot_alta_min or 20.0),
            'rot_alta_tol': float(cfg.rot_alta_tol or 0.0),
            'rot_media_min': float(cfg.rot_media_min or 5.0),
            'rot_media_tol': float(cfg.rot_media_tol or 0.0),
            'rot_baja_tol': float(cfg.rot_baja_tol or 0.0),
        }


# ── Product helpers ──────────────────────────────────────────────────────────

def _find_producto(session, codigo_barra):
    """Busca un producto por código principal o cualquier alternativo (alt1/2/3)."""
    from sqlalchemy import or_
    bc = str(codigo_barra).strip()
    return session.query(Producto).filter(
        or_(
            Producto.codigo_barra == bc,
            Producto.codigo_barra_alt1 == bc,
            Producto.codigo_barra_alt2 == bc,
            Producto.codigo_barra_alt3 == bc,
        )
    ).first()


def _upsert_producto(session, codigo_barra, descripcion, precio_pvp=None, laboratorio_id=None, fecha_compra=None, codigo_alfabeta=None):
    """Crea o actualiza un producto en la tabla productos."""
    if not codigo_barra:
        return
    codigo_barra = str(codigo_barra).strip()
    codigo_alfabeta = str(codigo_alfabeta).strip() if codigo_alfabeta else None
    prod = _find_producto(session, codigo_barra)
    if prod:
        if descripcion and not prod.descripcion:
            prod.descripcion = str(descripcion).strip()
        if precio_pvp and float(precio_pvp) > 0:
            prod.precio_pvp = precio_pvp
        if laboratorio_id and not prod.laboratorio_id:
            prod.laboratorio_id = laboratorio_id
        if fecha_compra and (not prod.ultima_compra or fecha_compra > prod.ultima_compra):
            prod.ultima_compra = fecha_compra
        if codigo_alfabeta and not prod.codigo_alfabeta:
            prod.codigo_alfabeta = codigo_alfabeta
        from datetime import datetime as _dt
        prod.actualizado_en = _dt.utcnow()
    else:
        session.add(Producto(
            codigo_barra=codigo_barra,
            descripcion=str(descripcion).strip() if descripcion else '',
            precio_pvp=precio_pvp,
            laboratorio_id=laboratorio_id,
            ultima_compra=fecha_compra,
            codigo_alfabeta=codigo_alfabeta,
        ))


def _add_alt_barcode(session, codigo_barra_erp, codigo_barra_alt):
    """Agrega un código alternativo al producto ERP si no está ya registrado."""
    if not codigo_barra_erp or not codigo_barra_alt:
        return
    codigo_barra_erp = str(codigo_barra_erp).strip()
    codigo_barra_alt = str(codigo_barra_alt).strip()
    if codigo_barra_erp == codigo_barra_alt:
        return
    prod = session.query(Producto).filter_by(codigo_barra=codigo_barra_erp).first()
    if not prod:
        return
    existing = {prod.codigo_barra_alt1, prod.codigo_barra_alt2, prod.codigo_barra_alt3}
    if codigo_barra_alt in existing:
        return
    if not prod.codigo_barra_alt1:
        prod.codigo_barra_alt1 = codigo_barra_alt
    elif not prod.codigo_barra_alt2:
        prod.codigo_barra_alt2 = codigo_barra_alt
    elif not prod.codigo_barra_alt3:
        prod.codigo_barra_alt3 = codigo_barra_alt


# ── Bulk product upsert ──────────────────────────────────────────────────────

def _bulk_upsert_productos(session, items):
    """Upsert masivo: 1 SELECT en vez de N. items: list of (codigo_barra, descripcion, precio_pvp, fecha_compra)."""
    from datetime import datetime as _dt

    from sqlalchemy import or_

    barcodes = list({str(i[0]).strip() for i in items if i[0]})
    if not barcodes:
        return

    existing = session.query(Producto).filter(
        or_(
            Producto.codigo_barra.in_(barcodes),
            Producto.codigo_barra_alt1.in_(barcodes),
            Producto.codigo_barra_alt2.in_(barcodes),
            Producto.codigo_barra_alt3.in_(barcodes),
        )
    ).all()

    prod_map = {}
    for p in existing:
        prod_map[p.codigo_barra] = p
        if p.codigo_barra_alt1: prod_map[p.codigo_barra_alt1] = p
        if p.codigo_barra_alt2: prod_map[p.codigo_barra_alt2] = p
        if p.codigo_barra_alt3: prod_map[p.codigo_barra_alt3] = p

    new_prods = []
    for codigo_barra, descripcion, precio_pvp, fecha_compra in items:
        if not codigo_barra:
            continue
        bc = str(codigo_barra).strip()
        prod = prod_map.get(bc)
        if prod:
            if descripcion and not prod.descripcion:
                prod.descripcion = str(descripcion).strip()
            if precio_pvp and float(precio_pvp) > 0:
                prod.precio_pvp = precio_pvp
            if fecha_compra and (not prod.ultima_compra or fecha_compra > prod.ultima_compra):
                prod.ultima_compra = fecha_compra
            prod.actualizado_en = _dt.utcnow()
        else:
            new_prod = Producto(
                codigo_barra=bc,
                descripcion=str(descripcion).strip() if descripcion else '',
                precio_pvp=precio_pvp,
                ultima_compra=fecha_compra,
            )
            new_prods.append(new_prod)
            prod_map[bc] = new_prod
    if new_prods:
        session.add_all(new_prods)
        # Flush para que una llamada subsiguiente vea estos productos en el SELECT
        # y no intente insertarlos de nuevo (evita UNIQUE violation en codigo_barra).
        session.flush()


# ── Normalizador de texto PDF con caracteres cuadruplicados ───────────────
# pdfplumber en ciertas fuentes/layouts (ej. 20 de Junio) cuadruplica cada
# carácter de texto en negrita: "TOTAL" → "TTTTOOOOTTTTAAAALLLL". Detecta
# líneas donde al menos un token es multi-char cuadruplicado y reduce esa
# línea entera con `(.)\1{3} → \1`. No toca líneas normales.
# ── OCR fallback para PDFs escaneados (sin capa de texto) ──────────────────
# Usa pytesseract (con tesseract-ocr + tesseract-ocr-spa instalados en Docker).
# Cachea el resultado en "<pdf_path>.ocr.txt" para no re-procesar.
def _clean_ocr_text(txt):
    """Post-procesa texto OCR: quita espacios y líneas extras."""
    import re as _re
    # 1) Strip por línea
    lines = [l.rstrip() for l in (txt or '').split('\n')]
    out = []
    for l in lines:
        # 2) Colapsar espacios/tabs múltiples dentro de la línea a uno
        l = _re.sub(r'[ \t]+', ' ', l).strip()
        out.append(l)
    # 3) Colapsar 3+ líneas vacías a 1 sola
    result = []
    empty = 0
    for l in out:
        if not l:
            empty += 1
            if empty >= 2:
                continue  # saltar línea vacía extra
        else:
            empty = 0
        result.append(l)
    return '\n'.join(result)


def _preprocess_image_for_ocr(pil_img):
    """Convierte a grayscale + auto-contraste + binarización para mejorar OCR.
    Reduce errores tipo '$→2/3/5' que ocurren cuando Tesseract ve tonos grises
    intermedios en vez de blanco puro y negro puro."""
    try:
        from PIL import ImageOps
        img = pil_img.convert('L')                     # grayscale
        img = ImageOps.autocontrast(img, cutoff=2)     # estirar contraste
        img = img.point(lambda p: 255 if p > 170 else 0, mode='1')  # threshold → B/W
        return img
    except Exception:
        return pil_img  # si falla cualquier cosa, devolvemos la original


def extract_text_with_ocr_fallback(pdf_path, min_chars=50, lang='spa', dpi=400):
    """Intenta extraer texto con pdfplumber. Si el resultado es muy chico
    (PDF escaneado), corre OCR sobre cada página y cachea el resultado.

    Retorna el texto completo (todas las páginas unidas con \\n).
    """
    import os as _os

    import pdfplumber as _pdfplumber
    # 1) Intento rápido con pdfplumber
    try:
        with _pdfplumber.open(pdf_path) as pdf:
            texto_plano = '\n'.join((p.extract_text() or '') for p in pdf.pages)
    except Exception:
        texto_plano = ''
    if len(texto_plano.strip()) >= min_chars:
        return texto_plano

    # 2) Necesitamos OCR. Chequear cache.
    cache_path = pdf_path + '.ocr.txt'
    if _os.path.isfile(cache_path):
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                cached = f.read()
            if cached.strip():
                return cached
        except OSError:
            pass

    # 3) Correr OCR página por página
    try:
        import pytesseract
    except ImportError:
        return texto_plano  # sin OCR disponible, devolvemos lo que haya

    try:
        paginas = []
        with _pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                img = page.to_image(resolution=dpi).original
                img = _preprocess_image_for_ocr(img)  # grayscale + binarización
                # PSM 6: bloque uniforme de texto (mejor para facturas)
                txt = pytesseract.image_to_string(img, lang=lang, config='--psm 6') or ''
                paginas.append(txt)
        texto_ocr = _clean_ocr_text('\n'.join(paginas))
    except Exception:
        return texto_plano

    # 4) Guardar cache
    try:
        with open(cache_path, 'w', encoding='utf-8') as f:
            f.write(texto_ocr)
    except OSError:
        pass
    return texto_ocr


def _normalize_quadrupled(text):
    import re as _re
    out_lines = []
    for line in text.split('\n'):
        hit = False
        # Detectar si la línea tiene tokens cuadruplicados (TTTTOOOO...) o un
        # run largo de tokens cortos (letter-spacing "( G ) G r a v a d o...").
        short_run = 0
        max_short_run = 0
        for tok in line.split():
            if len(tok) < 8 or len(tok) % 4 != 0:
                pass
            else:
                chunks = [tok[i:i+4] for i in range(0, len(tok), 4)]
                if all(len(set(c)) == 1 for c in chunks) and len({c[0] for c in chunks}) >= 2:
                    hit = True
            if len(tok) <= 2:
                short_run += 1
                if short_run > max_short_run:
                    max_short_run = short_run
            else:
                short_run = 0
        if max_short_run >= 10:
            hit = True  # letter-spacing significativo
        if hit:
            # 1) Reducir cuadruplicados: TTTTOOOO → TO
            line = _re.sub(r'(.)\1{3}', r'\1', line)
            # 2) Colapsar runs de ≥5 tokens cortos (letter-spacing):
            #    `G r a v a d o` → `Gravado`
            tokens = line.split(' ')
            result, run = [], []
            def _flush(res, r):
                if len(r) >= 5:
                    res.append(''.join(r))
                else:
                    res.extend(r)
            for t in tokens:
                if len(t) <= 2:
                    run.append(t)
                else:
                    _flush(result, run); run = []
                    result.append(t)
            _flush(result, run)
            line = ' '.join(result)
        # 3) Colapsar rellenos de puntos (≥4 puntos seguidos) → espacio simple
        #    `Subtotal Bruto................................$ 1.409.334,86` → `Subtotal Bruto $ 1.409.334,86`
        #    Aplica siempre (no solo en líneas hit), es un artefacto común.
        line = _re.sub(r'\.{4,}', ' ', line)
        out_lines.append(line)
    return '\n'.join(out_lines)


# ── Pattern builder (used by invoices + converter) ──────────────────────────

def _build_item_pattern(example_line, selections):
    """Build regex pattern from example_line + selections.
    Content after the first newline in example_line is wrapped in an optional group,
    so rows that fit on a single line still match."""
    import re as _re
    sel = sorted(selections, key=lambda s: s.get('start', 0))

    def _kind(txt):
        t = (txt or '').strip()
        if _re.fullmatch(r'-?\d[\d.,]*', t):
            return r'[\d.,]+'
        if _re.fullmatch(r'\d+', t):
            return r'\d+'
        if _re.fullmatch(r'\S+', t):
            return r'\S+'
        return r'.+?'

    def _norm_literal(s):
        result = ''
        for tok in _re.findall(r'\s+|\S+', s):
            if tok.isspace():
                result += r'\s+'
            else:
                result += _re.escape(tok)
        return result

    first_nl = example_line.find('\n')
    pattern_main = '^'
    pattern_opt = ''
    cursor = 0
    fields = []
    prev_cap = None
    opened_opt = False

    for s in sel:
        start = int(s.get('start', 0))
        end   = int(s.get('end', start))
        literal = example_line[cursor:start]
        cap = _kind(example_line[start:end])

        in_opt_zone = first_nl >= 0 and start >= first_nl

        if in_opt_zone and not opened_opt:
            if first_nl > cursor:
                pre = example_line[cursor:first_nl]
                if pre:
                    pattern_main += _norm_literal(pre)
            pattern_opt = r'(?:'
            rest = example_line[max(cursor, first_nl):start]
            if rest:
                pattern_opt += _norm_literal(rest)
            opened_opt = True
        else:
            target = pattern_opt if opened_opt else pattern_main
            if literal:
                norm = _norm_literal(literal)
                if literal.strip() == '' and prev_cap == r'.+?' and cap in (r'[\d.,]+', r'\d+', r'\S+'):
                    norm = r'\s*'
                if opened_opt:
                    pattern_opt += norm
                else:
                    pattern_main += norm
            elif prev_cap is not None and cap in (r'[\d.,]+', r'\d+', r'\S+') \
                    and prev_cap in (r'[\d.,]+', r'\d+', r'\S+'):
                # Dos capturas del mismo tipo sin literal entre ellas → sin un
                # separador explícito, la primera greedy roba caracteres de la
                # segunda. Forzamos \s+ para evitar el bug.
                if opened_opt:
                    pattern_opt += r'\s+'
                else:
                    pattern_main += r'\s+'

        if opened_opt:
            pattern_opt += '(' + cap + ')'
        else:
            pattern_main += '(' + cap + ')'
        fields.append(s.get('field'))
        prev_cap = cap
        cursor = end

    tail = example_line[cursor:]
    if tail.strip():
        if first_nl >= cursor and not opened_opt:
            pre = example_line[cursor:first_nl]
            if pre:
                pattern_main += _norm_literal(pre)
            rest = example_line[first_nl:]
            if rest.strip():
                pattern_opt = r'(?:' + _norm_literal(rest)
                opened_opt = True
        else:
            if opened_opt:
                pattern_opt += _norm_literal(tail)
            else:
                pattern_main += _norm_literal(tail)

    pattern = pattern_main + (pattern_opt + r')?' if opened_opt else '')
    # Anclar al fin de línea para evitar que el último [\d.,]+ absorba
    # el primer número de la línea siguiente (bug de PHARMAMERICAN:
    # precio_unit capturaba el EAN de la fila de abajo).
    pattern += r'\s*$'

    def _base(name):
        return _re.sub(r'_\d+$', '', name or '')

    base_fields = []
    for f in fields:
        b = _base(f)
        if b not in base_fields:
            base_fields.append(b)

    return pattern, fields, base_fields, _base
