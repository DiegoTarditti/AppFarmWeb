"""Busca columnas que parezcan precio/PVP/costo/monto en todas las vistas DW."""
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

HINTS = ['recio', 'pvp', 'pub', 'valor', 'importe', 'costo', 'monto']


def main():
    conn = pymssql.connect(server=HOST, port=PORT, user=USER, password=PASS,
                           database=DB, timeout=15, login_timeout=15)
    print(f"Conectado a {DB}\n")
    with conn.cursor(as_dict=True) as cur:
        cur.execute("""
            SELECT s.name + '.' + v.name AS vista, c.name AS col, t.name AS tipo, c.max_length AS len
            FROM sys.views v
            JOIN sys.schemas s ON s.schema_id = v.schema_id
            JOIN sys.columns c ON c.object_id = v.object_id
            JOIN sys.types   t ON t.user_type_id = c.user_type_id
            WHERE s.name = 'DW'
            ORDER BY s.name, v.name, c.column_id
        """)
        rows = cur.fetchall()

        print("Columnas con hints de precio/costo/monto en DW.*:")
        print("-" * 80)
        matches = []
        for r in rows:
            col_low = r['col'].lower()
            for h in HINTS:
                if h in col_low:
                    print(f"  {r['vista']:<40} {r['col']:<30} {r['tipo']}({r['len']})")
                    matches.append(r)
                    break
        print(f"\nTotal matches: {len(matches)}")


if __name__ == '__main__':
    main()
