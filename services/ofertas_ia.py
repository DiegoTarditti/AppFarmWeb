"""Extracción de catálogos de ofertas a JSON estructurado vía Claude.

Motor paralelo al de facturas (`services/factura_ia.py`): recibe el archivo
(PDF, imagen o XLSX) + API key y devuelve un dict con el MISMO shape que las
funciones `_previsualizar_*` de `routes/ofertas_import.py`:

    { headers, rows, mapping, header_row, count_filas, fuente: 'ia' }

Así el resto del wizard (validación contra catálogo, match manual, commit) NO
cambia: la salida IA entra por el mismo carril que el parser regex/OCR.

PDF y imagen → Vision API (Claude lee el documento). XLSX → se convierte cada
hoja a texto CSV-like y se manda como texto (Vision no soporta XLSX).

Modelo por defecto: Haiku 4.5 (las ofertas son tabulares más simples que las
facturas; si la precisión no alcanza, subir a Sonnet/Opus por el parámetro).
"""
import base64
import json
import mimetypes
import os
import re

MODEL = 'claude-haiku-4-5-20251001'

# Columnas estándar que devolvemos en `headers` (orden = orden visual del wizard).
COLUMNAS_STANDARD = [
    'ean', 'codigo', 'descripcion', 'precio', 'unidades_minima',
    'descuento_psl', 'rentabilidad', 'plazo_pago', 'grupo_id', 'vigencia_hasta',
]

PROMPT = """Sos un extractor de catálogos de OFERTAS de laboratorios / droguerías argentinas. Leé el documento adjunto y devolvé SOLO un JSON (sin markdown, sin texto extra) con esta estructura exacta:

{
  "laboratorio": null,
  "vigencia_hasta": null,
  "items": [
    {
      "ean": null,
      "codigo": null,
      "descripcion": "...",
      "precio": null,
      "unidades_minima": null,
      "descuento_psl": null,
      "rentabilidad": null,
      "plazo_pago": null,
      "grupo_id": null,
      "vigencia_hasta": null
    }
  ]
}

REGLAS:
- Números: punto decimal, SIN separador de miles. El doc usa formato argentino (1.234,56) → convertir a 1234.56.
- Porcentajes (descuento, rentabilidad): número crudo sin el "%". Ej "32,5%" → 32.5.
- Fechas en ISO (YYYY-MM-DD). Si dice "31/12/26" → "2026-12-31".
- NO inventes datos: campo ausente → null. NO omitas filas con datos válidos.
- ean: SOLO si es un código de barras real (8-14 dígitos). El código interno del proveedor (números cortos, alfanuméricos) va en `codigo`. Si dudás, poné el valor en `codigo` y dejá `ean` en null.
- CRÍTICO: copiá ean y codigo EXACTAMENTE como figuran. NO conviertas la letra O en cero 0, ni l/I en 1.
- descripcion: nombre del producto, presentación incluida (ej. "AMOXIDAL 875mg x 14 COMP").
- precio: precio base / PSL / lista (el unitario por unidad mínima si así viene). Ignorá precios tachados.
- unidades_minima: cantidad mínima para acceder a la oferta. Default 1 si no figura.
- descuento_psl: % de descuento sobre PSL. Si hay varios columnas (descuento base + adicional), poné la SUMA.
- rentabilidad: % de rentabilidad sobre PVP (si la columna está). Si no, null.
- plazo_pago: días de plazo (entero). Ej "30 días", "30 d", "30" → 30. Si dice "CTA CTE" o "contado" → null.
- grupo_id: identificador de la línea/familia/módulo si el catálogo agrupa productos (ej. "MOD 1", "GRUPO A", "LÍNEA PEDIATRÍA"). Slug en minúsculas. Si todo el archivo es del mismo grupo o no hay agrupación → null.
- vigencia_hasta del item: si cada fila tiene su vigencia. Si es global del catálogo, poné null por item y completá `vigencia_hasta` arriba.
- laboratorio: nombre del lab si está claro en el encabezado del documento ("Bayer", "Roemmers", etc.). Si no está, null.

CASOS:
- Si una columna agrupa varios descuentos (ej. "% PSL Mod 1", "% Mod 2"), elegí la que aplica a `unidades_minima` de esa fila.
- Si hay productos sin precio (faltantes, comentarios) → NO incluirlos en `items`.
- Multi-página / multi-hoja: concatená TODOS los items.

Devolvé el JSON y nada más."""


def _parse_json(raw):
    """Parsea el texto de Claude a dict, tolerando fences ```json y texto alrededor."""
    if not raw:
        return None
    s = raw.strip()
    m = re.search(r'```(?:json)?\s*(.+?)\s*```', s, re.DOTALL)
    if m:
        s = m.group(1).strip()
    i, j = s.find('{'), s.rfind('}')
    if i >= 0 and j > i:
        s = s[i:j + 1]
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return None


def _xlsx_a_texto(path, max_filas_por_hoja=400):
    """Lee un XLSX/XLS y lo devuelve como texto CSV-like, una sección por hoja.

    Limita a `max_filas_por_hoja` para no inflar el prompt: catálogos típicos
    tienen <200 filas; si es más grande, se trunca con aviso al final.
    """
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    partes = []
    for hoja in wb.worksheets:
        if hoja.sheet_state != 'visible':
            continue
        partes.append(f'=== HOJA: {hoja.title} ===')
        n = 0
        for fila in hoja.iter_rows(values_only=True):
            if not any(c is not None and str(c).strip() for c in fila):
                continue
            partes.append('|'.join('' if c is None else str(c).strip() for c in fila))
            n += 1
            if n >= max_filas_por_hoja:
                partes.append(f'... (hoja truncada en {max_filas_por_hoja} filas)')
                break
        partes.append('')
    wb.close()
    return '\n'.join(partes)


def _llamar_claude(content_parts, api_key, model):
    """Manda el array de content (mixto texto/documento/imagen) a Claude.
    Devuelve (raw_text, usage). Propaga excepciones de la API."""
    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)
    with client.messages.stream(
        model=model,
        max_tokens=16000,
        messages=[{'role': 'user', 'content': content_parts}],
    ) as stream:
        resp = stream.get_final_message()
    raw = ''.join(b.text for b in resp.content if getattr(b, 'type', '') == 'text').strip()
    return raw, resp.usage


def extraer(path, ext, api_key=None, model=MODEL):
    """Extrae items de oferta de un archivo. Devuelve dict con shape de preview.

    ext: '.pdf' | '.xlsx' | '.xls' | '.jpg' | '.jpeg' | '.png' | '.webp' | etc.
    api_key: ANTHROPIC_API_KEY (si None, la toma del env).
    """
    api_key = api_key or os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        raise RuntimeError('ANTHROPIC_API_KEY no configurada')

    ext = (ext or '').lower()
    with open(path, 'rb') as fh:
        data_bytes = fh.read()

    if ext == '.pdf':
        content = [
            {'type': 'document',
             'source': {'type': 'base64', 'media_type': 'application/pdf',
                        'data': base64.standard_b64encode(data_bytes).decode('utf-8')}},
            {'type': 'text', 'text': PROMPT},
        ]
    elif ext in ('.xlsx', '.xls'):
        texto = _xlsx_a_texto(path)
        content = [
            {'type': 'text',
             'text': f'CONTENIDO DEL ARCHIVO XLSX (separador "|"):\n\n{texto}\n\n---\n\n{PROMPT}'},
        ]
    else:  # imagen
        mt, _ = mimetypes.guess_type(path)
        if not mt or not mt.startswith('image/'):
            mt = 'image/jpeg'
        content = [
            {'type': 'image',
             'source': {'type': 'base64', 'media_type': mt,
                        'data': base64.standard_b64encode(data_bytes).decode('utf-8')}},
            {'type': 'text', 'text': PROMPT},
        ]

    raw, _usage = _llamar_claude(content, api_key, model)
    obj = _parse_json(raw)
    if obj is None or not isinstance(obj.get('items'), list):
        raise ValueError('Claude no devolvió un JSON válido o sin `items`.')

    return _a_preview_shape(obj)


def _a_preview_shape(obj):
    """Convierte el JSON de Claude al shape que ya usa el wizard de ofertas:
       {headers, rows, mapping, header_row, count_filas, fuente: 'ia'}.
    El mapping queda pre-resuelto (identidad) porque los campos ya están
    normalizados a las columnas standard.
    """
    vig_global = obj.get('vigencia_hasta')
    items = obj.get('items') or []
    headers = list(COLUMNAS_STANDARD)
    rows = []
    for it in items:
        if not isinstance(it, dict):
            continue
        fila = []
        for col in COLUMNAS_STANDARD:
            val = it.get(col)
            if col == 'vigencia_hasta' and not val:
                val = vig_global   # fallback al global
            fila.append('' if val is None else val)
        rows.append(fila)
    mapping = {h: h for h in headers}
    return {
        'headers': headers,
        'rows': rows,
        'mapping': mapping,
        'header_row': 0,
        'count_filas': len(rows),
        'fuente': 'ia',
        'laboratorio_detectado': obj.get('laboratorio'),
        'vigencia_global': vig_global,
    }
