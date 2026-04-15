"""
Parser para informe 'Evolución de ventas por producto' de ObServer Gestión (formato Excel).
Extrae: farmacia, laboratorio, período, y lista de productos con ventas mensuales.
Retorna el mismo formato que parse_sales_history_pdf.
"""
import re
import openpyxl

MONTH_ABBR = {'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
              'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12}
MONTH_RE = re.compile(r'^([A-Z][a-z]{2})/(\d{2})$')
PERIOD_RE = re.compile(r'Per.odo del (\d{2})/(\d{4}) al (\d{2})/(\d{4})')


def _month_num(label):
    """'May/25' → 5"""
    m = MONTH_RE.match(str(label))
    return MONTH_ABBR.get(m.group(1)) if m else None


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def parse_sales_history_xls(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    farmacia = ''
    laboratorio = ''
    periodo = ''
    start_month = None
    end_month = None
    products = []

    # col_idx → month_num (from header row)
    month_cols = {}   # {col_idx: month_num}
    stock_col = None
    total_col = None
    header_row_idx = None

    for i, row in enumerate(rows):
        if not row or not any(c for c in row if c is not None):
            continue

        # Row 0: farmacia name (first non-None string)
        if i == 0:
            for c in row:
                if c and isinstance(c, str) and c.strip():
                    farmacia = c.strip()
                    break
            continue

        # Look for period info in any cell
        if start_month is None:
            for c in row:
                if c and isinstance(c, str):
                    m = PERIOD_RE.search(c)
                    if m:
                        start_month = int(m.group(1))
                        end_month = int(m.group(3))
                        periodo = f"Período del {m.group(1)}/{m.group(2)} al {m.group(3)}/{m.group(4)}"
                        # Laboratorio: after "Laboratorios?:\n"
                        lab_m = re.search(r'Laboratorios?:\s*\n\s*(.+?)(?:\n|$)', c)
                        if lab_m:
                            laboratorio = lab_m.group(1).strip()
                        break

        # Detect header row: contains 'Stock' + month labels
        if header_row_idx is None:
            if any(c == 'Stock' for c in row):
                for idx, c in enumerate(row):
                    if c == 'Stock':
                        stock_col = idx
                    elif c == 'Totales':
                        total_col = idx
                    elif c and isinstance(c, str):
                        mn = _month_num(c)
                        if mn:
                            month_cols[idx] = mn
                header_row_idx = i
                break

    if header_row_idx is None or not month_cols:
        return {'farmacia': farmacia, 'laboratorio': laboratorio, 'periodo': periodo,
                'start_month': start_month or 1, 'end_month': end_month or 12, 'products': []}

    # Build ordered month sequence: start_month → end_month (wrapping around year)
    if start_month and end_month:
        ordered_months = []
        m = start_month
        for _ in range(12):
            ordered_months.append(m)
            if m == end_month:
                break
            m = m % 12 + 1
    else:
        ordered_months = sorted(set(month_cols.values()))

    # Reverse lookup: month_num → col_idx
    month_to_col = {mn: idx for idx, mn in month_cols.items()}

    for row in rows[header_row_idx + 1:]:
        if not row or not row[0]:
            continue
        nombre = str(row[0]).strip() if isinstance(row[0], str) else ''
        if not nombre:
            continue
        # Skip footer/summary rows
        if any(kw in nombre for kw in ('ObServer', 'Totales', 'Total')):
            continue

        lab = str(row[3]).strip() if len(row) > 3 and row[3] else laboratorio
        precio = float(row[5]) if len(row) > 5 and row[5] else 0.0
        barcode = str(row[7]).strip() if len(row) > 7 and row[7] else ''

        if not barcode or not re.match(r'^\d{7,15}$', barcode):
            continue

        stock = _int(row[stock_col]) if stock_col is not None and stock_col < len(row) else 0

        ventas = []
        for mn in ordered_months:
            col = month_to_col.get(mn)
            v = _int(row[col]) if col is not None and col < len(row) and row[col] is not None else 0
            ventas.append(v)

        # Pad to 12 entries
        while len(ventas) < 12:
            ventas.append(0)

        total = _int(row[total_col]) if total_col is not None and total_col < len(row) and row[total_col] else sum(ventas)

        if not laboratorio and lab:
            laboratorio = lab

        products.append({
            'nombre': nombre,
            'laboratorio': lab or laboratorio,
            'precio_pvp': precio,
            'codigo_barra': barcode,
            'stock': stock,
            'ventas': ventas,
            'total': total,
        })

    return {
        'farmacia': farmacia,
        'laboratorio': laboratorio,
        'periodo': periodo,
        'start_month': start_month or ordered_months[0],
        'end_month': end_month or ordered_months[-1],
        'products': products,
    }
