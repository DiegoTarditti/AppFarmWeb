"""
Parser para Excel de módulos de laboratorio.

Detecta automáticamente 2 formatos:

  FORMATO A (5 cols — nuestra plantilla):
    Fila 1: Título
    Fila 2: NOMBRE MÓDULO | CÓDIGO EAN | DESCRIPCIÓN | CANT. | DESC. %
    Fila módulo: (nombre, None, None, None, None)
    Fila ítem:   (nombre, ean, descripcion, cant, desc_pct)

  FORMATO B (6 cols — Excel real de laboratorio, ej. Roemmers):
    Fila 1: (None, Título, ...)
    Fila 2: COD MOD. | NOMBRE MODULO | CODIGO EAN | DESCRIPCION | CANT, | DESC, %
    Fila módulo: (None, nombre, None, None, None, None)
    Fila ítem:   (cod, nombre, ean, descripcion, cant, desc_pct)
"""


def _detect_format(ws):
    """
    Devuelve 'A' (5 cols, plantilla) o 'B' (6 cols, lab real).
    Busca la fila de encabezados y analiza las columnas.
    """
    for row in ws.iter_rows(min_row=1, max_row=5, values_only=True):
        if not row or row[0] is None:
            continue
        vals = [str(v).upper().strip() if v else '' for v in row]
        # Formato B: primera columna es "COD MOD" o similar
        if vals[0] in ('COD MOD.', 'COD MOD', 'CODIGO MODULO', 'CÓD. MOD.'):
            return 'B'
        # Formato A: primera columna es el nombre del módulo
        if vals[0] in ('NOMBRE MÓDULO', 'NOMBRE MODULO', 'MÓDULO', 'MODULO'):
            return 'A'
    return 'A'  # default


def _norm_ean(val):
    """Normaliza un valor de celda a string EAN. Retorna None si inválido."""
    if val is None:
        return None
    try:
        return str(int(float(str(val).strip())))
    except (ValueError, TypeError):
        s = str(val).strip()
        return s if s else None


def _parse_int(val, default=1):
    try:
        return int(val) if val is not None else default
    except (ValueError, TypeError):
        return default


def _parse_float(val, default=0.0):
    try:
        return float(val) if val is not None else default
    except (ValueError, TypeError):
        return default


def parse_modulos_xlsx(path):
    """
    Retorna lista de módulos:
    [
        {
            'nombre': 'MOD. OPTAMOX DUO',
            'items': [
                {'ean': '7793450121123', 'descripcion': '...', 'cant': 10, 'desc_pct': 7.0},
                ...
            ]
        },
        ...
    ]
    """
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active

    fmt = _detect_format(ws)

    modules = []
    current = None

    for row in ws.iter_rows(min_row=1, values_only=True):
        if not row or all(v is None for v in row):
            continue

        # ── FORMATO B ────────────────────────────────────────────────
        if fmt == 'B':
            print(f"[DEBUG B] row={row}")
            cod      = row[0] if len(row) > 0 else None
            nombre   = str(row[1]).strip() if len(row) > 1 and row[1] else None
            ean_raw  = row[2] if len(row) > 2 else None
            desc     = str(row[3]).strip() if len(row) > 3 and row[3] else ''
            cant     = row[4] if len(row) > 4 else None
            desc_pct = row[5] if len(row) > 5 else 0

            # Saltar fila de encabezados de columna
            if nombre and str(nombre).upper() in ('NOMBRE MODULO', 'NOMBRE MÓDULO'):
                continue

            # Cabecera de módulo: col[0] vacío y cols[2..5] todas vacías
            # Usamos "not x" en vez de "is None" para cubrir también strings vacíos
            tail_empty = all(
                not row[i]
                for i in range(2, min(6, len(row)))
            )
            if not cod and tail_empty and nombre:
                if current is not None:
                    modules.append(current)
                current = {'nombre': nombre, 'items': []}
                continue

            # Fila de ítem: necesita al menos un nombre en col[1]
            if not nombre:
                continue

            ean = _norm_ean(ean_raw)
            if not ean:
                # Fallback: EAN puede estar en col[1] (layout real Roemmers: COD MOD | EAN | vacío | DESC | CANT | %)
                ean = _norm_ean(nombre)
            if not ean:
                # EAN ausente → saltar ítem pero NO cortar el módulo
                continue

            if current is None:
                current = {'nombre': nombre, 'items': []}

            current['items'].append({
                'ean':         ean,
                'descripcion': desc,
                'cant':        _parse_int(cant),
                'desc_pct':    _parse_float(desc_pct),
            })
            continue   # ← FORMAT B completamente manejado aquí

        # ── FORMATO A ────────────────────────────────────────────────
        nombre   = str(row[0]).strip() if row[0] else None
        ean_raw  = row[1] if len(row) > 1 else None
        desc     = str(row[2]).strip() if len(row) > 2 and row[2] else ''
        cant     = row[3] if len(row) > 3 else None
        desc_pct = row[4] if len(row) > 4 else 0

        # Saltar fila de encabezados de columna
        if nombre and str(nombre).upper() in ('NOMBRE MÓDULO', 'NOMBRE MODULO', 'MÓDULO', 'MODULO'):
            continue

        ean = _norm_ean(ean_raw)

        if ean is None:
            # Fila de cabecera de módulo (sin EAN)
            if nombre and (current is None or nombre != current['nombre']):
                if current is not None:
                    modules.append(current)
                current = {'nombre': nombre, 'items': []}
        else:
            # Fila de ítem — si el nombre del módulo en col[0] cambió respecto al
            # current, abrir módulo nuevo (formato Siegfried donde el nombre se
            # repite por cada fila, en vez de aparecer como cabecera separada).
            if current is None or (nombre and nombre != current['nombre']):
                if current is not None:
                    modules.append(current)
                current = {'nombre': nombre or 'SIN NOMBRE', 'items': []}
            current['items'].append({
                'ean':         ean,
                'descripcion': desc,
                'cant':        _parse_int(cant),
                'desc_pct':    _parse_float(desc_pct),
            })

    if current is not None:
        modules.append(current)

    # Filtrar módulos sin ítems (ej. fila de título global)
    return [m for m in modules if m['items']]
