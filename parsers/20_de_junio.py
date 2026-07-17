"""
Parser para: Droguería 20 de Junio
CUIT: 23-17460511-4

Formato de línea de ítem:
  CANT  DESC  LABO  [OBS]  BARCODE  SUGG  [DTO  NETO]  [FLAG]  IMPORTE
  - SUGG y FARMACIA sin puntos de miles (ej: 34338,97)
  - IMPORTE con puntos de miles (ej: 34.338,97)
"""
import re
from datetime import datetime

from helpers import _normalize_quadrupled, extract_text_with_ocr_fallback


def parse_invoice_pdf(pdf_path):
    def to_float(s):
        return float(s.replace('.', '').replace(',', '.'))

    # Mismo pipeline que los parsers que genera /converter: OCR fallback si el PDF
    # viene escaneado (pdfplumber devuelve vacío y la factura no se podía importar
    # de ninguna forma) + limpieza de los artefactos de pdfplumber. Acá el artefacto
    # es la fuente bold, que sale con cada carácter x4.
    full_text = _normalize_quadrupled(extract_text_with_ocr_fallback(pdf_path))

    # Número de factura: "0011-19376868 / 01"
    numero_m = re.search(r'(\d{4}-\d{8})\s*/', full_text)
    numero_factura = numero_m.group(1) if numero_m else 'SIN_NUMERO'

    # Fecha
    fecha_m = re.search(r'(\d{2}/\d{2}/\d{4})', full_text)
    fecha = (datetime.strptime(fecha_m.group(1), '%d/%m/%Y').date()
             if fecha_m else datetime.today().date())

    # CUIT: "Res.Insc.01 23-17460511-4"
    cuit_m = re.search(r'Res\.Insc\.\d+\s+([\d-]+)', full_text)
    cuit = cuit_m.group(1) if cuit_m else '23-17460511-4'

    # Total: "TOTAL NETO $ 1.063.343,51".
    # Esta línea sale cuadruplicada del PDF ("TTTTOOOOTTTTAAAALLLL ... $$$$
    # 1111....000066663333..."). Antes se leía del texto crudo y se rearmaba a mano
    # tomando cada 4º carácter, lo que además exigía que el largo fuera múltiplo de 4
    # (si no, total = 0 en silencio). _normalize_quadrupled ya deja la línea legible.
    total = 0
    total_m = re.search(r'TOTAL\s*NETO.*?\$\s*([\d.,]+)', full_text)
    if total_m:
        try:
            total = to_float(total_m.group(1))
        except ValueError:
            total = 0

    # Regex de ítems: ancla en el código de barras (7-14 dígitos)
    # Antes: cantidad + descripción + labo + obs opcional
    # Después: campos de precio → el importe siempre lleva punto de miles (X.XXX,XX)
    item_re = re.compile(
        r'^(\d+)\s+'         # 1: cantidad
        r'(.+?)\s+'          # 2: descripcion + labo (+ obs)
        r'(\d{7,14})'        # 3: código de barra
        r'(.*?)$',           # 4: campos de precio
        re.MULTILINE
    )

    # Todos los números con coma decimal en el tail (precios y porcentajes)
    price_re = re.compile(r'[\d]+(?:\.[\d]+)*,\d{2}')

    items = []
    for m in item_re.finditer(full_text):
        cant_str, pre_barcode, barcode, prices_str = m.groups()

        numbers = price_re.findall(prices_str)
        if not numbers:
            continue  # línea sin precios, ignorar

        # precio_unitario = penúltimo número (neto o farmacia); importe = último
        importe = to_float(numbers[-1])
        precio_unitario = to_float(numbers[-2]) if len(numbers) >= 2 else to_float(numbers[0])

        # % dto: si hay ≥ 4 números es [sugg, dto, neto, importe]
        dto = None
        if len(numbers) >= 4:
            try:
                dto = to_float(numbers[-3])
            except ValueError:
                pass

        # Limpiar descripción: quitar labo (3-4 letras mayúsculas al final) y obs (=)
        desc = pre_barcode.strip()
        desc = re.sub(r'\s+[A-Z]{3,4}\s*=?\s*$', '', desc).strip()
        desc = re.sub(r'\s+=\s*$', '', desc).strip()

        items.append({
            'codigo_barra':    barcode,
            'cantidad':        int(cant_str),
            'descripcion':     desc,
            'precio_unitario': precio_unitario,
            'dto':             dto,
            'importe':         importe,
        })

    return {
        'numero_factura':      numero_factura,
        'fecha':               fecha,
        'proveedor_razon':     '20 de Junio',
        'proveedor_cuit':      cuit,
        'proveedor_domicilio': 'Pte. Roca 1553, Rosario, Santa Fe',
        'cliente_codigo':      None,
        'cliente_razon':       None,
        'total':               total,
        'total_articulos':     len(items),
        'items':               items,
    }
