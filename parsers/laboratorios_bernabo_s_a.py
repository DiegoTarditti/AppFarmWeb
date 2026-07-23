"""
Parser para: LABORATORIOS BERNABO S.A.
CUIT: 30-50054729-0

Formato PDF multi-columna:
  CÓDIGO  CONCEPTO              LOTE        CANTIDAD  PCIO.UNIT  DESCTO   IMPORTE
  1134O   PERPIEL AGUA...       0000015203  12 UN     8.430,31   30,00%   101.163,72

Usa extract_words() para reconstruir filas por posición Y.
CÓDIGO interno (ej: 1134O) → se usa como codigo_barra.
match_strategy del proveedor: 'descripcion'.
"""
import re
from collections import defaultdict
from datetime import datetime

import pdfplumber

from helpers import _normalize_quadrupled


def parse_invoice_pdf(pdf_path):
    def to_float(s):
        return float(s.replace('.', '').replace(',', '.'))

    # ── Extraer encabezado con texto plano ────────────────────────────────────
    # Sin OCR fallback a propósito, a diferencia del resto de los parsers: acá los
    # ítems salen de las COORDENADAS de las palabras (layout multi-columna), y el OCR
    # devuelve texto plano, no coordenadas. En un PDF escaneado igual no habría ítems,
    # así que enchufarlo daría encabezado sin ítems — la misma factura fallida, con
    # más ruido. _normalize_quadrupled sí aplica: limpia artefactos del encabezado.
    pages_text = []
    all_words_by_page = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            pages_text.append(_normalize_quadrupled(page.extract_text() or ''))
            all_words_by_page.append(
                page.extract_words(x_tolerance=3, y_tolerance=3) or []
            )
    full_text = '\n'.join(pages_text)

    # Número de factura
    numero_m = re.search(r'FACTURA\s+(\d{4}-\d{8})', full_text)
    numero_factura = numero_m.group(1) if numero_m else 'SIN_NUMERO'

    # Fecha
    fecha_m = re.search(r'(\d{2}/\d{2}/\d{4})', full_text)
    fecha = (datetime.strptime(fecha_m.group(1), '%d/%m/%Y').date()
             if fecha_m else datetime.today().date())

    # CUIT
    cuit_m = re.search(r'C\.U\.I\.T\.:\s*([\d\-]+)', full_text)
    cuit = cuit_m.group(1) if cuit_m else '30-50054729-0'

    # Total final
    totales = re.findall(r'TOTAL\s+\$\s*([\d.]+,\d{2})', full_text)
    total = to_float(totales[-1]) if totales else 0.0

    # ── Reconstruir filas por posición Y ──────────────────────────────────────
    # Agrupa palabras que comparten la misma Y (±4px) → fila visual completa
    CODIGO_RE   = re.compile(r'^\d{2,6}[A-Z]$')          # ej: 1134O
    LOTE_RE     = re.compile(r'^0{3,4}\d{6,7}$')          # ej: 0000015203
    IMPORTE_RE  = re.compile(r'^\d{1,3}(?:\.\d{3})*,\d{2}$')  # ej: 101.163,72

    # Juntamos todas las palabras de todas las páginas
    all_words = []
    for words in all_words_by_page:
        all_words.extend(words)

    # Agrupar por Y redondeado
    rows = defaultdict(list)
    for w in all_words:
        y_key = round(w['top'] / 4) * 4
        rows[y_key].append(w)

    # Ordenar filas por Y y palabras dentro de cada fila por X
    sorted_rows = []
    for y in sorted(rows.keys()):
        row_words = sorted(rows[y], key=lambda w: w['x0'])
        sorted_rows.append([w['text'] for w in row_words])

    # ── Parsear ítems ─────────────────────────────────────────────────────────
    # Cada ítem puede ocupar 1-3 filas visuales. Busco filas que empiezan con CÓDIGO.
    # Luego busco en esa misma fila (o las siguientes agrupadas) el LOTE, CANT, PRECIO, DESCTO, IMPORTE.

    items = []
    i = 0
    while i < len(sorted_rows):
        row = sorted_rows[i]
        if not row:
            i += 1
            continue

        # Busco fila que empiece con un CÓDIGO de ítem
        if not CODIGO_RE.match(row[0]):
            i += 1
            continue

        # Tomo esta fila y la siguiente (puede haber overflow de descripción)
        merged = list(row)
        if i + 1 < len(sorted_rows):
            next_row = sorted_rows[i + 1]
            # Agrego la siguiente fila si NO empieza con otro CÓDIGO
            if next_row and not CODIGO_RE.match(next_row[0]):
                merged = merged + next_row
                i += 1

        # Identificar LOTE — puede ser palabra exacta o pegado al final de otra
        LOTE_EMBEDDED = re.compile(r'(0{3,4}\d{6,7})$')
        lote_idx, lote = None, None
        for j, t in enumerate(merged):
            if LOTE_RE.match(t):
                lote_idx, lote = j, t
                merged[j] = ''   # vaciar para que no se agregue a descripción
                break
            m = LOTE_EMBEDDED.search(t)
            if m:
                lote = m.group(1)
                lote_idx = j
                merged[j] = t[:t.rfind(lote)].rstrip()  # recortar lote del token
                break

        if lote_idx is None:
            i += 1
            continue

        # Descripción: todo entre CÓDIGO y LOTE
        codigo = merged[0]
        desc   = ' '.join(merged[1:lote_idx]).strip()
        if merged[lote_idx]:  # si quedó algo tras recortar el lote embebido
            desc = (desc + ' ' + merged[lote_idx]).strip()

        # Después del LOTE: CANT UN PRECIO DESCTO% IMPORTE
        rest = merged[lote_idx + 1:]

        # Encontrar "UN" para anclar cantidad
        try:
            un_idx = next(j for j, t in enumerate(rest) if t == 'UN')
        except StopIteration:
            i += 1
            continue

        cant_str = rest[un_idx - 1] if un_idx > 0 else '0'

        # Importes después de UN: precio, descto(sin%), importe
        numeros = [t for t in rest[un_idx + 1:] if IMPORTE_RE.match(t)]
        if len(numeros) < 2:
            i += 1
            continue

        # precio=primero, descto=segundo (valor sin %), importe=último
        precio  = to_float(numeros[0])
        dto_str = numeros[1] if len(numeros) >= 3 else '0'
        importe = to_float(numeros[-1])

        try:
            cant = int(cant_str)
            dto  = to_float(dto_str)
        except ValueError:
            i += 1
            continue

        items.append({
            'codigo_barra':    codigo,
            'cantidad':        cant,
            'descripcion':     desc,
            'precio_unitario': precio,
            'dto':             dto,
            'importe':         importe,
            'lote':            lote,
        })
        i += 1

    return {
        'numero_factura':      numero_factura,
        'fecha':               fecha,
        'proveedor_razon':     'LABORATORIOS BERNABO S.A.',
        'proveedor_cuit':      cuit,
        'proveedor_domicilio': 'Terrada 2346/48 - (1416) CABA',
        'cliente_codigo':      None,
        'cliente_razon':       None,
        'total':               total,
        'total_articulos':     len(items),
        'items':               items,
    }
