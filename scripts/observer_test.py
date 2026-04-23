"""Test de conexión a la DB real de ObServer (SQL Server).

Prueba varias formas de conexión (instance, puerto 1433, puerto custom)
y al conectar lista tablas/vistas disponibles.

Uso:
    python scripts/observer_test.py

Config: lee OBSERVER_HOST / OBSERVER_USER / OBSERVER_PASS / OBSERVER_DB del env,
o usa los defaults de abajo para pruebas locales rápidas.
"""
import os
import sys
# Fuerza stdout UTF-8 para Windows
if hasattr(sys.stdout, 'reconfigure'):
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

HOST = os.environ.get('OBSERVER_HOST', '192.168.1.137')
USER = os.environ.get('OBSERVER_USER', 'usuarioDW')
PASS = os.environ.get('OBSERVER_PASS', 'UDW_FarmaciaFS2025')
DB   = os.environ.get('OBSERVER_DB', 'ObserverGestion')
INSTANCE = os.environ.get('OBSERVER_INSTANCE', 'BADIA')
PORT = os.environ.get('OBSERVER_PORT')  # opcional, si hay puerto TCP fijo

try:
    import pymssql
except ImportError:
    print("pymssql no instalado. Ejecutá:  pip install pymssql")
    sys.exit(1)


def intentar(label, **kwargs):
    print(f"\n> Intento: {label}")
    print(f"  kwargs: {kwargs}")
    try:
        conn = pymssql.connect(user=USER, password=PASS, database=DB,
                               timeout=10, login_timeout=10, **kwargs)
        with conn.cursor() as cur:
            cur.execute("SELECT @@VERSION")
            version = cur.fetchone()[0][:80]
            print(f"  [OK] CONECTADO - {version}")
            return conn
    except Exception as e:
        print(f"  [X] Fallo: {type(e).__name__}: {e}")
        return None


def intentar_tds(port):
    """Prueba varios TDS version contra el puerto dado."""
    for tds in ('7.4', '7.3', '7.2', '7.1', '7.0'):
        os.environ['TDSVER'] = tds
        conn = intentar(f"port {port} TDSVER={tds}", server=HOST, port=port)
        if conn:
            return conn
    return None


def listar_schema(conn):
    with conn.cursor(as_dict=True) as cur:
        # DBs disponibles
        cur.execute("SELECT name FROM sys.databases ORDER BY name")
        print(f"\nDatabases visibles: {[r['name'] for r in cur.fetchall()]}")
        # Cambiar a la DB objetivo por las dudas
        cur.execute(f"USE [{DB}]")
        # Tablas de usuario
        cur.execute("""
            SELECT s.name AS esquema, t.name AS tabla,
                   p.rows AS filas_aprox
            FROM sys.tables t
            JOIN sys.schemas s ON s.schema_id = t.schema_id
            LEFT JOIN sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0,1)
            ORDER BY p.rows DESC
        """)
        tablas = cur.fetchall()
        print(f"\n{len(tablas)} tablas en {DB}:")
        for t in tablas[:40]:
            print(f"  {t['esquema']}.{t['tabla']:<40} ~{t['filas_aprox']:>12} filas")
        if len(tablas) > 40:
            print(f"  ... ({len(tablas) - 40} más)")
        # Vistas
        cur.execute("""
            SELECT s.name AS esquema, v.name AS vista
            FROM sys.views v
            JOIN sys.schemas s ON s.schema_id = v.schema_id
            ORDER BY v.name
        """)
        vistas = cur.fetchall()
        print(f"\n{len(vistas)} vistas en {DB}:")
        for v in vistas[:40]:
            print(f"  {v['esquema']}.{v['vista']}")
        if len(vistas) > 40:
            print(f"  ... ({len(vistas) - 40} más)")


def main():
    print(f"ObServer test · host={HOST}  user={USER}  db={DB}  instance={INSTANCE}")

    conn = None
    # 1. puerto explicito si el user lo seteo — probar varias TDS versions
    if PORT:
        conn = intentar_tds(int(PORT))
    # 2. host\instance (requiere SQL Browser en UDP 1434)
    if not conn and INSTANCE:
        conn = intentar(f"instance '{HOST}\\{INSTANCE}'", server=f"{HOST}\\{INSTANCE}")
    # 3. puerto 1433 (default)
    if not conn:
        conn = intentar("port 1433 (default)", server=HOST, port=1433)

    if not conn:
        print("\n[ERROR] No se pudo conectar con ninguna forma.")
        print("  Posibles causas:")
        print("  - SQL Server Browser apagado (si es instance) > pedirle al admin el puerto TCP fijo y setear OBSERVER_PORT")
        print("  - Firewall de Windows bloqueando")
        print("  - Usuario no tiene permisos de SQL auth (solo Windows auth)")
        sys.exit(2)

    try:
        listar_schema(conn)
    finally:
        conn.close()
    print("\nListo. Pasale este output al chat para ver qué tablas/vistas corresponden a articulos/ventas/etc.")


if __name__ == '__main__':
    main()
