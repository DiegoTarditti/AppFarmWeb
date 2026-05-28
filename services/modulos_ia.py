"""Extracción de módulos de descuento (packs de laboratorio) a JSON estructurado
vía Claude.

Espejo de `services/ofertas_ia.py` pero para el formato de MÓDULOS / PACKS.
Devuelve la misma estructura que `parsers/modulos_xlsx.parse_modulos_xlsx`:

    [{'nombre': 'MOD. ...', 'items': [{'ean', 'descripcion', 'cant',
      'desc_pct', 'destacado'}, ...]}, ...]

Así el resto del flujo (pack_detector, preview, commit) NO cambia: la IA
entra por el mismo carril que el parser regex.

PDF/imagen → Vision API. XLSX/XLS → texto CSV-like.

Modelo por defecto: Haiku 4.5.
"""
import base64
import json
import mimetypes
import os
import re

MODEL = 'claude-haiku-4-5-20251001'

PROMPT = """Sos un extractor de catálogos de MÓDULOS DE DESCUENTO de laboratorios farmacéuticos argentinos. Leé el documento adjunto y devolvé SOLO un JSON (sin markdown, sin texto extra) con esta estructura exacta:

{
  "modulos": [
    {
      "nombre": "MOD. ...",
      "items": [
        {"ean": "...", "descripcion": "...", "cant": 0, "desc_pct": 0, "destacado": false}
      ]
    }
  ]
}

REGLAS:
- Números: punto decimal, SIN separador de miles. El doc usa formato argentino (1.234,56) → convertir a 1234.56.
- Porcentajes (desc_pct): número crudo sin el "%". "7%" → 7. "7,5%" → 7.5.
- NO inventes datos: campo ausente → null. NO omitas filas con EAN+descripción válidos.
- ean: SOLO si es un código de barras real (8-14 dígitos). Si no está claro o es un código interno del lab, dejá null. CRÍTICO: copiá el EAN EXACTAMENTE como figura, NO conviertas O por 0 ni l/I por 1.
- descripcion: nombre del producto, presentación incluida (ej. "AMOXIDAL DUO 850 PACK X 10 ENV. x 14 COM"). NO cortes ni resumas — copiá literal.
- cant: cantidad de packs/cajas en el módulo (columna "CANT." del Excel típico). Si no está, null.
- desc_pct: descuento % del módulo aplicado a esa fila (columna "DESC %"). Si no, null.
- destacado: SIEMPRE false desde IA (el destacado en amarillo del Excel se detecta aparte; vos no lo ves).
- nombre del módulo: el título de la sección que precede a los ítems (ej. "MOD. OPTAMOX DUO", "MÓDULO ANTIBIÓTICOS"). Si todos los items van bajo un único módulo sin nombre claro, usá un nombre genérico ("MÓDULO 1").

CASOS:
- "PACK X N" en la descripción es señal de pack — copiá tal cual el texto, NO lo separes ni lo conviertas.
- Múltiples módulos en el doc: agrupá los items bajo cada módulo correspondiente.
- Multi-página: concatená todos los módulos / items en el array.
- Filas de header, totales o vacías: ignorar.

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


def _xlsx_a_texto(path, max_filas_por_hoja=500):
    """Lee un XLSX/XLS y lo devuelve como texto pipe-separated, una sección por hoja."""
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
    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)
    with client.messages.stream(
        model=model, max_tokens=16000,
        messages=[{'role': 'user', 'content': content_parts}],
    ) as stream:
        resp = stream.get_final_message()
    raw = ''.join(b.text for b in resp.content if getattr(b, 'type', '') == 'text').strip()
    return raw, resp.usage


def extraer(path, ext, api_key=None, model=MODEL):
    """Extrae módulos de un archivo. Devuelve la MISMA lista de módulos que
    `parsers/modulos_xlsx.parse_modulos_xlsx`, lista para `pack_detector`."""
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
    else:
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
    if obj is None or not isinstance(obj.get('modulos'), list):
        raise ValueError('Claude no devolvió un JSON válido o sin `modulos`.')

    # Adaptar al shape del parser regex (lista de módulos, no dict-wrapped).
    out = []
    for m in obj['modulos']:
        if not isinstance(m, dict):
            continue
        items = []
        for it in (m.get('items') or []):
            if not isinstance(it, dict):
                continue
            items.append({
                'ean': (str(it.get('ean') or '').strip()) or None,
                'descripcion': (str(it.get('descripcion') or '').strip()) or '',
                'cant': it.get('cant'),
                'desc_pct': it.get('desc_pct'),
                'destacado': False,   # IA no ve resaltados del Excel
            })
        if items:
            out.append({
                'nombre': (str(m.get('nombre') or '').strip()) or 'MÓDULO',
                'items': items,
            })
    return out
