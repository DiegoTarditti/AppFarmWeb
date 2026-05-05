"""Inferencia central de tipo de campo / mapeo de columnas.

UNA sola fuente de verdad para todos los importadores del sistema (ofertas
XLSX, conversor de facturas, módulos de descuento, etc.). Cualquier wizard
que tenga que mapear columnas/celdas a campos conocidos pasa por acá.

Tres niveles de inferencia, de más confiable a menos:

1. **Por header** (palabra clave en el nombre de la columna).
   `inferir_campo_por_header('descuento %')` → 'descuento_psl'.
2. **Por contenido** (forma del valor: EAN, money, %, fecha, etc.).
   `inferir_tipo_valor('7793450121123')` → 'ean'.
3. **Por matemática** (tripletes que cumplen una relación, ej. cant × unit
   = importe). `relacion_aritmetica([1, 100, 100])` → ('cant_unit_imp', ...).

API principal:
- `inferir_columnas(headers, sample_rows)` — combina niveles 1 + 2 sobre
  un dataset. Devuelve `{campo: idx_columna}` con confianza mejor primero.

API auxiliar:
- `inferir_tipo_valor(s)`, `inferir_campo_por_header(s)`,
  `parsear_numero_ar(s)`, `validar_ean(s)`.

Diseño: stateless, sin DB, sin imports de Flask/SQLAlchemy. Eso lo hace
testeable y reusable (también desde scripts CLI).
"""
import re
import unicodedata
from typing import Optional

# ── Diccionario de datos: campos conocidos por el sistema ───────────────────
# UNA fuente de verdad para todos los importadores. Cada entrada describe un
# campo "estándar" del dominio farmacéutico, con metadatos suficientes para:
#   - autodetección por header (keywords)
#   - validación por contenido (tipo + regex_valor opcional)
#   - render en UI (label, descripcion, ejemplos)
#   - priorización (nucleo: True → "siempre presente en cualquier import")
#
# Para usos específicos, los importadores eligen un subconjunto via
# `nombres_campos(nucleo_only=True)` o pasando `candidatos=[...]` a las
# funciones de inferencia.

CAMPOS = {
    # ── NÚCLEO: campos siempre usados ───────────────────────────────────────
    'ean': {
        'label': 'EAN',
        'descripcion': 'Código de barras (7-14 dígitos numéricos)',
        'tipo': 'ean',
        'nucleo': True,
        'keywords': ['ean', 'codigo_ean', 'cod_ean', 'codigo_barra', 'codigo_barras',
                     'codigo_de_barras', 'codigobarra', 'cod_barra', 'cb', 'gtin'],
        'regex_valor': r'^\d{7,14}$',
        'ejemplos': ['7793450121123', '7791234567890'],
    },
    'codigo': {
        'label': 'Código',
        'descripcion': 'Código interno del proveedor / SKU',
        'tipo': 'text',
        'nucleo': True,
        'keywords': ['codigo', 'cod', 'sku', 'cod_interno', 'codigo_interno', 'art', 'ref'],
        'ejemplos': ['BON-001', '79-65', 'AMX500'],
    },
    'descripcion': {
        'label': 'Descripción',
        'descripcion': 'Nombre/detalle del producto',
        'tipo': 'text',
        'nucleo': True,
        'keywords': ['descripcion', 'producto', 'detalle', 'nombre', 'articulo', 'desc'],
        'ejemplos': ['TAFIROL 1g COM x 50', 'AMOXIDAL 500mg COM x 16'],
    },
    'cantidad': {
        'label': 'Cantidad',
        'descripcion': 'Unidades pedidas/facturadas (entero corto)',
        'tipo': 'int',
        'nucleo': True,
        'keywords': ['cantidad', 'cant', 'qty', 'piezas', 'unidades', 'uds', 'unid'],
        'regex_valor': r'^\d{1,4}(?:\.0+)?$',
        'ejemplos': ['1', '12', '100'],
    },
    'precio': {
        'label': 'Precio',
        'descripcion': 'Precio unitario en $ (acepta formatos AR y EN)',
        'tipo': 'money',
        'nucleo': True,
        'keywords': ['precio', 'pvf', 'p_unit', 'importe', 'costo'],
        'ejemplos': ['1.234,56', '4500', '$ 1500.50'],
    },
    'descuento_psl': {
        'label': 'Descuento %',
        'descripcion': 'Porcentaje de descuento (0–100)',
        'tipo': 'pct',
        'nucleo': True,
        'keywords': ['descuento', 'dto', 'desc', 'desc_pct', 'porcentaje', 'pct',
                     'rebaja', 'descuento_psl', 'descuento_psf', 'dscto', 'descto'],
        'regex_valor': r'^\d{1,2}(?:[.,]\d{1,3})?\s*%?$',
        'ejemplos': ['25', '7,5%', '0.20'],
    },

    # ── EXTRAS: específicos de algún flujo ──────────────────────────────────
    'nombre_modulo': {
        'label': 'Nombre del módulo',
        'descripcion': 'Identificador del módulo de descuento (ej. "MOD 1", "PROMO ABRIL")',
        'tipo': 'text',
        'nucleo': False,
        'keywords': ['modulo', 'nombre_modulo', 'mod', 'pack', 'promo'],
        'ejemplos': ['MOD 1', 'PROMO ABRIL', 'PACK CARDIO'],
    },
    'codigo_alfabeta': {
        'label': 'Código Alfabeta',
        'descripcion': 'Código del catálogo Alfabeta (clave compartida con ObServer)',
        'tipo': 'text',
        'nucleo': False,
        'keywords': ['alfabeta', 'cod_alfabeta', 'codigo_alfabeta'],
        'ejemplos': ['AMX500-16'],
    },
    'unidades_minima': {
        'label': 'Mín. unidades',
        'descripcion': 'Cantidad mínima para acceder al descuento',
        'tipo': 'int',
        'nucleo': False,
        'keywords': ['minimo', 'min_unidades', 'unidades_minima', 'unidades_min',
                     'min_cantidad', 'cant_min', 'cantidad_min', 'unid_min', 'minima'],
        'ejemplos': ['12', '50', '100'],
    },
    'precio_publico': {
        'label': 'Precio público',
        'descripcion': 'Precio al público (PVP) antes del descuento',
        'tipo': 'money',
        'nucleo': False,
        'keywords': ['precio_publico', 'precio_pub', 'pvp', 'precio_publ', 'p_publico'],
        'ejemplos': ['5.500,00'],
    },
    'precio_unitario': {
        'label': 'Precio unitario',
        'descripcion': 'Precio neto por unidad (después de descuento)',
        'tipo': 'money',
        'nucleo': False,
        'keywords': ['precio_unitario', 'precio_unit', 'pcio_unit', 'unitario', 'unit'],
        'ejemplos': ['4.125,00'],
    },
    'importe': {
        'label': 'Importe',
        'descripcion': 'Subtotal de la línea (cant × precio_unitario)',
        'tipo': 'money',
        'nucleo': False,
        'keywords': ['importe', 'subtotal', 'monto', 'total_renglon'],
        'ejemplos': ['49.500,00'],
    },
    'rentabilidad': {
        'label': 'Rentabilidad %',
        'descripcion': 'Margen comercial sobre el costo',
        'tipo': 'pct',
        'nucleo': False,
        'keywords': ['rentabilidad', 'rent', 'margen', 'rentab'],
        'ejemplos': ['35,87%'],
    },
    'plazo_pago': {
        'label': 'Plazo de pago',
        'descripcion': 'Condición/plazo de pago',
        'tipo': 'text',
        'nucleo': False,
        'keywords': ['plazo', 'plazo_pago', 'pago', 'condicion'],
        'ejemplos': ['30 días', 'Contado'],
    },
    'grupo_id': {
        'label': 'Grupo',
        'descripcion': 'Agrupación para mínimos compartidos',
        'tipo': 'int',
        'nucleo': False,
        'keywords': ['grupo', 'grupo_id', 'agrupacion', 'set'],
        'ejemplos': ['1', '2'],
    },
    'lote': {
        'label': 'Lote',
        'descripcion': 'Número de lote / partida',
        'tipo': 'text',
        'nucleo': False,
        'keywords': ['lote', 'partida', 'batch'],
        'ejemplos': ['L240312'],
    },
    'vencimiento': {
        'label': 'Vencimiento',
        'descripcion': 'Fecha de vencimiento del lote',
        'tipo': 'date',
        'nucleo': False,
        'keywords': ['vencimiento', 'venc', 'vto', 'expiracion'],
        'ejemplos': ['12/2026', '31/12/2025'],
    },
}


def nombres_campos(nucleo_only=False):
    """Lista los nombres de campos del catálogo. nucleo_only=True filtra a los siempre-usados."""
    if nucleo_only:
        return [n for n, c in CAMPOS.items() if c.get('nucleo')]
    return list(CAMPOS.keys())


def campo(nombre):
    """Devuelve el dict de metadatos de un campo, o None si no existe."""
    return CAMPOS.get(nombre)


# ── Helpers de texto ────────────────────────────────────────────────────────

def _norm_header(s) -> str:
    """Normaliza un header: lower + sin acentos + no-alfanum → '_'.
    Ej: 'Descuento %' → 'descuento'.
    """
    if s is None:
        return ''
    s = unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode('ascii')
    s = s.lower().strip()
    s = re.sub(r'[^a-z0-9]+', '_', s)
    return s.strip('_')


# ── Inferencia por contenido (tipo de un valor) ────────────────────────────

_RE_EAN       = re.compile(r'^\d{7,14}$')
_RE_INT_SHORT = re.compile(r'^\d{1,2}(?:\.0+)?$')   # 1-99: "5" o "5.0"
_RE_MONEY_AR  = re.compile(r'^\d{1,3}(?:\.\d{3})+,\d{2}$|^\d+,\d{2}$')
_RE_MONEY_EN  = re.compile(r'^\d{1,3}(?:,\d{3})+\.\d{2}$|^\d+\.\d{2}$')
_RE_PCT_AR    = re.compile(r'^\d{1,2}(?:[.,]\d{1,3})?\s*%?$')
_RE_DATE      = re.compile(r'^\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}$|^\d{4}-\d{2}-\d{2}')


def formatear_numero_ar(n, decimales=2) -> str:
    """Formatea un float como string AR limpio: '5.365.571,92'.

    Útil cuando convertimos un valor OCR-roto y queremos volcar el
    valor canonical al input del formulario (sin ambigüedad).
    """
    if n is None:
        return ''
    try:
        n = float(n)
    except (TypeError, ValueError):
        return ''
    # f-string con coma decimal y punto de miles (formato AR)
    s = f'{n:,.{decimales}f}'   # estilo EN: 5,365,571.92
    # Swap: , ↔ .  → AR: 5.365.571,92
    s = s.replace(',', 'X').replace('.', ',').replace('X', '.')
    return s


def parsear_numero_ar(s) -> Optional[float]:
    """Convierte a float aceptando formatos AR, EN y OCR-rotos.

    Estrategia robusta: la ÚLTIMA coma o punto que aparezca después de un
    grupo de 1-3 dígitos al final se trata como decimal; todos los otros
    separadores (comas o puntos) se descartan como miles.

    Acepta:
        '1.234,56'      → 1234.56  (AR)
        '1,234.56'      → 1234.56  (EN)
        '1234,56'       → 1234.56
        '1,183.326,62'  → 1183326.62  (OCR-roto: punto en medio + coma decimal)
        '10,486,61'     → 10486.61  (OCR-roto: dos comas, la última decimal)
        '$ 100,50'      → 100.5
        '25%'           → 25.0
    """
    if s is None or s == '':
        return None
    if isinstance(s, (int, float)):
        return float(s)
    txt = str(s).strip().replace(' ', '').replace('$', '').replace('%', '')
    if not txt:
        return None

    # Encontrar el último separador (coma o punto). Si hay un dígito decimal
    # después (1-3 dígitos), ese separador es el punto decimal.
    if ',' in txt or '.' in txt:
        last_comma = txt.rfind(',')
        last_dot = txt.rfind('.')
        last_sep = max(last_comma, last_dot)
        # ¿Hay 1-3 dígitos después del último separador? → es decimal.
        cola = txt[last_sep + 1:]
        if cola.isdigit() and 1 <= len(cola) <= 3:
            entero = txt[:last_sep].replace(',', '').replace('.', '')
            txt = entero + '.' + cola
        else:
            # Sin parte decimal clara: todos los separadores son miles.
            txt = txt.replace(',', '').replace('.', '')

    try:
        return float(txt)
    except (ValueError, TypeError):
        return None


def validar_ean(s) -> bool:
    """True si el valor tiene forma de EAN (numérico de 7 a 14 dígitos)."""
    if s is None:
        return False
    txt = str(s).strip().replace(' ', '')
    # Excel guarda enteros como float: '7793450121123.0' → quitar el '.0'.
    if txt.endswith('.0'):
        txt = txt[:-2]
    return bool(_RE_EAN.match(txt))


def inferir_tipo_valor(s) -> Optional[str]:
    """Devuelve 'ean' | 'int' | 'money' | 'pct' | 'date' | 'text' | None.

    Heurística por la forma del valor. None si está vacío.
    """
    if s is None or s == '':
        return None
    txt = str(s).strip()
    if not txt:
        return None

    # EAN primero (más específico que int).
    if validar_ean(txt):
        return 'ean'
    # Fecha
    if _RE_DATE.match(txt):
        return 'date'
    # Money (con separadores de miles o decimales explícitos)
    if _RE_MONEY_AR.match(txt) or _RE_MONEY_EN.match(txt):
        return 'money'
    n = parsear_numero_ar(txt)
    # Pct con `%` explícito → siempre es pct
    if txt.endswith('%') and n is not None:
        return 'pct'
    # Int corto SIN % (1-99, sin separadores) → int. Tiene prioridad sobre pct
    # porque "5" es más probablemente "5 unidades" que "5%".
    if _RE_INT_SHORT.match(txt) and n is not None and 1 <= n <= 99 and n == int(n):
        return 'int'
    # Pct sin `%`: número 0-100 con o sin decimal corto, distinto de int corto.
    if _RE_PCT_AR.match(txt) and n is not None and 0 <= n <= 100:
        return 'pct'
    # Cualquier otro número → money por descarte (incluye 100-9999 enteros).
    if n is not None:
        return 'money'
    return 'text'


# ── Inferencia por header (palabra clave) ───────────────────────────────────

def inferir_campo_por_header(header, candidatos=None) -> Optional[str]:
    """Devuelve el nombre del campo (ean/codigo/descripcion/...) o None.

    Estrategia:
    1. **Match exacto**: si una o más keywords matchean exactamente el header
       normalizado, gana el campo MÁS específico (menos keywords totales).
       Ej. 'pvp' → entre 'precio' (8 kw) y 'precio_publico' (5 kw), gana
       precio_publico.
    2. **Score por contains**: para cada campo, sumar la longitud de cada
       keyword (≥3 chars) que esté contenida en el header. Gana el de mayor
       score (keywords más largas y/o más matches → más específico).

    Args:
        header: texto del header de la columna.
        candidatos: subset de nombres de campo a considerar (None = todos).
    """
    n = _norm_header(header)
    if not n:
        return None
    candidatos = list(candidatos) if candidatos else list(CAMPOS.keys())

    # Heurística: si el header original contiene '%', no es descripcion ni
    # ningún campo de texto libre. Forzamos a descartarlos.
    # Esto resuelve el bug donde 'DESC. %' (descuento) se confundía con
    # 'DESCRIPCIÓN' por compartir el prefijo 'desc'.
    if '%' in str(header):
        for textual in ('descripcion', 'codigo', 'plazo_pago', 'codigo_alfabeta',
                        'nombre_modulo'):
            if textual in candidatos:
                candidatos.remove(textual)

    # Heurística: si el header sugiere texto descriptivo (producto, descripcion,
    # nombre, articulo, detalle) Y NO menciona explícitamente "código/EAN/etc.",
    # descartar candidatos de identificador. Esto evita que "PRODUCTO" se mapee
    # a `codigo` por contenido, sin afectar headers como "Cód. Producto" o
    # "Código del Producto" donde la intención es claramente código.
    _kws_textuales = ('producto', 'descripcion', 'nombre', 'articulo', 'detalle')
    _kws_id = {'cod', 'codigo', 'cb', 'ean', 'gtin', 'sku', 'ref', 'art'}
    tiene_textual = any(kw in n for kw in _kws_textuales)
    # Split por '_' (separador que produce _norm_header) — chequear si alguna
    # parte coincide exacta con una keyword de identificador.
    partes = set(n.split('_'))
    tiene_id = bool(partes & _kws_id)
    if tiene_textual and not tiene_id:
        for ident_field in ('ean', 'codigo', 'codigo_alfabeta'):
            if ident_field in candidatos:
                candidatos.remove(ident_field)

    # Paso 1: exacto. El más específico (menos keywords) gana.
    exactos = []
    for nombre in candidatos:
        if nombre not in CAMPOS:
            continue
        if any(n == kw for kw in CAMPOS[nombre]['keywords']):
            exactos.append(nombre)
    if exactos:
        return min(exactos, key=lambda c: len(CAMPOS[c]['keywords']))

    # Paso 2: scoring por longitud de keywords matchadas, con bonus por
    # match al INICIO del header (alineación posicional fuerte: ej. "Cod.
    # Producto" → 'cod' al inicio gana sobre 'producto' aunque sea más corta).
    scores = {}
    for nombre in candidatos:
        if nombre not in CAMPOS:
            continue
        score = 0
        for kw in CAMPOS[nombre]['keywords']:
            if len(kw) < 3 or kw not in n:
                continue
            base = len(kw)
            # Bonus 3x si la keyword aparece al INICIO del header. Captura
            # el patrón "<prefix>. <descriptor>" tipo "Cód. Producto" donde
            # la primera palabra es la que define el campo.
            if n.startswith(kw):
                base *= 3
            score += base
        if score:
            scores[nombre] = score
    if scores:
        return max(scores.keys(), key=lambda c: scores[c])
    return None


# ── Inferencia mixta: header + contenido ────────────────────────────────────

def _columna_es_consistente(sample_values, tipo_esperado, campo=None) -> bool:
    """¿Mayoría de los valores no-vacíos de la columna concuerdan con el tipo?

    Si `campo` es 'ean' o 'codigo', además exige que los valores parezcan
    identificadores cortos (sin espacios, ≤20 chars). Evita proponer una
    columna de descripciones largas como código.
    """
    if not sample_values:
        return False
    no_vacios = [v for v in sample_values if v is not None and v != '']
    if not no_vacios:
        return False
    if campo in ('ean', 'codigo', 'codigo_alfabeta'):
        # Si la mayoría de los valores tiene espacios o son largos, no es un id.
        no_id = sum(1 for v in no_vacios
                    if ' ' in str(v).strip() or len(str(v).strip()) > 20)
        if no_id / len(no_vacios) > 0.3:
            return False
    hits = sum(1 for v in no_vacios if inferir_tipo_valor(v) == tipo_esperado)
    return hits / len(no_vacios) >= 0.6


def inferir_columnas(headers, sample_rows=None, candidatos=None) -> dict:
    """Devuelve dict {campo: idx_columna} sobre los headers (+ filas opcionales).

    Estrategia:
    1. Para cada header, intentar mapearlo por palabra clave (alta confianza).
    2. Si una columna queda sin asignar y hay sample_rows, intentar por
       contenido: detectar tipo dominante de la columna y matchear con un
       campo que esté libre y espere ese tipo.

    Args:
        headers: lista de strings (header de cada columna).
        sample_rows: lista de filas (cada fila = lista de valores) para
            inferencia por contenido. Si None, solo se usa header.
        candidatos: subset de nombres de campo a considerar. None = todos.

    Returns:
        dict {campo: idx_columna}. Puede tener menos entradas que headers.
    """
    candidatos = candidatos or list(CAMPOS.keys())
    mapa = {}
    usados = set()  # índices ya asignados a algún campo

    # Paso 1: por header. Pasamos candidatos = todos los todavía libres, así
    # si una keyword ambigua matchea varios campos (ej. 'desc' matchea
    # 'descripcion' Y 'descuento_psl'), una vez asignado uno el siguiente
    # header con la misma palabra prueba el OTRO campo en vez de quedar sin
    # mapear.
    for ci, h in enumerate(headers):
        if ci in usados:
            continue
        libres = [c for c in candidatos if c not in mapa]
        if not libres:
            break
        campo = inferir_campo_por_header(h, candidatos=libres)
        if campo:
            mapa[campo] = ci
            usados.add(ci)

    # Paso 2: por contenido (sólo si hay sample_rows y faltan campos clave).
    if sample_rows:
        libres = [c for c in candidatos if c not in mapa]
        # Para cada columna sin uso, detectar tipo dominante.
        for ci, _h in enumerate(headers):
            if ci in usados or not libres:
                continue
            sample = [r[ci] if ci < len(r) else None for r in sample_rows]
            for campo in libres:
                tipo = CAMPOS[campo]['tipo']
                if _columna_es_consistente(sample, tipo, campo=campo):
                    mapa[campo] = ci
                    usados.add(ci)
                    libres.remove(campo)
                    break

    return mapa


# ── Inferencia por relación aritmética ──────────────────────────────────────
# Útil para detectar trios (cant, unit, importe) o pares (gravado, iva)
# directamente sobre los VALORES de una fila o pie de factura, sin headers.

def relacion_aritmetica(values, contexto='item', tol_rel=0.005, tol_abs=0.05):
    """Busca relaciones conocidas entre los `values` numéricos de una fila.

    Args:
        values: lista de floats (filtrar None antes de llamar).
        contexto: 'item' busca cant×unit=imp y pub×(1-dto%)=unit.
                  'totales' busca iva=gravado×21% (o 10.5%) y total=sum(rest).
        tol_rel: tolerancia relativa (0.5% por default).
        tol_abs: tolerancia absoluta (0.05 por default).

    Returns:
        Lista de dicts {tipo, indices, formula} con las relaciones encontradas.
    """
    nums = [(i, v) for i, v in enumerate(values) if v is not None]
    out = []

    def _eq(a, b):
        return abs(a - b) <= max(tol_abs, abs(b) * tol_rel)

    if contexto == 'item':
        # Triplete: cant (entero chico) × unit (money) ≈ imp (money)
        for ai, (i, vi) in enumerate(nums):
            if not (vi >= 1 and vi <= 9999 and vi == int(vi)):
                continue
            for bi, (j, vj) in enumerate(nums[ai+1:], start=ai+1):
                for k, vk in nums[bi+1:]:
                    if _eq(vi * vj, vk):
                        out.append({
                            'tipo': 'cant_unit_imp',
                            'indices': {'cantidad': i, 'precio_unitario': j, 'importe': k},
                            'formula': f'{vi} × {vj} = {vk}',
                        })
                        break
                if out and out[-1]['tipo'] == 'cant_unit_imp':
                    break
            if out and out[-1]['tipo'] == 'cant_unit_imp':
                break
        # Par con descuento: pub × (1 - dto/100) ≈ unit
        for i, vi in nums:
            for j, vj in nums:
                if i == j or not (0 <= vj <= 100):
                    continue
                for k, vk in nums:
                    if k in (i, j):
                        continue
                    if _eq(vi * (1 - vj / 100), vk):
                        out.append({
                            'tipo': 'pub_dto_unit',
                            'indices': {'precio_publico': i, 'dto': j, 'precio_unitario': k},
                            'formula': f'{vi} × (1−{vj}%) = {vk}',
                        })
                        return out
        return out

    if contexto == 'pub_dto':
        # Solo busca pub × (1 - dto/100) ≈ unit (sin requerir cant_unit_imp).
        for i, vi in nums:
            for j, vj in nums:
                if i == j or not (0 <= vj <= 100):
                    continue
                for k, vk in nums:
                    if k in (i, j):
                        continue
                    if _eq(vi * (1 - vj / 100), vk):
                        out.append({
                            'tipo': 'pub_dto_unit',
                            'indices': {'precio_publico': i, 'dto': j, 'precio_unitario': k},
                            'formula': f'{vi} × (1−{vj}%) = {vk}',
                        })
                        return out
        return out

    if contexto == 'totales':
        # iva = gravado × rate
        for rate, campo_iva in ((0.21, 'iva_21'), (0.105, 'iva_105')):
            for i, vi in nums:
                for j, vj in nums:
                    if i == j:
                        continue
                    if _eq(vi * rate, vj):
                        out.append({
                            'tipo': 'iva_gravado',
                            'indices': {'monto_gravado': i, campo_iva: j},
                            'formula': f'{vi} × {rate*100}% = {vj}',
                        })
                        break
                if out and out[-1]['tipo'] == 'iva_gravado':
                    break
            if out and out[-1]['tipo'] == 'iva_gravado':
                break
        # total = suma del resto (mayor de los moneys)
        if len(nums) >= 2:
            sorted_nums = sorted(nums, key=lambda x: -x[1])
            mayor_idx, mayor_v = sorted_nums[0]
            resto_sum = sum(v for _, v in nums) - mayor_v
            if _eq(resto_sum, mayor_v):
                out.append({
                    'tipo': 'total_suma',
                    'indices': {'total': mayor_idx},
                    'formula': f'sum(rest) = {mayor_v}',
                })
        return out

    return out


# ── Detección completa de fila de factura ──────────────────────────────────
# Reemplaza el algoritmo JS `autodetectarCampos` de templates/converter_pick.html.
# Usable también desde cualquier wizard que necesite detectar campos por
# contenido y matemática sobre tokens (no solo facturas).

def detectar_campos_factura(tokens):
    """Asigna campos a tokens de una fila de factura por contenido + matemática.

    Cascada:
    1. EAN: primer token con 12-14 dígitos.
    2. Triplete `cant × unit ≈ importe`: cant entero corto seguido de dos
       valores money cuyo producto cierra dentro de 0.5%.
    3. Par `pub × (1 - dto/100) = unit`: para detectar precio_publico y dto%.
    4. Descripción: tokens entre cant (excl.) y el primer numérico asignado.

    Args:
        tokens: lista de strings (tokens de la línea, en orden).

    Returns:
        dict {
            'asignaciones': {campo: idx_o_rango},
            'tipos': [tipo_por_token],
            'warnings': [str]
        }
        Donde rango se expresa como [start, end] (incluyente).
    """
    if not tokens:
        return {'asignaciones': {}, 'tipos': [], 'warnings': []}

    # 1. Tipar cada token. Usamos parsear_numero_ar (tolerante a OCR-rotos
    # como '15,963,95' o '1,183.326,62') en vez de los regex estrictos —
    # un valor es "money" si parsea y no es int corto ni pct.
    cls = []
    for i, t in enumerate(tokens):
        txt = str(t).strip()
        n = parsear_numero_ar(t)
        es_ean = validar_ean(t) and len(txt) >= 12
        es_int_corto = (n is not None and 1 <= n <= 9999 and n == int(n)
                        and bool(re.match(r'^\d{1,4}(?:\.0+)?$', txt)))
        es_pct = (n is not None and 0 <= n <= 100
                  and (txt.endswith('%') or bool(_RE_PCT_AR.match(txt))))
        # Money: parseable, no EAN, no int corto. NO excluimos pct: un
        # token tipo '80,00' puede ser money en una columna y pct en otra,
        # el algoritmo lo decide por matemática (cant×unit=imp / pub×dto).
        es_money = n is not None and not es_ean and not es_int_corto
        cls.append({
            'i': i, 'text': t, 'n': n,
            'es_ean': es_ean,
            'es_int_corto': es_int_corto,
            'es_money': es_money,
            'es_pct': es_pct,
        })
    tipos = [
        'ean' if c['es_ean'] else
        'int' if c['es_int_corto'] else
        'pct' if c['es_pct'] else
        'money' if c['es_money'] else
        ('text' if cls[i]['n'] is None else 'money')
        for i, c in enumerate(cls)
    ]
    asign = {}
    warnings = []

    # 2. EAN
    ean_idx = next((i for i, c in enumerate(cls) if c['es_ean']), -1)
    if ean_idx < 0:
        warnings.append('No encontré EAN (12-14 dígitos)')
    else:
        asign['codigo_barra'] = ean_idx

    # 3. Triplete cant×unit=imp (más cercano: minimizar k-i).
    TOL = 0.02
    start_from = ean_idx + 1 if ean_idx >= 0 else 0
    best = None
    for i in range(start_from, len(cls)):
        if not cls[i]['es_int_corto']:
            continue
        for j in range(i + 1, len(cls)):
            if not cls[j]['es_money']:
                continue
            for k in range(j + 1, len(cls)):
                if not cls[k]['es_money']:
                    continue
                vi, vj, vk = cls[i]['n'], cls[j]['n'], cls[k]['n']
                calc = vi * vj
                if abs(calc - vk) <= max(TOL, abs(vk) * 0.005):
                    if best is None or (k - i) < (best['k'] - best['i']):
                        best = {'i': i, 'j': j, 'k': k}
    if best is None:
        warnings.append('No pude deducir cant × unit = importe')
    else:
        asign['cantidad'] = best['i']
        asign['precio_unitario'] = best['j']
        asign['importe'] = best['k']

    # 4. Par pub × (1 - dto%) = unit (solo si tenemos unit)
    if 'precio_unitario' in asign:
        unit_idx = asign['precio_unitario']
        unit_n = cls[unit_idx]['n']
        for p in range(start_from, unit_idx):
            if not cls[p]['es_money']:
                continue
            for d in range(p + 1, unit_idx):
                if not cls[d]['es_pct']:
                    continue
                vp, vd = cls[p]['n'], cls[d]['n']
                if abs(vp * (1 - vd / 100) - unit_n) <= max(TOL, abs(unit_n) * 0.005):
                    asign['precio_publico'] = p
                    asign['dto'] = d
                    break
            if 'precio_publico' in asign:
                break

    # 5. Descripción: rango entre cant (excl.) y el primer asignado siguiente.
    cant_idx = asign.get('cantidad')
    if cant_idx is not None or ean_idx >= 0:
        desc_start = (cant_idx if cant_idx is not None else ean_idx) + 1
        # ancla = pub si lo encontramos, sino unit, sino imp.
        anchor = asign.get('precio_publico')
        if anchor is None:
            anchor = asign.get('precio_unitario')
        if anchor is None:
            anchor = asign.get('importe')
        if anchor is None:
            anchor = len(cls)
        desc_end = anchor - 1
        if desc_start <= desc_end:
            asign['descripcion'] = [desc_start, desc_end]

    # Valores normalizados: para cada campo asignado, devolver el valor
    # parseado y reformateado en AR limpio. Útil para que el frontend vuelque
    # un texto canonical en el input en vez del crudo OCR-roto.
    valores = {}
    for campo, idx in asign.items():
        if isinstance(idx, list):
            continue
        # EAN/codigo_barra: NO formatear, devolver el texto crudo del token
        # (ej. '7798129415043' debe quedar igual, no '7.798.129.415.043,00').
        if cls[idx]['es_ean'] or campo == 'codigo_barra':
            valores[campo] = str(cls[idx]['text']).strip()
            continue
        n = cls[idx]['n']
        if n is None:
            continue
        if campo == 'cantidad' or cls[idx]['es_int_corto']:
            valores[campo] = str(int(n))
        elif cls[idx]['es_pct']:
            # Pct: formato AR sin separador de miles
            txt = formatear_numero_ar(n, decimales=2)
            valores[campo] = txt.replace('.', '')   # 33,41 (sin miles para pct chico)
        else:
            valores[campo] = formatear_numero_ar(n, decimales=2)

    return {'asignaciones': asign, 'tipos': tipos, 'warnings': warnings,
            'valores': valores}


# ── Detección integral de una factura ──────────────────────────────────────

def detectar_factura_completa(texto):
    """Análisis automático del texto entero de una factura.

    Hace en una pasada lo que normalmente requiere muchos clicks del user:
    1. Tokeniza línea por línea.
    2. Encuentra la fila de ÍTEM con más campos auto-detectables (mejor
       candidata para 'fila de ejemplo').
    3. Encuentra la fila de TOTALES (heurística: contiene gravado×rate=iva
       o palabras clave + ≥3 numéricos).
    4. Aplica detectar_campos_factura y detectar_campos_totales.
    5. Cuenta cuántas otras filas comparten el patrón (estimación de
       productos detectables).

    Args:
        texto: string con el texto extraído del PDF (idealmente ya
            normalizado con _normalize_quadrupled).

    Returns:
        dict {
            'fila_ejemplo': str,
            'tokens_ejemplo': [str],
            'asignaciones_ejemplo': {campo: idx},
            'valores_ejemplo': {campo: str_canonical},
            'fila_totales': str,
            'tokens_totales': [str],
            'asignaciones_totales': {campo: idx},
            'valores_totales': {campo: str_canonical},
            'stats': {
                'filas_totales_pdf': N,
                'filas_compatibles': M,   # estimación de productos detectables
            },
            'warnings': [str],
        }
    """
    if not texto or not texto.strip():
        return {'warnings': ['Texto vacío'], 'stats': {}}

    lineas = [line.strip() for line in texto.split('\n') if line.strip()]
    warnings = []

    # Tokenizar cada línea (split por whitespace).
    filas = []
    for line in lineas:
        toks = line.split()
        if len(toks) >= 5:
            filas.append({'linea': line, 'tokens': toks})

    if not filas:
        return {'warnings': ['No hay líneas con ≥5 tokens'], 'stats': {'filas_totales_pdf': len(lineas)}}

    # Score por línea: cuántos campos auto-detecta detectar_campos_factura.
    # Solo nos quedamos con líneas que tengan al menos cant + unit + imp.
    candidatos = []
    for f in filas:
        det = detectar_campos_factura(f['tokens'])
        a = det['asignaciones']
        # Una fila de ítem REAL tiene al menos cantidad + precio_unitario + importe.
        if 'cantidad' in a and 'precio_unitario' in a and 'importe' in a:
            score = len([k for k in ('codigo_barra', 'cantidad', 'precio_unitario',
                                      'importe', 'descripcion', 'precio_publico', 'dto')
                         if k in a])
            candidatos.append({**f, 'det': det, 'score': score})

    fila_ejemplo = None
    if candidatos:
        # El "mejor candidato" es el que tiene más campos detectados.
        # Empate: el primero (suelen estar al principio del PDF).
        candidatos.sort(key=lambda x: -x['score'])
        fila_ejemplo = candidatos[0]
    else:
        warnings.append('No encontré ninguna fila con cant×unit=imp')

    # Fila de totales: buscar entre las últimas 30 líneas alguna que tenga
    # al menos 2 valores monetarios y cumpla iva = gravado × rate.
    fila_totales = None
    for f in reversed(filas[-30:] if len(filas) > 30 else filas):
        det_t = detectar_campos_totales(f['tokens'])
        a = det_t['asignaciones']
        if 'monto_gravado' in a and ('iva_21' in a or 'iva_105' in a):
            fila_totales = {**f, 'det': det_t}
            break
    # Si no encontró por matemática, fallback: la última línea con ≥3 moneys.
    if fila_totales is None:
        for f in reversed(filas):
            det_t = detectar_campos_totales(f['tokens'])
            n_moneys = sum(1 for t in det_t['tipos'] if t == 'money')
            if n_moneys >= 3:
                fila_totales = {**f, 'det': det_t}
                break
    if fila_totales is None:
        warnings.append('No encontré fila de totales')

    # Estimar cuántas filas más comparten el patrón.
    # Métrica simple: cuántas otras filas tienen al menos cant×unit=imp
    # detectado (las "compatibles" con el ejemplo).
    filas_compatibles = len(candidatos)

    out = {
        'fila_ejemplo': fila_ejemplo['linea'] if fila_ejemplo else '',
        'tokens_ejemplo': fila_ejemplo['tokens'] if fila_ejemplo else [],
        'asignaciones_ejemplo': fila_ejemplo['det']['asignaciones'] if fila_ejemplo else {},
        'valores_ejemplo': fila_ejemplo['det'].get('valores', {}) if fila_ejemplo else {},
        'fila_totales': fila_totales['linea'] if fila_totales else '',
        'tokens_totales': fila_totales['tokens'] if fila_totales else [],
        'asignaciones_totales': fila_totales['det']['asignaciones'] if fila_totales else {},
        'valores_totales': fila_totales['det'].get('valores', {}) if fila_totales else {},
        'stats': {
            'filas_totales_pdf': len(lineas),
            'filas_con_tokens': len(filas),
            'filas_compatibles': filas_compatibles,
        },
        'warnings': warnings,
    }
    return out


def detectar_campos_totales(tokens):
    """Asigna campos del pie de factura a tokens por matemática.

    Reemplaza `autodetectarTotales` JS de converter_pick.html. Usa
    `parsear_numero_ar` (tolerante a OCR-rotos como '1,183.326,62') para
    parsear los valores antes de buscar relaciones.

    Detecta:
    - cantidad_total: primer entero corto (1-9999) sin formato money.
    - monto_gravado + iva_21|iva_105: par donde iva ≈ gravado × rate.
    - total: el money mayor cuya suma cierra con los otros.
    - monto_exento, percepciones: los moneys remanentes (mayor → exento,
      menor → percepciones).

    Args:
        tokens: lista de strings (tokens de la fila de totales).

    Returns:
        dict {asignaciones, tipos, warnings}
    """
    if not tokens:
        return {'asignaciones': {}, 'tipos': [], 'warnings': []}

    # Tipar tokens. Para totales aceptamos como "money" cualquier número
    # parseable, no solo los formatos estrictos. parsear_numero_ar es
    # tolerante a OCR (1,183.326,62 → 1183326.62).
    cls = []
    for i, t in enumerate(tokens):
        n = parsear_numero_ar(t)
        es_int_corto = (n is not None and 1 <= n <= 9999 and n == int(n)
                        and bool(re.match(r'^\d{1,4}(?:[.,]0+)?$', str(t).strip())))
        es_pct = (n is not None and 0 <= n <= 100
                  and (str(t).strip().endswith('%')
                       or bool(_RE_PCT_AR.match(str(t).strip()))))
        # Money: cualquier número que NO sea int corto ni pct.
        es_money = n is not None and not es_int_corto and not es_pct
        cls.append({'i': i, 'text': t, 'n': n,
                    'es_int': es_int_corto, 'es_pct': es_pct, 'es_money': es_money})
    tipos = [
        'pct' if c['es_pct'] else
        'int' if c['es_int'] else
        'money' if c['es_money'] else
        'text'
        for c in cls
    ]
    asign = {}
    warnings = []
    moneys = [c for c in cls if c['es_money']]

    if len(moneys) < 2:
        warnings.append(f'Solo {len(moneys)} valores monetarios — no alcanza para deducir relaciones')
        # Aún así, asignar lo que se pueda
        ints = [c for c in cls if c['es_int']]
        if ints:
            asign['cantidad_total'] = ints[0]['i']
        return {'asignaciones': asign, 'tipos': tipos, 'warnings': warnings}

    TOL_REL = 0.005
    TOL_ABS = 0.05

    def _eq(a, b):
        return abs(a - b) <= max(TOL_ABS, abs(b) * TOL_REL)

    # 1. iva = gravado × rate (21% o 10.5%)
    pair = None
    for rate, campo_iva in ((0.21, 'iva_21'), (0.105, 'iva_105')):
        for g in moneys:
            for iv in moneys:
                if iv['i'] == g['i']:
                    continue
                if _eq(g['n'] * rate, iv['n']):
                    pair = {'gravado': g, 'iva': iv, 'campo_iva': campo_iva}
                    break
            if pair:
                break
        if pair:
            break

    # 2. total = sum(rest) → el mayor money debería ser ≈ suma de los demás.
    sorted_moneys = sorted(moneys, key=lambda x: -x['n'])
    total_tok = None
    if len(sorted_moneys) >= 2:
        biggest = sorted_moneys[0]
        rest_sum = sum(m['n'] for m in moneys) - biggest['n']
        if _eq(rest_sum, biggest['n']):
            total_tok = biggest

    if not pair and not total_tok:
        warnings.append('No pude deducir relaciones (iva/total). Asigná manualmente.')
        return {'asignaciones': asign, 'tipos': tipos, 'warnings': warnings}

    used = set()
    if pair:
        asign['monto_gravado'] = pair['gravado']['i']
        asign[pair['campo_iva']] = pair['iva']['i']
        used.add(pair['gravado']['i'])
        used.add(pair['iva']['i'])
    if total_tok:
        asign['total'] = total_tok['i']
        used.add(total_tok['i'])

    # cantidad_total: primer int corto que no esté usado.
    ints_libres = [c for c in cls if c['es_int'] and c['i'] not in used]
    if ints_libres:
        asign['cantidad_total'] = ints_libres[0]['i']

    # Remanentes: el mayor → exento, el menor → percepciones.
    remaining = sorted(
        [m for m in moneys if m['i'] not in used],
        key=lambda x: -x['n'],
    )
    if remaining:
        asign['monto_exento'] = remaining[0]['i']
    if len(remaining) >= 2:
        asign['percepciones'] = remaining[-1]['i']

    # Valores normalizados (ver explicación en detectar_campos_factura).
    valores = {}
    for campo, idx in asign.items():
        n = cls[idx]['n']
        if n is None:
            continue
        if campo == 'cantidad_total' or cls[idx]['es_int']:
            valores[campo] = str(int(n))
        else:
            valores[campo] = formatear_numero_ar(n, decimales=2)

    return {'asignaciones': asign, 'tipos': tipos, 'warnings': warnings,
            'valores': valores}
