"""Busca columnas que parezcan EAN / codigo de barra en todas las vistas de DW."""
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

EAN_HINTS = ['barra', 'ean', 'gtin', 'codigo', 'barcode', 'troquel', 'alfabeta', 'kairos']


def main():
    conn = pymssql.connect(server=HOST, port=PORT, user=USER, password=PASS,
                           database=DB, timeout=15, login_timeout=15)
    print(f"Conectado a {DB}\n")

    with conn.cursor(as_dict=True) as cur:
        # Todas las columnas de todas las vistas
        cur.execute("""
            SELECT s.name + '.' + v.name AS vista, c.name AS col, t.name AS tipo, c.max_length AS len
            FROM sys.views v
            JOIN sys.schemas s ON s.schema_id = v.schema_id
            JOIN sys.columns c ON c.object_id = v.object_id
            JOIN sys.types   t ON t.user_type_id = c.user_type_id
            ORDER BY s.name, v.name, c.column_id
        """)
        rows = cur.fetchall()
        print(f"Total columnas en todas las vistas: {len(rows)}\n")

        print("Matches por hint (EAN/barra/etc):")
        print("-" * 70)
        matches = []
        for r in rows:
            col_low = r['col'].lower()
            for h in EAN_HINTS:
                if h in col_low:
                    print(f"  {r['vista']:<40} {r['col']:<30} {r['tipo']}({r['len']})")
                    matches.append(r)
                    break

        print(f"\nTotal matches: {len(matches)}")

        # Explorar valores sample de DW.ProductosHistorico si existe (completo)
        print("\n" + "=" * 70)
        print("DW.ProductosHistorico - columnas completas + sample")
        print("=" * 70)
        cur.execute("""
            SELECT c.name AS col, t.name AS tipo, c.max_length AS len, c.is_nullable AS nullable
            FROM sys.views v
            JOIN sys.schemas s ON s.schema_id = v.schema_id
            JOIN sys.columns c ON c.object_id = v.object_id
            JOIN sys.types   t ON t.user_type_id = c.user_type_id
            WHERE s.name = 'DW' AND v.name = 'ProductosHistorico'
            ORDER BY c.column_id
        """)
        for r in cur.fetchall():
            null = 'NULL' if r['nullable'] else 'NOT NULL'
            print(f"    {r['col']:<40} {r['tipo']:<12}({r['len']:<5}) {null}")

        cur.execute("SELECT TOP 3 * FROM DW.ProductosHistorico")
        for i, r in enumerate(cur.fetchall(), 1):
            print(f"\n    --- fila {i} ---")
            for k, v in r.items():
                sv = str(v)[:80] if v is not None else '(null)'
                print(f"      {k:<40} = {sv}")


if __name__ == '__main__':
    main()
