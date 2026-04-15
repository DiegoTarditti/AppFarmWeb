"""
Parser para informe 'Evolución de ventas por producto' de ObServer Gestión.
Extrae: farmacia, laboratorio, período, y lista de productos con ventas mensuales.
"""
import re
import pdfplumber

MONTH_LABELS = [
    'Apr/25', 'May/25', 'Jun/25', 'Jul/25', 'Aug/25', 'Sep/25',
    'Oct/25', 'Nov/25', 'Dec/25', 'Jan/26', 'Feb/26', 'Mar/26'
]
COL_NUMERIC = ['Stock'] + MONTH_LABELS + ['Totales']


def _parse_int(s):
    """'1.550' o '-1' → int"""
    try:
        return int(s.replace('.', ''))
    except Exception:
        return 0


def _parse_price(s):
    """'$51435,00' → float"""
    s = s.replace('$', '').replace('.', '').replace(',', '.')
    try:
        return float(s)
    except Exception:
        return 0.0


def _group_by_row(words, y_tol=4):
    """Agrupa palabras por fila según posición Y."""
    rows = {}
    for w in words:
        key = round(w['top'] / y_tol) * y_tol
        rows.setdefault(key, []).append(w)
    for k in rows:
        rows[k].sort(key=lambda w: w['x0'])
    return dict(sorted(rows.items()))


def _build_col_positions(row_words):
    """Extrae posición X central de cada columna numérica del header."""
    positions = {}
    for w in row_words:
        if w['text'] in COL_NUMERIC:
            positions[w['text']] = (w['x0'] + w['x1']) / 2
    return positions


def _assign_numbers(num_words, col_positions):
    """Asigna cada número a su columna usando la posición X más cercana."""
    stock = 0
    ventas = [0] * 12
    total = 0

    if not col_positions or not num_words:
        return stock, ventas, total

    for w in num_words:
        wx = (w['x0'] + w['x1']) / 2
        nearest = min(col_positions, key=lambda k: abs(col_positions[k] - wx))
        val = _parse_int(w['text'])
        if nearest == 'Stock':
            stock = val
        elif nearest == 'Totales':
            total = val
        elif nearest in MONTH_LABELS:
            ventas[MONTH_LABELS.index(nearest)] = val

    return stock, ventas, total


def _extract_product_lab(row_words, price_w):
    """Separa nombre del producto y laboratorio de las palabras previas al precio."""
    pre = [w for w in row_words if w['x1'] <= price_w['x0'] + 2]
    if not pre:
        return '', ''

    # El laboratorio son las últimas palabras puramente alfabéticas y en mayúscula inicial
    lab_words = []
    name_words = list(pre)
    while name_words:
        t = name_words[-1]['text']
        if t.isalpha() and t[0].isupper():
            lab_words.insert(0, t)
            name_words.pop()
        else:
            break

    nombre = ' '.join(w['text'] for w in name_words)
    lab = ' '.join(lab_words)
    return nombre.strip(), lab.strip()


def parse_sales_history_pdf(path):
    """
    Parsea el informe de evolución de ventas de ObServer.
    Retorna dict con: farmacia, laboratorio, periodo, start_month (int 1-12),
    end_month (int 1-12), products (lista de dicts).
    """
    farmacia = ''
    laboratorio = ''
    periodo = ''
    start_month = 4   # default: April
    end_month = 3     # default: March
    products = []
    col_positions = {}

    with pdfplumber.open(path) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            words = page.extract_words(x_tolerance=2, y_tolerance=3)
            rows = _group_by_row(words)

            for _, row_words in rows.items():
                texts = [w['text'] for w in row_words]
                if not texts:
                    continue

                # -- Detectar fila de encabezado de columnas --
                if 'Stock' in texts and any(re.match(r'[A-Z][a-z]{2}/\d{2}', t) for t in texts):
                    col_positions = _build_col_positions(row_words)
                    continue

                # -- Metadata (página 1) --
                if page_idx == 0:
                    line = ' '.join(texts)
                    m = re.search(r'Período del (\d{2})/(\d{4}) al (\d{2})/(\d{4})', line)
                    if m:
                        periodo = line.strip()
                        start_month = int(m.group(1))
                        end_month = int(m.group(3))
                        continue
                    if 'Farmacia:' in texts:
                        idx = texts.index('Farmacia:')
                        farmacia = ' '.join(texts[idx + 1:]).strip()
                        continue

                # -- Filas de datos: deben tener precio ($) y código de barras --
                price_w = next((w for w in row_words if w['text'].startswith('$')), None)
                barcode_w = next((w for w in row_words if re.match(r'^\d{9,15}$', w['text'])), None)

                if not price_w or not barcode_w:
                    # Capturar nombre del laboratorio (línea solitaria, solo texto alfa)
                    if page_idx == 0 and len(texts) == 1 and texts[0].isalpha() and texts[0][0].isupper():
                        laboratorio = texts[0]
                    continue

                nombre, lab = _extract_product_lab(row_words, price_w)
                if not laboratorio and lab:
                    laboratorio = lab

                precio = _parse_price(price_w['text'])
                barcode = barcode_w['text']

                # Números después del código de barras
                post = [w for w in row_words if w['x0'] >= barcode_w['x1'] - 5]
                num_words = [w for w in post if re.match(r'^-?\d[\d.]*$', w['text'])]
                stock, ventas, total = _assign_numbers(num_words, col_positions)

                products.append({
                    'nombre': nombre,
                    'laboratorio': lab or laboratorio,
                    'precio_pvp': precio,
                    'codigo_barra': barcode,
                    'stock': stock,
                    'ventas': ventas,   # [Apr..Mar] 12 valores
                    'total': total,
                })

    return {
        'farmacia': farmacia,
        'laboratorio': laboratorio,
        'periodo': periodo,
        'start_month': start_month,
        'end_month': end_month,
        'products': products,
    }
