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

        if fmt == 'B':
            # col[0]=cod_mod, col[1]=nombre, col[2]=ean, col[3]=desc, col[4]=cant, col[5]=desc_pct
            cod    = row[0] if len(row) > 0 else None
            nombre = str(row[1]).strip() if len(row) > 1 and row[1] else None
            ean    = row[2] if len(row) > 2 else None
            desc   = str(row[3]).strip() if len(row) > 3 and row[3] else ''
            cant   = row[4] if len(row) > 4 else None
            desc_pct = row[5] if len(row) > 5 else 0

            # Saltar fila de encabezados
            if nombre and str(nombre).upper() in ('NOMBRE MODULO', 'NOMBRE MÓDULO'):
                continue
            # Saltar título global (col[0]=None, col[1]=título que no es un módulo conocido)
            if cod is None and ean is None and nombre:
                # Podría ser cabecera de módulo o título global
                # Si es la primera fila con este patrón y no hay current → título global
                if current is not None:
                    modules.append(current)
                current = {'nombre': nombre, 'items': []}
                continue

        else:
            # Formato A: col[0]=nombre, col[1]=ean, col[2]=desc, col[3]=cant, col[4]=desc_pct
            nombre = str(row[0]).strip() if row[0] else None
            ean    = row[1] if len(row) > 1 else None
            desc   = str(row[2]).strip() if len(row) > 2 and row[2] else ''
            cant   = row[3] if len(row) > 3 else None
            desc_pct = row[4] if len(row) > 4 else 0

            # Saltar fila de encabezados de columnas
            if nombre and str(nombre).upper() in ('NOMBRE MÓDULO', 'NOMBRE MODULO', 'MÓDULO', 'MODULO'):
                continue

        if ean is None:
            # Fila de cabecera de módulo (o título global)
            if nombre:
                if current is not None:
                    modules.append(current)
                current = {'nombre': nombre, 'items': []}
        else:
            # Fila de ítem
            if current is None:
                current = {'nombre': nombre or 'SIN NOMBRE', 'items': []}
            try:
                cant_int = int(cant) if cant is not None else 1
            except (ValueError, TypeError):
                cant_int = 1
            try:
                desc_float = float(desc_pct) if desc_pct is not None else 0.0
            except (ValueError, TypeError):
                desc_float = 0.0

            current['items'].append({
                'ean': str(ean).strip(),
                'descripcion': desc,
                'cant': cant_int,
                'desc_pct': desc_float,
            })

    if current is not None:
        modules.append(current)

    # Filtrar módulos sin ítems (ej. fila de título global)
    return [m for m in modules if m['items']]
