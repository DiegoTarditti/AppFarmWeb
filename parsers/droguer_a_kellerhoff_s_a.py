"""Parser auto-generado para: DROGUERÍA KELLERHOFF S.A.
CUIT: —

Creado desde el modo aprendizaje del conversor.
Si el layout del proveedor cambia, reentrenar el patrón desde /converter.
"""
import re
import pdfplumber
from datetime import datetime


PATTERN = r"""^([\d.,]+)\s+([\d.,]+)\s+(.+?)\s*([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)"""
FIELDS = ['codigo_barra', 'cantidad', 'descripcion', 'precio_publico', 'dto', 'precio_unitario', 'importe']


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
            pages_text.append(page.extract_text() or '')
    full_text = '\n'.join(pages_text)

    # Encabezado genérico
    numero_m = re.search(r'(?:FACTURA|REMITO|N[º°])\s*[:\s]*(\S+)', full_text)
    fecha_m = re.search(r'(?:FECHA|Fecha)[:\s]*(\d{2}/\d{2}/\d{4})', full_text)
    numero_factura = numero_m.group(1) if numero_m else 'SIN_NUMERO'
    fecha = (datetime.strptime(fecha_m.group(1), '%d/%m/%Y').date()
             if fecha_m else datetime.today().date())

    # Ítems desde el patrón aprendido
    rx = re.compile(PATTERN, re.MULTILINE)
    items = []
    for m in rx.finditer(full_text):
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
            'precio_unitario': _to_float(joined.get('precio_unitario')),
            'importe': _to_float(joined.get('importe')) or 0,
        })

    total = sum((it.get('importe') or 0) for it in items)

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
        'items': items,
    }
