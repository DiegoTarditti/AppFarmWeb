"""Explora columnas y sample de las vistas DW.Clientes / CategoriasClientes / GruposClientes.

Uso:
    python scripts/explorar_clientes_observer.py

Output esperado: lista de columnas de cada vista + 3 filas de sample con valores
reales (sin nulls). Lo usamos para armar los modelos ObsCliente, ObsCategoriaCliente,
ObsGrupoCliente + sync + ABM.

Copiá/pegá TODO el output.
"""
import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

os.environ.setdefault('TDSVER', os.environ.get('OBSERVER_TDSVER', '7.0'))

try:
    import pymssql
except ImportError:
    print('ERROR: falta pymssql.  pip install pymssql')
    sys.exit(1)


def _connect():
    host = os.environ.get('OBSERVER_HOST', '').strip()
    if not host:
        print('ERROR: OBSERVER_HOST no seteado.')
        sys.exit(1)
    return pymssql.connect(
        server = host,
        port = int(os.environ.get('OBSERVER_PORT', '1433')),
        user = os.environ.get('OBSERVER_USER', '').strip(),
        password = os.environ.get('OBSERVER_PASS', '').strip(),
        database = os.environ.get('OBSERVER_DB', 'ObServerGestion').strip(),
        tds_version = os.environ.get('OBSERVER_TDSVER', '7.0'),
        timeout = 30,
    )


def _hdr(t):
    print()
    print('=' * 72)
    print(f'  {t}')
    print('=' * 72)


def inspeccionar(cur, schema, tabla):
    _hdr(f'{schema}.{tabla}')
    cur.execute("""
        SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, IS_NULLABLE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
        ORDER BY ORDINAL_POSITION
    """, (schema, tabla))
    cols = cur.fetchall()
    if not cols:
        print(f'(vista no accesible: {schema}.{tabla})')
        return

    print(f'{"COLUMNA":<35} {"TIPO":<15} LEN/PREC   NULL')
    print('-' * 75)
    for c, dt, mx, np, null in cols:
        print(f'{c:<35} {dt:<15} {str(mx or np or ""):<10} {null}')

    # Sample 3 filas con al menos algún valor
    col_names = [c[0] for c in cols]
    col_list = ', '.join(f'[{c}]' for c in col_names)
    try:
        cur.execute(f'SELECT TOP 3 {col_list} FROM [{schema}].[{tabla}]')
        rows = cur.fetchall()
        print('\n  Sample (3 filas, solo campos con valor):')
        for i, r in enumerate(rows, 1):
            print(f'\n  Fila {i}:')
            for col, val in zip(col_names, r):
                if val is not None and val != '':
                    s = str(val)
                    if len(s) > 60:
                        s = s[:57] + '...'
                    print(f'    {col:<35} = {s}')
    except Exception as e:
        print(f'  (error sample: {e})')

    # Conteo total
    try:
        cur.execute(f'SELECT COUNT(*) FROM [{schema}].[{tabla}]')
        n = cur.fetchone()[0]
        print(f'\n  TOTAL filas: {n:,}')
    except Exception as e:
        print(f'  (error count: {e})')


def main():
    print('Conectando a ObServer...')
    conn = _connect()
    cur = conn.cursor()
    try:
        inspeccionar(cur, 'DW', 'Clientes')
        inspeccionar(cur, 'DW', 'CategoriasClientes')
        inspeccionar(cur, 'DW', 'GruposClientes')
        inspeccionar(cur, 'DW', 'ObrasSociales')
        inspeccionar(cur, 'DW', 'Convenios')
        inspeccionar(cur, 'DW', 'Planes')
        print()
        print('=' * 72)
        print('LISTO. Copiá TODO el output y pegalo en el chat.')
        print('=' * 72)
    finally:
        cur.close()
        conn.close()


if __name__ == '__main__':
    main()
