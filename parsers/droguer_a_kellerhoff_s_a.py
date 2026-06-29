"""Parser auto-generado para: DROGUERÍA KELLERHOFF S.A
CUIT: 30-53975649-0

Creado desde el modo aprendizaje del conversor.
Si el layout del proveedor cambia, reentrenar el patrón desde /converter.
"""
import re
from datetime import datetime

import pdfplumber

from helpers import _normalize_quadrupled, extract_text_with_ocr_fallback

PATTERN = r"""^([\d.,]+)\s+([\d.,]+)\s+(.+?)\s*([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s*$"""
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
    # OCR fallback si el PDF no tiene capa de texto (scanned).
    full_text = _normalize_quadrupled(extract_text_with_ocr_fallback(pdf_path))

    # Encabezado genérico
    numero_m = re.search(r'(?:FACTURA|REMITO|N[º°])\s*[:\s]*(\S+)', full_text)
    fecha_m = re.search(r'(?:FECHA|Fecha)[:\s]*(\d{2}/\d{2}/\d{4})', full_text)
    numero_factura = numero_m.group(1) if numero_m else 'SIN_NUMERO'
    fecha = (datetime.strptime(fecha_m.group(1), '%d/%m/%Y').date()
             if fecha_m else datetime.today().date())

    # Ítems desde el patrón aprendido (7 columnas: barcode, cant, desc,
    # precio_publico, dto, precio_unitario, importe).
    rx = re.compile(PATTERN, re.MULTILINE)
    items = []
    seen_barcodes = set()
    for m in rx.finditer(full_text):
        row = {}
        for i, f in enumerate(FIELDS):
            base = f.rstrip('0123456789_')
            val = m.group(i + 1) or ''
            row.setdefault(base, []).append(val)
        joined = {b: re.sub(r'\s+', ' ', ' '.join(v).strip()) for b, v in row.items()}
        bc = joined.get('codigo_barra', '')
        seen_barcodes.add(bc)
        items.append({
            'codigo_barra': bc,
            'cantidad': _to_int(joined.get('cantidad', 0)),
            'descripcion': joined.get('descripcion', ''),
            'precio_unitario': _to_float(joined.get('precio_unitario')),
            'importe': _to_float(joined.get('importe')) or 0,
        })

    # Segunda pasada: sección "PRODUCTOS GRAVADOS". Esos ítems traen solo 5
    # columnas (barcode, cant, desc, precio_unitario, importe) — sin Precio
    # Público ni % Dto — así que el patrón de 7 columnas no los toma. Acotada
    # a partir del marcador y con dedup por barcode para no re-capturar filas
    # del cuerpo principal.
    gravados_idx = full_text.upper().find('PRODUCTOS GRAVADOS')
    if gravados_idx != -1:
        rx5 = re.compile(
            r'^([\d.,]+)\s+([\d.,]+)\s+(.+?)\s*([\d.,]+)\s+([\d.,]+)\s*$',
            re.MULTILINE)
        for m in rx5.finditer(full_text[gravados_idx:]):
            bc = m.group(1)
            if bc in seen_barcodes:
                continue
            seen_barcodes.add(bc)
            items.append({
                'codigo_barra': bc,
                'cantidad': _to_int(m.group(2)),
                'descripcion': re.sub(r'\s+', ' ', m.group(3).strip()),
                'precio_unitario': _to_float(m.group(4)),
                'importe': _to_float(m.group(5)) or 0,
            })

    total = sum((it.get('importe') or 0) for it in items)

    return {
        'numero_factura': numero_factura,
        'fecha': fecha,
        'proveedor_razon': 'DROGUERÍA KELLERHOFF S.A',
        'proveedor_cuit': '30-53975649-0',
        'proveedor_domicilio': None,
        'cliente_codigo': None,
        'cliente_razon': None,
        'total': total,
        'total_articulos': len(items),
        'items': items,
    }
