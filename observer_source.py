"""Capa de acceso a la DB real de ObServer (SQL Server 2014).

Lee de las vistas DW.* vía pymssql. Las conexiones se abren y cierran por query
(el pool de pymssql no es thread-safe y no compensa para el caudal que manejamos).

Config vía env vars (en docker-compose.yml / .env):

    OBSERVER_HOST=192.168.1.137
    OBSERVER_PORT=54572         # puerto TCP dinámico o fijo de la instancia
    OBSERVER_USER=usuarioDW
    OBSERVER_PASS=...
    OBSERVER_DB=ObServerGestion
    OBSERVER_TDSVER=7.0         # SQL Server 2014 requiere TDS 7.0 via FreeTDS
    OBSERVER_ID_FARMACIA=10525  # filtro por sucursal para vistas multi-farmacia

Si OBSERVER_HOST no está seteado, `observer_disponible()` devuelve False
y todas las funciones devuelven listas vacías (no rompe la app si no hay acceso).
"""
import os
import logging

try:
    import pymssql
except ImportError:
    pymssql = None

_log = logging.getLogger(__name__)

# Forzar versión TDS antes de cualquier conexión
os.environ.setdefault('TDSVER', os.environ.get('OBSERVER_TDSVER', '7.0'))


def _config():
    """Lee config de entorno. Devuelve dict o None si falta algo obligatorio."""
    host = os.environ.get('OBSERVER_HOST', '').strip()
    if not host or pymssql is None:
        return None
    return {
        'host':      host,
        'port':      int(os.environ.get('OBSERVER_PORT', '1433')),
        'user':      os.environ.get('OBSERVER_USER', '').strip(),
        'password':  os.environ.get('OBSERVER_PASS', '').strip(),
        'database':  os.environ.get('OBSERVER_DB', 'ObServerGestion').strip(),
        'id_farmacia': int(os.environ.get('OBSERVER_ID_FARMACIA', '10525')),
    }


def _connect(timeout=10):
    """Abre una conexión nueva. Llamador debe cerrarla."""
    cfg = _config()
    if not cfg:
        return None
    return pymssql.connect(
        server=cfg['host'], port=cfg['port'],
        user=cfg['user'], password=cfg['password'],
        database=cfg['database'],
        timeout=timeout, login_timeout=timeout,
    )


def observer_disponible():
    """True si la DB responde a un ping simple en <5s."""
    if not _config():
        return False
    try:
        conn = _connect(timeout=5)
        if conn is None:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            return True
        finally:
            conn.close()
    except Exception as e:
        _log.warning('ObServer no responde: %s', e)
        return False


# ───────────────────────────────────────────────────────────────────────────
# Laboratorios
# ───────────────────────────────────────────────────────────────────────────

def get_laboratorios_dw():
    """Devuelve todos los laboratorios activos de DW.Laboratorios.

    Lista de dicts con: {'id': int, 'nombre': str}. Lista vacía si no hay conexión.
    """
    conn = _connect()
    if conn is None:
        return []
    try:
        with conn.cursor(as_dict=True) as cur:
            cur.execute("""
                SELECT IdLaboratorio, Descripcion
                FROM DW.Laboratorios
                WHERE FechaBaja IS NULL
                ORDER BY Descripcion
            """)
            return [{'id': int(r['IdLaboratorio']),
                     'nombre': (r['Descripcion'] or '').strip()}
                    for r in cur.fetchall()]
    finally:
        conn.close()


# ───────────────────────────────────────────────────────────────────────────
# Stubs — se implementan en fases siguientes cuando las use alguna ruta
# ───────────────────────────────────────────────────────────────────────────

def get_laboratorios_disponibles():
    """Alias legacy. Devuelve solo nombres para los dropdowns existentes."""
    return [{'nombre': l['nombre'], 'n_articulos': 0} for l in get_laboratorios_dw()]


def get_articulo(codigo_barra):
    """TODO Fase 5 (mapeo EAN↔IdProducto)."""
    return None


def get_ventas_12_meses(codigo_barra, anio_hasta, mes_hasta):
    """TODO Fase 2. Hoy devuelve ceros para no romper callers."""
    return [0] * 12


def get_ventas_laboratorio(laboratorio, anio_hasta, mes_hasta):
    """TODO Fase 2 (ventas por lab desde DW.ProductosVendidos)."""
    return []


def get_recepciones_factura(numero_factura, proveedor_cuit=None):
    """TODO Fase 4. No hay vista de recepciones expuesta aún."""
    return []


def get_stock(codigo_barra):
    """TODO Fase 3. Requiere mapeo EAN↔IdProducto."""
    return None
