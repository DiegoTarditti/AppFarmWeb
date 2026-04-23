"""Capa de acceso y sincronización con la DB real de ObServer (SQL Server 2014).

Funciones `sync_*(session)` leen vistas DW.* vía pymssql y upsertean en las
tablas locales `obs_*`. Cada una acepta una SQLAlchemy session abierta y
devuelve un dict con stats { 'upsert': N, 'duracion_ms': X }.

Config vía env vars:

    OBSERVER_HOST=192.168.1.137
    OBSERVER_PORT=54572
    OBSERVER_USER=usuarioDW
    OBSERVER_PASS=...
    OBSERVER_DB=ObServerGestion
    OBSERVER_TDSVER=7.0
    OBSERVER_ID_FARMACIA=10525
"""
import os
import time
import logging

try:
    import pymssql
except ImportError:
    pymssql = None

_log = logging.getLogger(__name__)

os.environ.setdefault('TDSVER', os.environ.get('OBSERVER_TDSVER', '7.0'))


def _config():
    host = os.environ.get('OBSERVER_HOST', '').strip()
    if not host or pymssql is None:
        return None
    return {
        'host':        host,
        'port':        int(os.environ.get('OBSERVER_PORT', '1433')),
        'user':        os.environ.get('OBSERVER_USER', '').strip(),
        'password':    os.environ.get('OBSERVER_PASS', '').strip(),
        'database':    os.environ.get('OBSERVER_DB', 'ObServerGestion').strip(),
        'id_farmacia': int(os.environ.get('OBSERVER_ID_FARMACIA', '10525')),
    }


def _connect(timeout=30):
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


# ──────────────────────────────────────────────────────────────────────────
# Syncs — cada función abre su propia conexión al source y recibe la session
# local como parámetro para el upsert. Devuelve dict con stats.
# ──────────────────────────────────────────────────────────────────────────

def _log_sync(session, entidad, upsert, duracion_ms, error=None):
    from database import ObsSyncLog
    session.add(ObsSyncLog(entidad=entidad, filas_upsert=upsert,
                            duracion_ms=duracion_ms, error=error))


def _upsert_obs(session, Model, pk_col, pk_value, **fields):
    """Upsert simple: si existe PK actualiza, si no crea."""
    obj = session.get(Model, pk_value)
    if obj is None:
        obj = Model(**{pk_col: pk_value, **fields})
        session.add(obj)
        return 'insert'
    for k, v in fields.items():
        setattr(obj, k, v)
    return 'update'


def sync_laboratorios(session):
    from database import ObsLaboratorio, now_ar
    t0 = time.time()
    conn = _connect()
    if conn is None:
        raise RuntimeError('ObServer no configurado o pymssql no disponible')
    n = 0
    try:
        with conn.cursor(as_dict=True) as cur:
            cur.execute("SELECT IdLaboratorio, Descripcion, FechaBaja FROM DW.Laboratorios")
            for r in cur.fetchall():
                _upsert_obs(
                    session, ObsLaboratorio, 'observer_id',
                    int(r['IdLaboratorio']),
                    descripcion=(r['Descripcion'] or '').strip() or '(sin descripcion)',
                    fecha_baja=r['FechaBaja'],
                    sync_en=now_ar(),
                )
                n += 1
    finally:
        conn.close()
    duracion = int((time.time() - t0) * 1000)
    _log_sync(session, 'laboratorios', n, duracion)
    return {'upsert': n, 'duracion_ms': duracion}


def sync_rubros(session):
    from database import ObsRubro, now_ar
    t0 = time.time()
    conn = _connect()
    if conn is None:
        raise RuntimeError('ObServer no configurado')
    n = 0
    try:
        with conn.cursor(as_dict=True) as cur:
            cur.execute("SELECT * FROM DW.Rubros")
            cols = [d[0] for d in cur.description]
            id_col = _pick(cols, ['IdRubro', 'Id_Rubro', 'Id'])
            desc_col = _pick(cols, ['Descripcion', 'Rubro', 'Nombre'])
            for r in cur.fetchall():
                _upsert_obs(
                    session, ObsRubro, 'observer_id',
                    int(r[id_col]),
                    descripcion=(r[desc_col] or '').strip() or '(sin descripcion)',
                    sync_en=now_ar(),
                )
                n += 1
    finally:
        conn.close()
    duracion = int((time.time() - t0) * 1000)
    _log_sync(session, 'rubros', n, duracion)
    return {'upsert': n, 'duracion_ms': duracion}


def sync_subrubros(session):
    from database import ObsSubrubro, now_ar
    t0 = time.time()
    conn = _connect()
    if conn is None:
        raise RuntimeError('ObServer no configurado')
    n = 0
    try:
        with conn.cursor(as_dict=True) as cur:
            cur.execute("SELECT * FROM DW.Subrubros")
            cols = [d[0] for d in cur.description]
            id_col = _pick(cols, ['IdSubrubro', 'IdSubRubro', 'Id_Subrubro', 'Id'])
            desc_col = _pick(cols, ['Descripcion', 'Subrubro', 'Nombre'])
            rubro_col = _pick(cols, ['IdRubro', 'Id_Rubro'], required=False)
            for r in cur.fetchall():
                _upsert_obs(
                    session, ObsSubrubro, 'observer_id',
                    int(r[id_col]),
                    descripcion=(r[desc_col] or '').strip() or '(sin descripcion)',
                    rubro_observer=int(r[rubro_col]) if rubro_col and r[rubro_col] is not None else None,
                    sync_en=now_ar(),
                )
                n += 1
    finally:
        conn.close()
    duracion = int((time.time() - t0) * 1000)
    _log_sync(session, 'subrubros', n, duracion)
    return {'upsert': n, 'duracion_ms': duracion}


def sync_nombres_drogas(session):
    from database import ObsNombreDroga, now_ar
    t0 = time.time()
    conn = _connect()
    if conn is None:
        raise RuntimeError('ObServer no configurado')
    n = 0
    try:
        with conn.cursor(as_dict=True) as cur:
            cur.execute("SELECT * FROM DW.NombresDrogas")
            cols = [d[0] for d in cur.description]
            id_col = _pick(cols, ['IdNombresDrogas', 'IdNombreDroga', 'Id'])
            desc_col = _pick(cols, ['Descripcion', 'NombreDroga', 'NombresDrogas', 'Nombre'])
            for r in cur.fetchall():
                _upsert_obs(
                    session, ObsNombreDroga, 'observer_id',
                    int(r[id_col]),
                    descripcion=(r[desc_col] or '').strip() or '(sin descripcion)',
                    sync_en=now_ar(),
                )
                n += 1
    finally:
        conn.close()
    duracion = int((time.time() - t0) * 1000)
    _log_sync(session, 'nombres_drogas', n, duracion)
    return {'upsert': n, 'duracion_ms': duracion}


def sync_productos(session):
    """Sync DW.Productos. Requiere tener laboratorios/subrubros/nombres_drogas ya sincronizados
    (por FKs)."""
    from database import ObsProducto, now_ar
    t0 = time.time()
    conn = _connect(timeout=120)
    if conn is None:
        raise RuntimeError('ObServer no configurado')
    n = 0
    try:
        with conn.cursor(as_dict=True) as cur:
            cur.execute("""
                SELECT IdProducto, Producto, IdLaboratorio, IdSubRubro, IdNombresDrogas,
                       CodigoAlfabeta, Troquel, CantidadDelEnvase,
                       EsHabilitadoVenta, RequiereCadenaFrio, FechaBaja
                FROM DW.Productos
            """)
            for r in cur.fetchall():
                lab = r['IdLaboratorio']
                sub = r['IdSubRubro']
                droga = r['IdNombresDrogas']
                _upsert_obs(
                    session, ObsProducto, 'observer_id',
                    int(r['IdProducto']),
                    descripcion=(r['Producto'] or '').strip() or '(sin descripcion)',
                    laboratorio_observer=int(lab) if lab is not None else None,
                    subrubro_observer=int(sub) if sub is not None else None,
                    nombre_droga_observer=int(droga) if droga is not None else None,
                    codigo_alfabeta=(r['CodigoAlfabeta'] or '').strip() or None,
                    troquel=int(r['Troquel']) if r['Troquel'] is not None else None,
                    cantidad_envase=r['CantidadDelEnvase'],
                    es_habilitado_venta=bool(r['EsHabilitadoVenta']),
                    requiere_cadena_frio=bool(r['RequiereCadenaFrio']),
                    fecha_baja=r['FechaBaja'],
                    sync_en=now_ar(),
                )
                n += 1
                # Commit parcial cada 5000 para no llenar transacción
                if n % 5000 == 0:
                    session.flush()
    finally:
        conn.close()
    duracion = int((time.time() - t0) * 1000)
    _log_sync(session, 'productos', n, duracion)
    return {'upsert': n, 'duracion_ms': duracion}


def sync_stock(session, id_farmacia=None):
    """Sync DW.StockFarmaciasProductos. Requiere obs_productos poblado primero.
    Si id_farmacia=None usa OBSERVER_ID_FARMACIA del env."""
    from database import ObsStock, ObsProducto, now_ar
    t0 = time.time()
    cfg = _config()
    if not cfg:
        raise RuntimeError('ObServer no configurado')
    if id_farmacia is None:
        id_farmacia = cfg['id_farmacia']

    # Set de observer_id válidos en nuestra copia local (la FK apunta acá)
    ids_validos = {pid for (pid,) in session.query(ObsProducto.observer_id).all()}

    conn = _connect(timeout=120)
    if conn is None:
        raise RuntimeError('ObServer no configurado')
    n = skipped = 0
    try:
        with conn.cursor(as_dict=True) as cur:
            cur.execute("""
                SELECT IdProducto, StockActual, Maximo, Minimo
                FROM DW.StockFarmaciasProductos
                WHERE IdFarmacia = %d
            """, (int(id_farmacia),))
            for r in cur.fetchall():
                pid = int(r['IdProducto'])
                if pid not in ids_validos:
                    skipped += 1
                    continue
                pk = (int(id_farmacia), pid)
                obj = session.get(ObsStock, pk)
                if obj is None:
                    obj = ObsStock(id_farmacia=pk[0], producto_observer=pk[1])
                    session.add(obj)
                obj.stock_actual = int(r['StockActual'] or 0)
                obj.maximo = int(r['Maximo']) if r['Maximo'] is not None else None
                obj.minimo = int(r['Minimo']) if r['Minimo'] is not None else None
                obj.sync_en = now_ar()
                n += 1
                if n % 5000 == 0:
                    session.flush()
    finally:
        conn.close()
    duracion = int((time.time() - t0) * 1000)
    error = f'{skipped} huerfanos ignorados (producto sin match local)' if skipped else None
    _log_sync(session, 'stock', n, duracion, error)
    return {'upsert': n, 'duracion_ms': duracion, 'skipped': skipped}


def _pick(cols, candidates, required=True):
    """Devuelve la primera columna de candidates que exista en cols (case-insensitive).
    Si required=True y no hay match, raises; si required=False devuelve None."""
    col_low = {c.lower(): c for c in cols}
    for c in candidates:
        if c.lower() in col_low:
            return col_low[c.lower()]
    if required:
        raise RuntimeError(f'Ninguna de {candidates} en columnas {cols}')
    return None


# ──────────────────────────────────────────────────────────────────────────
# Helpers legacy que usan los routes/observer.py — wrappers sobre tablas
# locales obs_* para no tener que pegarle a SQL Server en cada request.
# ──────────────────────────────────────────────────────────────────────────

def get_laboratorios_disponibles():
    """Lee laboratorios del espejo local. El sync se hace por separado."""
    from database import get_db, ObsLaboratorio
    with get_db() as session:
        labs = (session.query(ObsLaboratorio)
                .filter(ObsLaboratorio.fecha_baja.is_(None))
                .order_by(ObsLaboratorio.descripcion).all())
        return [{'nombre': l.descripcion, 'n_articulos': 0, 'observer_id': l.observer_id}
                for l in labs]
