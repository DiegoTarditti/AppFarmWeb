"""Explora qué tablas de ObServer contienen códigos de barra / EAN (v2, más amplio).

Uso:
    python scripts/explorar_codigos_observer.py

La v1 buscaba por nombre de columna (ean, barra, codigo…) y no encontró nada.
Esto sugiere que:
  (a) el usuario DW no tiene acceso al schema que los tiene, o
  (b) la columna se llama de otra forma (CAlt, Cod1, CodNac, etc.).

Esta v2 hace 3 exploraciones:
  1. Lista TODAS las tablas/vistas accesibles (no solo DW) → para ver qué hay.
  2. Lista TODAS las columnas de DW.Productos (ahí puede estar el EAN inline).
  3. Busca columnas con patrones más amplios: Cod*, Alt*, Nac*, Barra*, numeric(13).
  4. Sample de 3 filas de DW.Productos para ver todos los campos reales.

Copiá/pegá el output completo.
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
    print('ERROR: falta pymssql. Instalalo con:  pip install pymssql')
    sys.exit(1)


def _connect():
    host = os.environ.get('OBSERVER_HOST', '').strip()
    if not host:
        print('ERROR: OBSERVER_HOST no está seteado.')
        sys.exit(1)
    return pymssql.connect(
        server   = host,
        port     = int(os.environ.get('OBSERVER_PORT', '1433')),
        user     = os.environ.get('OBSERVER_USER', '').strip(),
        password = os.environ.get('OBSERVER_PASS', '').strip(),
        database = os.environ.get('OBSERVER_DB', 'ObServerGestion').strip(),
        tds_version = os.environ.get('OBSERVER_TDSVER', '7.0'),
        timeout  = 30,
    )


def _hdr(t):
    print()
    print('=' * 72)
    print(f'  {t}')
    print('=' * 72)


def listar_schemas_y_tablas(cur):
    _hdr('1) TODAS LAS TABLAS/VISTAS ACCESIBLES')
    cur.execute("""
        SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE
        FROM INFORMATION_SCHEMA.TABLES
        ORDER BY TABLE_SCHEMA, TABLE_NAME
    """)
    rows = cur.fetchall()
    schemas = {}
    for sch, tab, tp in rows:
        schemas.setdefault(sch, []).append((tab, tp))
    for sch, items in sorted(schemas.items()):
        print(f'\n  [{sch}]  ({len(items)} objetos)')
        for tab, tp in items:
            flag = 'V' if tp == 'VIEW' else 'T'
            print(f'    {flag} {tab}')


def columnas_de_dw_productos(cur):
    _hdr('2) COLUMNAS COMPLETAS DE DW.Productos')
    cur.execute("""
        SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA='DW' AND TABLE_NAME='Productos'
        ORDER BY ORDINAL_POSITION
    """)
    rows = cur.fetchall()
    if not rows:
        print('(DW.Productos no accesible)')
        return []
    print(f'{"COLUMNA":<35} {"TIPO":<15} LEN/PREC')
    print('-' * 70)
    for c, dt, mx, np in rows:
        print(f'{c:<35} {dt:<15} {mx or np or ""}')
    return [r[0] for r in rows]


def sample_dw_productos(cur, cols):
    _hdr('3) SAMPLE DE 3 FILAS DE DW.Productos (todas las columnas)')
    if not cols:
        print('(no hay columnas para mostrar)')
        return
    col_list = ', '.join(f'[{c}]' for c in cols)
    try:
        cur.execute(f'SELECT TOP 3 {col_list} FROM DW.Productos')
        rows = cur.fetchall()
        for i, r in enumerate(rows, 1):
            print(f'\n  Fila {i}:')
            for col, val in zip(cols, r):
                if val is not None and val != '':
                    print(f'    {col:<35} = {val}')
    except Exception as e:
        print(f'(error: {e})')


def buscar_columnas_amplias(cur):
    _hdr('4) COLUMNAS CON NOMBRES EXÓTICOS PARA CÓDIGOS (Cod, Alt, Nac, Nro…)')
    cur.execute("""
        SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE,
               CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE COLUMN_NAME LIKE 'Cod%'
           OR COLUMN_NAME LIKE '%Alt%'
           OR COLUMN_NAME LIKE '%Nac%'
           OR COLUMN_NAME LIKE '%Nro%'
           OR COLUMN_NAME LIKE '%Troq%'
           OR COLUMN_NAME LIKE '%Kairos%'
           OR COLUMN_NAME LIKE '%Alfabeta%'
           OR COLUMN_NAME LIKE '%Manual%'
           OR COLUMN_NAME = 'IdOwner'
        ORDER BY TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME
    """)
    rows = cur.fetchall()
    if not rows:
        print('(nada)')
        return
    print(f'{"SCHEMA":<10} {"TABLA":<35} {"COLUMNA":<30} {"TIPO":<12} LEN')
    print('-' * 100)
    for sch, tab, col, dt, mx, np in rows:
        print(f'{sch:<10} {tab:<35} {col:<30} {dt:<12} {mx or np or ""}')


def buscar_tablas_codigos(cur):
    _hdr('4b) TABLAS CON NOMBRE "codigos*" (pista del usuario)')
    cur.execute("""
        SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_NAME LIKE '%odigo%'
           OR TABLE_NAME LIKE '%Codigo%'
           OR TABLE_NAME LIKE '%codigo%'
           OR TABLE_NAME LIKE 'Cod%'
        ORDER BY TABLE_SCHEMA, TABLE_NAME
    """)
    rows = cur.fetchall()
    if not rows:
        print('(no se encontró ninguna tabla con "codigo" en el nombre)')
        return
    print(f'{"SCHEMA":<10} {"TABLA":<40} TIPO')
    print('-' * 70)
    for sch, tab, tp in rows:
        print(f'{sch:<10} {tab:<40} {tp}')
    print()
    # Para cada una, mostrar columnas + sample
    for sch, tab, tp in rows:
        print(f'\n  ── Columnas de [{sch}].[{tab}] ──')
        cur.execute(f"""
            SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA='{sch}' AND TABLE_NAME='{tab}'
            ORDER BY ORDINAL_POSITION
        """)
        cols = cur.fetchall()
        for c, dt, mx in cols:
            print(f'    - {c:<30} {dt}{f"({mx})" if mx else ""}')
        # Sample
        col_names = [c[0] for c in cols]
        if col_names:
            try:
                col_list = ', '.join(f'[{c}]' for c in col_names[:10])
                cur.execute(f'SELECT TOP 3 {col_list} FROM [{sch}].[{tab}]')
                for r in cur.fetchall():
                    print(f'      {r}')
            except Exception as e:
                print(f'      (no se pudo samplear: {e})')


def buscar_varchars_largos_numericos(cur):
    _hdr('5) COLUMNAS varchar(13..20) — candidatos a EAN por forma')
    cur.execute("""
        SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE DATA_TYPE IN ('varchar','nvarchar','char','nchar')
          AND CHARACTER_MAXIMUM_LENGTH BETWEEN 10 AND 20
          AND TABLE_NAME LIKE '%rod%'
        ORDER BY TABLE_SCHEMA, TABLE_NAME
    """)
    rows = cur.fetchall()
    if not rows:
        print('(nada)')
        return
    print(f'{"SCHEMA":<10} {"TABLA":<35} {"COLUMNA":<25} {"TIPO":<12} LEN')
    print('-' * 95)
    for sch, tab, col, dt, mx in rows:
        print(f'{sch:<10} {tab:<35} {col:<25} {dt:<12} {mx}')


def main():
    print('Conectando a ObServer...')
    conn = _connect()
    cur = conn.cursor()
    try:
        listar_schemas_y_tablas(cur)
        cols = columnas_de_dw_productos(cur)
        sample_dw_productos(cur, cols)
        buscar_columnas_amplias(cur)
        buscar_tablas_codigos(cur)
        buscar_varchars_largos_numericos(cur)
        print()
        print('=' * 72)
        print('LISTO. Copiá TODO el output y pegalo en el chat.')
        print('=' * 72)
    finally:
        cur.close()
        conn.close()


if __name__ == '__main__':
    main()
