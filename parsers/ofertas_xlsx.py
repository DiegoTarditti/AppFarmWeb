"""
Parser para Excel de productos en oferta.
Formato flexible: busca columnas que contengan EAN (13 dígitos) y descripción.
Salta filas de encabezado automáticamente.
"""


def parse_ofertas_xlsx(path):
    """
    Retorna lista de ofertas:
    [{'ean': '7793450121123', 'descripcion': 'PRODUCTO XXX'}, ...]
    """
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active

    # Detectar qué columna tiene EAN (valor numérico de ~13 dígitos o string de dígitos)
    def is_ean(val):
        if val is None:
            return False
        s = str(val).strip().replace('.0', '')
        return s.isdigit() and 7 <= len(s) <= 14

    ean_col = None
    desc_col = None

    # Escanear primeras 5 filas para detectar estructura
    rows = list(ws.iter_rows(min_row=1, max_row=5, values_only=True))
    for row in rows:
        for ci, val in enumerate(row):
            if is_ean(val) and ean_col is None:
                ean_col = ci
            elif ean_col is not None and ci != ean_col and val and not is_ean(val):
                desc_col = ci
                break
        if ean_col is not None and desc_col is not None:
            break

    if ean_col is None:
        # Fallback: col 0 = EAN, col 1 = desc
        ean_col, desc_col = 0, 1

    if desc_col is None:
        desc_col = 1 if ean_col != 1 else 2

    ofertas = []
    seen = set()
    for row in ws.iter_rows(min_row=1, values_only=True):
        if not row or len(row) <= max(ean_col, desc_col):
            continue
        ean_val = row[ean_col]
        desc_val = row[desc_col]
        if not is_ean(ean_val):
            continue
        ean = str(ean_val).strip().replace('.0', '')
        desc = str(desc_val).strip() if desc_val else ''
        if ean not in seen:
            seen.add(ean)
            ofertas.append({'ean': ean, 'descripcion': desc})

    return ofertas
