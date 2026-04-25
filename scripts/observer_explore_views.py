"""Introspección de columnas + sample de las vistas DW clave de ObServer.

Asume que observer_test.py ya confirmó la conexión. Corré:

    set OBSERVER_PORT=54572
    set TDSVER=7.0
    python scripts/observer_explore_views.py

Salida: tabla de columnas de cada vista + primeras 3 filas.
"""
import os
import sys

if hasattr(sys.stdout, 'reconfigure'):
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

import pymssql

HOST = os.environ.get('OBSERVER_HOST', '192.168.1.137')
USER = os.environ.get('OBSERVER_USER', 'usuarioDW')
PASS = os.environ.get('OBSERVER_PASS', 'UDW_FarmaciaFS2025')
DB   = os.environ.get('OBSERVER_DB',   'ObServerGestion')
PORT = int(os.environ.get('OBSERVER_PORT', '54572'))
os.environ.setdefault('TDSVER', '7.0')

# Vistas priorizadas para la integracion inicial
VISTAS = [
    'DW.Productos',
    'DW.Laboratorios',
    'DW.StockFarmaciasProductos',
    'DW.ProductosVendidos',
    'DW.ProductosHistorico',
    'DW.NombresDrogas',
    'DW.Rubros',
    'DW.Subrubros',
    'DW.ObrasSociales',
    'DW.Convenios',
    'DW.Planes',
    'DW.Clientes',
    'DW.Farmacias',
    'DW.CondicionesComerciales',
    'DW.Medicos',
]


def main():
    conn = pymssql.connect(server=HOST, port=PORT, user=USER, password=PASS,
                           database=DB, timeout=15, login_timeout=15)
    print(f"Conectado a {DB} en {HOST}:{PORT}\n")

    with conn.cursor(as_dict=True) as cur:
        for full_name in VISTAS:
            esquema, nombre = full_name.split('.', 1)
            print('=' * 80)
            print(f"  {full_name}")
            print('=' * 80)
            # Contar filas (rapido con COUNT_BIG; las vistas suelen tener indice)
            try:
                cur.execute(f"SELECT COUNT_BIG(*) AS n FROM {full_name}")
                n = cur.fetchone()['n']
                print(f"  Filas: {n:,}")
            except Exception as e:
                print(f"  Filas: error ({e})")
            # Columnas
            cur.execute("""
                SELECT c.name AS col, t.name AS tipo, c.max_length AS len,
                       c.is_nullable AS nullable
                FROM sys.views v
                JOIN sys.schemas s ON s.schema_id = v.schema_id
                JOIN sys.columns c ON c.object_id = v.object_id
                JOIN sys.types t ON t.user_type_id = c.user_type_id
                WHERE s.name = %s AND v.name = %s
                ORDER BY c.column_id
            """, (esquema, nombre))
            cols = cur.fetchall()
            print(f"\n  Columnas ({len(cols)}):")
            for c in cols:
                null = 'NULL' if c['nullable'] else 'NOT NULL'
                print(f"    {c['col']:<40} {c['tipo']:<12} {null}")
            # Sample
            try:
                cur.execute(f"SELECT TOP 3 * FROM {full_name}")
                rows = cur.fetchall()
                if rows:
                    print("\n  Sample (3 filas):")
                    for i, r in enumerate(rows, 1):
                        print(f"    --- fila {i} ---")
                        for k, v in r.items():
                            sv = str(v)[:80] if v is not None else '(null)'
                            print(f"      {k:<40} = {sv}")
            except Exception as e:
                print(f"  Sample: error ({e})")
            print()


if __name__ == '__main__':
    main()
