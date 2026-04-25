"""Parser genérico de Excel de ofertas.

Detecta heurísticamente las columnas comunes (ean, código, descripción, cantidad
mínima, precio, descuento, plazo de pago, grupo) buscando palabras clave en las
primeras 10 filas. Si encuentra fila de header, la usa. Si no, fallback a
detección por contenido (EAN = numérico 7-14 dígitos).

Salida: lista de dicts con campos opcionales. EAN y descripción son los más
probables de estar; los demás solo aparecen si el archivo los trae.

Compatible con el formato Bernabó completo y con archivos genéricos chicos.

Ejemplo de output:
    [{
        'ean': '7793450121123',
        'codigo': 'BON-001',                # opcional
        'descripcion': 'AMOXICILINA 500 COM x 16',
        'unidades_minima': 12,              # opcional
        'precio': 1500.50,                  # opcional
        'descuento_psl': 20.5,              # opcional (% de descuento)
        'rentabilidad': 15.0,               # opcional
        'plazo_pago': '30 días',            # opcional
        'grupo_id': 1,                      # opcional
    }, ...]
"""
import re
import unicodedata


def _norm(s):
    """Normaliza un header: lowercase, sin acentos, sin puntuación, espacios → _."""
    if s is None:
        return ''
    s = unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode('ascii')
    s = s.lower().strip()
    s = re.sub(r'[^a-z0-9]+', '_', s)
    return s.strip('_')


# Mapeo header → campo de salida. Múltiples palabras clave por campo.
HEADER_KEYWORDS = {
    'ean':              ['ean', 'codigo_barra', 'codigobarra', 'cod_barra', 'cb', 'gtin'],
    'codigo':           ['codigo', 'cod', 'sku', 'cod_interno', 'codigo_interno', 'art', 'ref'],
    'descripcion':      ['descripcion', 'producto', 'detalle', 'nombre', 'articulo', 'desc'],
    'unidades_minima':  ['minimo', 'min', 'unidades_minima', 'cant_min', 'cantidad_min',
                         'unid_min', 'cantidad', 'unidades', 'cant', 'piezas'],
    'precio':           ['precio', 'pvp', 'pvf', 'p_unit', 'precio_unit', 'precio_unitario',
                         'importe', '$', 'costo'],
    'descuento_psl':    ['descuento', 'dto', 'desc', 'desc_pct', 'porcentaje', 'pct',
                         'rebaja', 'descuento_psl', 'descuento_psf'],
    'rentabilidad':     ['rentabilidad', 'rent', 'margen', 'rentab'],
    'plazo_pago':       ['plazo', 'plazo_pago', 'pago', 'condicion'],
    'grupo_id':         ['grupo', 'grupo_id', 'agrupacion', 'set'],
}


def _detectar_columnas(rows):
    """Busca una fila de header en las primeras 10 filas.

    Devuelve dict { campo_salida: indice_columna } y nro de fila donde está
    el header (None si no encontró).
    """
    mapa = {}
    header_row_idx = None
    for ri, row in enumerate(rows[:10]):
        if not row:
            continue
        candidato = {}
        for ci, val in enumerate(row):
            n = _norm(val)
            if not n:
                continue
            for campo, kws in HEADER_KEYWORDS.items():
                if campo in candidato:
                    continue  # ya encontramos esa columna
                # Match exacto o que contenga la keyword
                if n in kws or any(kw in n for kw in kws if len(kw) >= 3):
                    candidato[campo] = ci
                    break
        # Lo consideramos header si encontró al menos 2 campos significativos
        # (uno de ellos debería ser ean/codigo/descripcion para validar).
        ancla = any(c in candidato for c in ('ean', 'codigo', 'descripcion'))
        if len(candidato) >= 2 and ancla:
            mapa = candidato
            header_row_idx = ri
            break
    return mapa, header_row_idx


def _is_ean_value(val):
    """Detecta si un valor parece ser un EAN (numérico de 7-14 dígitos)."""
    if val is None:
        return False
    s = str(val).strip().replace('.0', '').replace(' ', '')
    return s.isdigit() and 7 <= len(s) <= 14


def _to_float(val):
    """Intenta convertir a float aceptando comas como decimal (formato AR)."""
    if val is None or val == '':
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace(' ', '')
    # Sacar símbolos de moneda y porcentaje
    s = s.replace('$', '').replace('%', '')
    # Si tiene coma decimal: 1.234,56 → 1234.56
    if ',' in s and s.count(',') == 1:
        # Asumimos que la coma es decimal si no hay punto, o si el punto está antes
        if '.' in s:
            s = s.replace('.', '').replace(',', '.')
        else:
            s = s.replace(',', '.')
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _to_int(val):
    f = _to_float(val)
    return int(f) if f is not None else None


def _coerce_row(row, mapa):
    """Aplica el mapa de columnas → campos a una row, devuelve dict del item.
    Devuelve None si la row no parece tener un EAN/código válido."""
    item = {}

    def get(campo):
        idx = mapa.get(campo)
        return row[idx] if idx is not None and idx < len(row) else None

    # EAN / código — al menos uno debe estar
    ean_raw = get('ean')
    cod_raw = get('codigo')

    if _is_ean_value(ean_raw):
        item['ean'] = str(ean_raw).strip().replace('.0', '').replace(' ', '')
    if cod_raw is not None and str(cod_raw).strip():
        item['codigo'] = str(cod_raw).strip()

    if 'ean' not in item and 'codigo' not in item:
        return None

    desc = get('descripcion')
    if desc is not None and str(desc).strip():
        item['descripcion'] = str(desc).strip()

    # Numéricos
    if (v := _to_int(get('unidades_minima'))) is not None and v > 0:
        item['unidades_minima'] = v
    if (v := _to_float(get('precio'))) is not None and v > 0:
        item['precio'] = v
    if (v := _to_float(get('descuento_psl'))) is not None:
        item['descuento_psl'] = v
    if (v := _to_float(get('rentabilidad'))) is not None:
        item['rentabilidad'] = v

    # Strings opcionales
    pp = get('plazo_pago')
    if pp is not None and str(pp).strip():
        item['plazo_pago'] = str(pp).strip()[:100]

    if (v := _to_int(get('grupo_id'))) is not None:
        item['grupo_id'] = v

    return item


def _parse_heuristico_sin_header(rows):
    """Fallback: si no encontramos headers, detectar columnas por el contenido
    de las primeras filas — la columna que tenga EAN-like values es la EAN.
    Mantiene retrocompatibilidad con el parser anterior (ean + descripción)."""
    ean_col = None
    desc_col = None
    for row in rows[:10]:
        if not row:
            continue
        for ci, val in enumerate(row):
            if _is_ean_value(val) and ean_col is None:
                ean_col = ci
            elif ean_col is not None and ci != ean_col and val and not _is_ean_value(val):
                if desc_col is None:
                    desc_col = ci
                    break
        if ean_col is not None and desc_col is not None:
            break

    if ean_col is None:
        ean_col, desc_col = 0, 1
    if desc_col is None:
        desc_col = 1 if ean_col != 1 else 2

    return {'ean': ean_col, 'descripcion': desc_col}, None


def parse_ofertas_xlsx(path):
    """Devuelve lista de ofertas como dicts. Mantiene retrocompat con el parser
    viejo: si el archivo solo tiene EAN + descripción, devuelve eso. Si tiene
    más columnas reconocibles, devuelve más campos por fila.
    """
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active

    rows = list(ws.iter_rows(values_only=True))

    # Paso 1: intentar detectar fila de header
    mapa, header_idx = _detectar_columnas(rows)

    # Paso 2: si no hay header, fallback heurístico
    if not mapa:
        mapa, _ = _parse_heuristico_sin_header(rows)
        data_start = 0
    else:
        data_start = header_idx + 1

    # Paso 3: parsear filas de data
    ofertas = []
    seen_eans = set()
    seen_codigos = set()
    for row in rows[data_start:]:
        if not row:
            continue
        item = _coerce_row(row, mapa)
        if not item:
            continue
        # Dedup: por ean si está, sino por codigo
        key = item.get('ean') or f'cod:{item.get("codigo")}'
        if key in seen_eans or key in seen_codigos:
            continue
        if 'ean' in item:
            seen_eans.add(item['ean'])
        elif 'codigo' in item:
            seen_codigos.add(f'cod:{item["codigo"]}')
        ofertas.append(item)

    return ofertas
