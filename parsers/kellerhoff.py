"""
Parser para: DROGUERÍA KELLERHOFF S.A.
CUIT: 30539756490

Formato: factura multi-hoja con tabla de ítems.
Columnas: Código Barra | Cant. | Descripción | Precio Público | % Dto. | Precio Unitario | Importe
"""
import re
from datetime import datetime

import pdfplumber


def parse_invoice_pdf(pdf_path):
    def to_float(s):
        """'1.234,56' -> 1234.56"""
        return float(s.replace('.', '').replace(',', '.'))

    pages_text = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            pages_text.append(page.extract_text() or '')
    full_text = '\n'.join(pages_text)

    # --- Cabecera ---
    numero_m  = re.search(r'FACTURA\s+N[º°]:\s*(\S+)', full_text)
    fecha_m   = re.search(r'FECHA:\s*(\d{2}/\d{2}/\d{4})', full_text)
    cuit_m    = re.search(r'CUIT:\s*(\d[\d-]+)', full_text)
    razon_m   = re.search(
        r'^([A-ZÁÉÍÓÚÑÜ][A-ZÁÉÍÓÚÑÜ &]+(?:S\.A\.|S\.R\.L\.|S\.A\.S\.|LTDA\.|S\.C\.))',
        full_text, re.MULTILINE
    )
    cliente_m = re.search(r'Cliente:\s*(\d+)\s*-\s*(.+)', full_text)
    # Pie de página: "N/N  total_unidades  exento  gravado  iva  perc  TOTAL"
    footer_m  = re.search(
        r'^\d+/\d+\s+(\d+)\s+[\d.,]+\s+[\d.,]+\s+[\d.,]+\s+[\d.,]+\s+([\d.,]+)\s*$',
        full_text, re.MULTILINE
    )

    numero_factura  = numero_m.group(1) if numero_m else 'SIN_NUMERO'
    fecha = (datetime.strptime(fecha_m.group(1), '%d/%m/%Y').date()
             if fecha_m else datetime.today().date())
    proveedor_cuit  = cuit_m.group(1) if cuit_m else None
    proveedor_razon = razon_m.group(1).strip() if razon_m else None
    cliente_codigo  = cliente_m.group(1) if cliente_m else None
    cliente_razon   = cliente_m.group(2).strip() if cliente_m else None
    total_unidades  = int(footer_m.group(1)) if footer_m else None
    total           = to_float(footer_m.group(2)) if footer_m else 0

    # --- Items ---
    # Línea normal: barcode cant descripcion [WEB|TRZ] precio_pub %dto precio_unit importe
    item_re = re.compile(
        r'^(\d{7,14})\s+(\d+)\s+(.+?)\s+([\d.]+,\d{2})\s+([\d,]+)\s+([\d.]+,\d{2})\s+([\d.]+,\d{2})\s*$',
        re.MULTILINE
    )
    # Productos gravados: solo tienen importe (sin precio público ni %dto)
    gravado_re = re.compile(
        r'^(\d{7,14})\s+(\d+)\s+(.+?)\s+([\d.]+,\d{2})\s*$',
        re.MULTILINE
    )

    items = []
    matched_spans = set()

    for m in item_re.finditer(full_text):
        matched_spans.add(m.start())
        descripcion = re.sub(r'\s+(WEB|TRZ)\s*$', '', m.group(3)).strip()
        items.append({
            'codigo_barra':   m.group(1),
            'cantidad':       int(m.group(2)),
            'descripcion':    descripcion,
            'precio_unitario': to_float(m.group(6)),
            'importe':        to_float(m.group(7)),
        })

    for m in gravado_re.finditer(full_text):
        if m.start() in matched_spans:
            continue
        descripcion = re.sub(r'\s+(WEB|TRZ)\s*$', '', m.group(3)).strip()
        items.append({
            'codigo_barra':   m.group(1),
            'cantidad':       int(m.group(2)),
            'descripcion':    descripcion,
            'precio_unitario': None,
            'importe':        to_float(m.group(4)),
        })

    return {
        'numero_factura':     numero_factura,
        'fecha':              fecha,
        'proveedor_razon':    proveedor_razon,
        'proveedor_cuit':     proveedor_cuit,
        'proveedor_domicilio': None,
        'cliente_codigo':     cliente_codigo,
        'cliente_razon':      cliente_razon,
        'total':              total,
        'total_articulos':    len(items),
        'total_unidades':     total_unidades,
        'items':              items,
    }
