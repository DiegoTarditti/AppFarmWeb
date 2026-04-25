"""
Parser OCR para imágenes de tablas de módulos de descuento.
Ejemplo: MODULOS ROEMMERS (imagen escaneada o foto).

Formato esperado (columnas):
  NOMBRE MODULO | CODIGO EAN | DESCRIPCION | CANT | DESC

Retorna la misma estructura que descuento_modulos_xls.py:
[
  {'nombre_modulo': 'MOD. OPTAMOX DUO', 'items': [
      {'codigo_ean': '...', 'descripcion': '...', 'cantidad': 2, 'descuento': 7.0},
  ]},
  ...
]
"""
import io
import re

import pytesseract
from PIL import Image, ImageEnhance, ImageFilter

# ──────────────────────────── helpers ──────────────────────────────

def _preprocess(img):
    """Mejora la imagen para OCR: escala de grises, contraste, binarización."""
    img = img.convert('L')                          # escala de grises
    img = ImageEnhance.Contrast(img).enhance(2.0)   # más contraste
    img = img.filter(ImageFilter.SHARPEN)
    # binarización simple: umbral adaptativo
    img = img.point(lambda p: 255 if p > 140 else 0)
    return img


def _clean(s):
    return ' '.join(str(s).split()).strip()


def _is_ean(s):
    """Detecta si la cadena parece un código EAN (7-15 dígitos)."""
    digits = re.sub(r'\D', '', s)
    return 7 <= len(digits) <= 15


def _to_int(s, default=1):
    try:
        return max(1, int(re.sub(r'\D', '', str(s)) or str(default)))
    except Exception:
        return default


def _to_float(s, default=0.0):
    try:
        s2 = re.sub(r'[^\d.,]', '', str(s)).replace(',', '.')
        return float(s2) if s2 else default
    except Exception:
        return default


# ──────────────────────────── parse por líneas ──────────────────────

def _parse_lines(text):
    """
    Parsea el texto extraído por OCR línea a línea.

    Estrategia:
    - Una línea es "fila de datos" si contiene un EAN (7+ dígitos consecutivos)
    - Una línea es "nombre de módulo" si no tiene EAN y parece texto de encabezado
      (ej: "MOD. OPTAMOX DUO", "MOD. VIMAX", etc.)
    - Las columnas CANT y DESC son los últimos 1-2 tokens numéricos de la línea
    """
    modulos = []
    current_mod = None
    current_name = ''

    for raw_line in text.splitlines():
        line = _clean(raw_line)
        if not line:
            continue

        # Ignorar encabezados de tabla y líneas de título
        if any(kw in line.upper() for kw in [
            'NOMBRE MODULO', 'CODIGO EAN', 'CODIGO SAN', 'DESCRIPCION',
            'MODULOS ROEMMERS', 'MODULOS ', 'DESC\n', 'CANT\n',
        ]):
            continue

        # ¿Contiene un EAN?
        ean_match = re.search(r'\b(\d{7,15})\b', line)
        if ean_match:
            ean = ean_match.group(1)

            # Quitar el EAN de la línea para procesar el resto
            rest = line[ean_match.end():].strip()

            # Los últimos tokens numéricos son CANT y DESC
            # Buscar patrón: ... <descripcion> <cant> <desc%>
            # Ej: "OPTAMOX DUO 10 COMP REC X 14  2  7%"
            tokens = rest.split()
            cant = 1
            dto = 0.0
            desc_words = []

            # Buscar desde atrás: último número = dto, penúltimo = cant (si hay dos)
            numeric_tail = []
            i = len(tokens) - 1
            while i >= 0:
                t = re.sub(r'[%°]', '', tokens[i])
                if re.match(r'^\d+([.,]\d+)?$', t):
                    numeric_tail.insert(0, (i, tokens[i]))
                    i -= 1
                else:
                    break

            if len(numeric_tail) >= 2:
                cant = _to_int(numeric_tail[-2][1])
                dto  = _to_float(numeric_tail[-1][1])
                desc_words = tokens[:numeric_tail[-2][0]]
            elif len(numeric_tail) == 1:
                dto  = _to_float(numeric_tail[-1][1])
                desc_words = tokens[:numeric_tail[-1][0]]
            else:
                desc_words = tokens

            descripcion = ' '.join(desc_words).strip()

            # Si la descripción está antes del EAN, tomarla del prefijo
            prefix = line[:ean_match.start()].strip()
            if not descripcion and prefix:
                # Quitar posible nombre de módulo del prefix
                descripcion = prefix

            if current_mod is None:
                current_mod = {'nombre_modulo': current_name or 'SIN NOMBRE', 'items': []}
                modulos.append(current_mod)

            current_mod['items'].append({
                'codigo_ean': ean,
                'descripcion': _clean(descripcion),
                'cantidad': cant,
                'descuento': dto,
            })

        else:
            # Línea sin EAN → posible nombre de módulo
            # Filtrar líneas muy cortas o solo números
            if len(line) >= 4 and not re.match(r'^\d+$', line):
                # Si parece un nombre de módulo (tiene letras, puede tener puntos)
                if re.search(r'[A-Za-z]', line):
                    if line != current_name:
                        current_name = line
                        current_mod = {'nombre_modulo': line, 'items': []}
                        modulos.append(current_mod)

    return [m for m in modulos if m['items']]


# ──────────────────────────── entry point ──────────────────────────

def parse_descuento_modulos_ocr(path_or_bytes):
    """
    Acepta ruta de archivo (str) o bytes.
    Soporta: JPG, PNG, PDF (primera página), TIFF.
    """
    if isinstance(path_or_bytes, (str, bytes)):
        if isinstance(path_or_bytes, str):
            suffix = path_or_bytes.lower()
            if suffix.endswith('.pdf'):
                # PDF → convertir primera página a imagen con pdfplumber
                import pdfplumber
                with pdfplumber.open(path_or_bytes) as pdf:
                    page = pdf.pages[0]
                    img = page.to_image(resolution=200).original
            else:
                img = Image.open(path_or_bytes)
        else:
            img = Image.open(io.BytesIO(path_or_bytes))
    else:
        img = path_or_bytes  # ya es PIL Image

    img = _preprocess(img)

    # OCR con configuración para tablas: PSM 6 = bloque de texto uniforme
    config = '--psm 6 -l spa+eng'
    text = pytesseract.image_to_string(img, config=config)

    return _parse_lines(text)
