"""
Parser para: {{RAZON_SOCIAL}}
CUIT: {{CUIT}}

Generado automáticamente como plantilla base.
Adaptar las expresiones regulares al formato exacto del PDF de este proveedor.

Cada ítem debe devolver un dict con:
  codigo_barra, cantidad, descripcion, precio_unitario (puede ser None), importe
"""
import re
import pdfplumber
from datetime import datetime


def parse_invoice_pdf(pdf_path):
    def to_float(s):
        """Convierte formato argentino '1.234,56' a float 1234.56"""
        return float(s.replace('.', '').replace(',', '.'))

    pages_text = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            pages_text.append(page.extract_text() or '')
    full_text = '\n'.join(pages_text)

    # TODO: Adaptar estas expresiones al encabezado del PDF del proveedor
    numero_m = re.search(r'(?:FACTURA|REMITO|N[º°])\s*[:\s]*(\S+)', full_text)
    fecha_m  = re.search(r'FECHA:\s*(\d{2}/\d{2}/\d{4})', full_text)

    numero_factura = numero_m.group(1) if numero_m else 'SIN_NUMERO'
    fecha = (datetime.strptime(fecha_m.group(1), '%d/%m/%Y').date()
             if fecha_m else datetime.today().date())

    # TODO: Adaptar el regex al formato de líneas de ítem de este proveedor
    # Ejemplo genérico: barcode cant descripcion precio_unit importe
    item_re = re.compile(
        r'^(\d{7,14})\s+(\d+)\s+(.+?)\s+([\d.]+,\d{2})\s+([\d.]+,\d{2})\s*$',
        re.MULTILINE
    )

    items = []
    for m in item_re.finditer(full_text):
        items.append({
            'codigo_barra':    m.group(1),
            'cantidad':        int(m.group(2)),
            'descripcion':     m.group(3).strip(),
            'precio_unitario': to_float(m.group(4)),
            'importe':         to_float(m.group(5)),
        })

    # TODO: Extraer total del pie de página si corresponde
    total = 0

    return {
        'numero_factura':      numero_factura,
        'fecha':               fecha,
        'proveedor_razon':     '{{RAZON_SOCIAL}}',
        'proveedor_cuit':      '{{CUIT}}',
        'proveedor_domicilio': None,
        'cliente_codigo':      None,
        'cliente_razon':       None,
        'total':               total,
        'total_articulos':     len(items),
        'items':               items,
    }
