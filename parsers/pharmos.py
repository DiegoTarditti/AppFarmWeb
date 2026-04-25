"""
Parser para: PHARMOS S.A.
CUIT: 30-64266156-2

Formato de factura PDF:
  Columnas: REF | DESCRIPCION | (DESC_CODE) | [IVA] | CANT | PREC.UNIT | IMP.BRUTO
  - REF: código interno tipo '79-65', '80-272' (NO es código de barras)
  - IVA (21,00) es opcional — aparece en algunos ítems gravados
  - Las líneas de bonificación negativas comienzan con '(11) BONIFICACION' — se descartan

Nota: el PDF no incluye código de barras. Se usa el código interno como 'codigo_barra'.
El cruce con el ERP se realiza por descripción normalizada o por mappings manuales.
"""
import re
from datetime import datetime

import pdfplumber


def parse_invoice_pdf(pdf_path):
    def to_float(s):
        """'1.234,5600' → 1234.56"""
        return float(s.replace('.', '').replace(',', '.'))

    pages_text = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            pages_text.append(page.extract_text() or '')
    full_text = '\n'.join(pages_text)

    # --- Cabecera ---
    # Número: aparece al final de la línea de fecha → "FECHA: 24/02/2026 0142-00964164"
    numero_m = re.search(r'FECHA:\s*\d{2}/\d{2}/\d{4}\s+(\S+)', full_text)
    fecha_m  = re.search(r'FECHA:\s*(\d{2}/\d{2}/\d{4})', full_text)
    cuit_m   = re.search(r'C\.U\.I\.T\.:\s*([\d-]+)', full_text)
    total_m  = re.search(r'^TOTAL\s+([\d.]+,\d{2})\s*$', full_text, re.MULTILINE)

    numero_factura = numero_m.group(1) if numero_m else 'SIN_NUMERO'
    fecha          = (datetime.strptime(fecha_m.group(1), '%d/%m/%Y').date()
                      if fecha_m else datetime.today().date())
    proveedor_cuit = cuit_m.group(1) if cuit_m else '30-64266156-2'
    total          = to_float(total_m.group(1)) if total_m else 0

    # --- Ítems ---
    # Formato: REF DESCRIPCION [(DESC)] [IVA] CANT PREC.UNIT(4dec) IMPORTE(2dec)
    # Ejemplos:
    #   79-65  DELTACORT X 10 COMPRIMIDOS (4) 4 5.517,2400 22.068,96
    #   79-1208 PITIRIAX CHAMPU X 100 ML. (4) 21,00 6 6.212,6000 37.275,60
    #   80-2234 MICOSEP NF CREMA DERMICA X 30 GRS. 8 5.813,7900 46.510,32
    item_re = re.compile(
        r'^(\d{2}-\d+)\s+(.+?)\s+'         # ref  descripcion
        r'(?:\(\d+\)\s+)?'                  # opcional: (DESC_CODE)
        r'(?:\d+,\d{2}\s+)?'               # opcional: IVA (21,00)
        r'(\d+)\s+'                         # cantidad (entero)
        r'([\d.]+,\d{2,4})\s+'             # precio unitario
        r'([\d.]+,\d{2})\s*$',             # importe
        re.MULTILINE
    )

    items = []
    for m in item_re.finditer(full_text):
        ref         = m.group(1)
        descripcion = m.group(2).strip()
        cantidad    = int(m.group(3))
        precio_unit = to_float(m.group(4))
        importe     = to_float(m.group(5))

        # Saltear líneas que son resúmenes, no productos
        if descripcion.upper().startswith(('TOTAL', 'TRANSPORTE', 'SUBTOTAL')):
            continue

        items.append({
            'codigo_barra':    ref,      # código interno — sin barcode real en PDF
            'cantidad':        cantidad,
            'descripcion':     descripcion,
            'precio_unitario': precio_unit,
            'dto':             None,
            'importe':         importe,
        })

    return {
        'numero_factura':      numero_factura,
        'fecha':               fecha,
        'proveedor_razon':     'PHARMOS S.A.',
        'proveedor_cuit':      proveedor_cuit,
        'proveedor_domicilio': None,
        'cliente_codigo':      None,
        'cliente_razon':       None,
        'total':               total,
        'total_articulos':     len(items),
        'total_unidades':      sum(i['cantidad'] for i in items),
        'items':               items,
    }
