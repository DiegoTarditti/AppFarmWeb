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


def _detectar_columnas(rows, candidatos=None):
    """Busca una fila de header en las primeras 10 filas y mapea sus columnas
    a los campos del sistema usando `field_inference` (módulo central).

    Devuelve dict { campo_salida: indice_columna } y nro de fila donde está
    el header (None si no encontró).

    Args:
        rows: lista de filas (cada fila tupla/lista de valores).
        candidatos: subset de campos a considerar. None = todos los del
            catálogo. Para el importador de ofertas dejamos los específicos
            del flujo.
    """
    import field_inference as fi
    if candidatos is None:
        candidatos = ['ean', 'codigo', 'descripcion', 'unidades_minima',
                      'precio', 'descuento_psl', 'rentabilidad',
                      'plazo_pago', 'grupo_id']
    for ri, row in enumerate(rows[:10]):
        if not row:
            continue
        headers = [str(v) if v is not None else '' for v in row]
        mapa = fi.inferir_columnas(headers, candidatos=candidatos)
        # Lo consideramos header válido si encontró al menos 2 campos y uno
        # de los anclas (ean/codigo/descripcion) está presente.
        ancla = any(c in mapa for c in ('ean', 'codigo', 'descripcion'))
        if len(mapa) >= 2 and ancla:
            return mapa, ri
    return {}, None


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
    """Fallback: si no encontramos headers, detectar columnas por contenido.

    Usa field_inference para clasificar cada columna por los valores que
    contiene. Detecta EAN, descripción, porcentajes (descuento/rentabilidad),
    enteros cortos (mínimo), y dinero (precio).
    """
    import field_inference as fi

    data_rows = [r for r in rows[:20] if r and any(v is not None for v in r)]
    if not data_rows:
        return {'ean': 0, 'descripcion': 1}, None

    n_cols = max(len(r) for r in data_rows)
    candidatos = ['ean', 'codigo', 'descripcion', 'unidades_minima',
                  'precio', 'descuento_psl', 'rentabilidad', 'grupo_id']

    mapa = fi.inferir_columnas(
        headers=[''] * n_cols,
        sample_rows=data_rows,
        candidatos=candidatos,
    )

    # Garantizar EAN y descripción mínimos si no se detectaron
    usadas = set(mapa.values())
    if 'ean' not in mapa:
        for ci in range(n_cols):
            if ci not in usadas:
                sample = [r[ci] if ci < len(r) else None for r in data_rows]
                if any(_is_ean_value(v) for v in sample):
                    mapa['ean'] = ci
                    usadas.add(ci)
                    break
        else:
            mapa['ean'] = 0
            usadas.add(0)
    if 'descripcion' not in mapa:
        for ci in range(n_cols):
            if ci not in usadas:
                sample = [r[ci] if ci < len(r) else None for r in data_rows]
                if any(isinstance(v, str) and len(v) > 5 for v in sample):
                    mapa['descripcion'] = ci
                    break
        else:
            mapa['descripcion'] = 1 if mapa.get('ean') != 1 else 2

    return mapa, None


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
