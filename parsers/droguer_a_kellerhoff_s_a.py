"""Parser auto-generado para: DROGUERÍA KELLERHOFF S.A.
CUIT: —

Creado desde el modo aprendizaje del conversor.
Si el layout del proveedor cambia, reentrenar el patrón desde /converter.
"""
import re
import pdfplumber
from datetime import datetime
from helpers import _normalize_quadrupled


PATTERN = r"""^([\d.,]+)\s+([\d.,]+)\s+(.+?)\s*([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s*$"""
FIELDS = ['codigo_barra', 'cantidad', 'descripcion', 'precio_publico', 'dto', 'precio_unitario', 'importe']

# Patrón secundario para la sección "PRODUCTOS GRAVADOS" (5 columnas: sin pub/dto)
#   ean  cant  descripcion  precio_unit  importe
PATTERN_GRAVADOS = r"""^(\d{7,14})\s+(\d+)\s+(.+?)\s+([\d.]+,\d{2})\s+([\d.]+,\d{2})\s*$"""

# Marcadores de sección a cortar
SECTION_CUT_MARKERS = [
    r'\*\*\*\s*PRODUCTOS\s+EN\s+FALTA',     # sin stock — NO son items reales
]


def _to_float(s):
    """Convierte formato argentino '1.234,56' a float 1234.56."""
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    try:
        return float(s.replace('.', '').replace(',', '.'))
    except Exception:
        return None


def _to_int(s):
    try:
        return int(float(str(s).replace('.', '').replace(',', '.')))
    except Exception:
        return 0


def parse_invoice_pdf(pdf_path):
    pages_text = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            pages_text.append(_normalize_quadrupled(page.extract_text() or ''))
    full_text = '\n'.join(pages_text)

    # Encabezado genérico
    numero_m = re.search(r'(?:FACTURA|REMITO|N[º°])\s*[:\s]*(\S+)', full_text)
    fecha_m = re.search(r'(?:FECHA|Fecha)[:\s]*(\d{2}/\d{2}/\d{4})', full_text)
    numero_factura = numero_m.group(1) if numero_m else 'SIN_NUMERO'
    fecha = (datetime.strptime(fecha_m.group(1), '%d/%m/%Y').date()
             if fecha_m else datetime.today().date())

    # Cortar el texto en el primer marcador de sección "sin stock / faltantes"
    # para que esas líneas no se parseen como items reales.
    items_text = full_text
    for marker in SECTION_CUT_MARKERS:
        m = re.search(marker, items_text)
        if m:
            items_text = items_text[:m.start()]

    # Ítems desde el patrón aprendido (7 columnas con pub/dto)
    rx = re.compile(PATTERN, re.MULTILINE)
    items = []
    matched_spans = set()
    for m in rx.finditer(items_text):
        matched_spans.add(m.start())
        row = {}
        for i, f in enumerate(FIELDS):
            base = f.rstrip('0123456789_')
            val = m.group(i + 1) or ''
            row.setdefault(base, []).append(val)
        joined = {b: re.sub(r'\s+', ' ', ' '.join(v).strip()) for b, v in row.items()}
        items.append({
            'codigo_barra': joined.get('codigo_barra', ''),
            'cantidad': _to_int(joined.get('cantidad', 0)),
            'descripcion': joined.get('descripcion', ''),
            'precio_publico': _to_float(joined.get('precio_publico')),
            'dto': _to_float(joined.get('dto')),
            'precio_unitario': _to_float(joined.get('precio_unitario')),
            'importe': _to_float(joined.get('importe')) or 0,
        })

    # Ítems de la sección "PRODUCTOS GRAVADOS" (5 columnas, sin pub/dto)
    rx_grav = re.compile(PATTERN_GRAVADOS, re.MULTILINE)
    for m in rx_grav.finditer(items_text):
        if m.start() in matched_spans:
            continue
        items.append({
            'codigo_barra':    m.group(1),
            'cantidad':        _to_int(m.group(2)),
            'descripcion':     re.sub(r'\s+(WEB|TRZ)\s*$', '', m.group(3)).strip(),
            'precio_publico':  None,
            'dto':             None,
            'precio_unitario': _to_float(m.group(4)),
            'importe':         _to_float(m.group(5)) or 0,
        })

    total_items = sum((it.get('importe') or 0) for it in items)

    # Pie: "Hoja  Cant  Exento  Gravado  IVA_Inscrip  [Percep_IVA]  Percepciones  TOTAL"
    # Percep_IVA es opcional — pdfplumber colapsa la columna si está vacía.
    footer_m = re.search(
        r'^\d+/\d+\s+(\d+)'                  # 1: cant un
        r'\s+([\d.,]+)'                      # 2: monto exento
        r'\s+([\d.,]+)'                      # 3: monto gravado
        r'\s+([\d.,]+)'                      # 4: iva inscrip (10,5 o 21)
        r'(?:\s+([\d.,]+))?'                 # 5: percepción iva (opcional)
        r'\s+([\d.,]+)'                      # 6: percepciones
        r'\s+([\d.,]+)\s*$',                 # 7: TOTAL
        full_text, re.MULTILINE
    )
    total_unidades = monto_exento = monto_gravado = iva = percepciones = None
    total = total_items
    if footer_m:
        total_unidades = int(footer_m.group(1))
        monto_exento   = _to_float(footer_m.group(2))
        monto_gravado  = _to_float(footer_m.group(3))
        iva            = _to_float(footer_m.group(4))
        perc_iva       = _to_float(footer_m.group(5)) or 0
        percepciones   = (_to_float(footer_m.group(6)) or 0) + perc_iva
        total          = _to_float(footer_m.group(7)) or total_items

    return {
        'numero_factura': numero_factura,
        'fecha': fecha,
        'proveedor_razon': 'DROGUERÍA KELLERHOFF S.A.',
        'proveedor_cuit': None,
        'proveedor_domicilio': None,
        'cliente_codigo': None,
        'cliente_razon': None,
        'total': total,
        'total_articulos': len(items),
        'total_unidades': total_unidades,
        'monto_exento': monto_exento,
        'monto_gravado': monto_gravado,
        'iva': iva,
        'percepciones': percepciones,
        'items': items,
    }
