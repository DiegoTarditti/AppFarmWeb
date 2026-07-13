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
import logging
import os
import time

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


# Cache module-level del resultado de "¿puedo leer schema Gestion?". Lo
# probamos UNA vez por proceso para no spammear el server con tests cada
# vez que arranca un sync premium. Se invalida en restart (cuando cambian
# las credenciales OBSERVER_USER/PASS).
# None = no testeado todavía, True/False = última respuesta cacheada.
_ACCESO_GESTION = None


def _test_acceso_gestion():
    """Hace UN query mínimo al schema Gestion para detectar si tenemos
    permisos. Devuelve True/False. Cachea el resultado.

    Diego 2026-06-24: en la farmacia de Lisandro tenemos credenciales SA
    con acceso a Gestion.* (precios exactos, condiciones comerciales, etc).
    En otras farmacias solo hay usuarioDW con acceso a DW.* — las features
    premium se skipean limpio para no romper el sync general.
    """
    global _ACCESO_GESTION
    if _ACCESO_GESTION is not None:
        return _ACCESO_GESTION
    try:
        conn = _connect(timeout=5)
        if conn is None:
            _ACCESO_GESTION = False
            return False
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT TOP 1 1 FROM Gestion.CondicionesComerciales")
                cur.fetchone()
            _ACCESO_GESTION = True
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        _log.info('Sin acceso al schema Gestion (%s). Las features premium '
                  'se saltarán: precios_vigentes, condiciones_comerciales.',
                  type(e).__name__)
        _ACCESO_GESTION = False
    return _ACCESO_GESTION


def reset_cache_acceso_gestion():
    """Invalida el cache (útil tras cambiar OBSERVER_USER/PASS en runtime)."""
    global _ACCESO_GESTION
    _ACCESO_GESTION = None


def diagnostico_acceso():
    """Diagnóstico para /admin/observer/diagnostico. Devuelve qué schemas
    son accesibles + nivel de privilegio del usuario actual."""
    cfg = _config()
    info = {
        'host': cfg['host'] if cfg else None,
        'port': cfg['port'] if cfg else None,
        'user': cfg['user'] if cfg else None,
        'database': cfg['database'] if cfg else None,
        'observer_disponible': observer_disponible(),
        'schemas': {},
        'es_sysadmin': False,
        'server_name': None,
        'features_premium_disponibles': [],
    }
    if not info['observer_disponible']:
        return info
    try:
        conn = _connect(timeout=10)
        if conn is None:
            return info
        with conn.cursor() as cur:
            cur.execute("SELECT @@SERVERNAME, IS_SRVROLEMEMBER('sysadmin')")
            r = cur.fetchone()
            info['server_name'] = r[0]
            info['es_sysadmin'] = bool(r[1])
            for schema, tabla_probe in (('DW', 'DW.Productos'),
                                         ('Gestion', 'Gestion.CondicionesComerciales'),
                                         ('dbo', 'dbo.FW_Permisos')):
                try:
                    cur.execute(f"SELECT TOP 1 1 FROM {tabla_probe}")
                    cur.fetchone()
                    info['schemas'][schema] = True
                except Exception:  # noqa: BLE001
                    info['schemas'][schema] = False
        conn.close()
        # Mapeo schema → features
        if info['schemas'].get('Gestion'):
            info['features_premium_disponibles'].extend([
                'precios_vigentes', 'condiciones_comerciales',
            ])
    except Exception as e:  # noqa: BLE001
        info['error'] = str(e)[:200]
    return info


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


def observer_analisis_disponible():
    """True si hay datos locales de ventas para hacer análisis, o si SQL Server responde.

    La primera opción cubre el caso típico: farmacia sincroniza ObServer → los datos
    están en obs_* (locales o bajados vía pull_from_render). Para ANALIZAR no hace
    falta tener conexión viva a SQL Server.
    """
    try:
        from database import ObsVentaMensual, get_db
        with get_db() as s:
            if s.query(ObsVentaMensual).limit(1).first():
                return True
    except Exception as e:
        _log.warning('No pude chequear obs_ventas_mensuales: %s', e)
    return observer_disponible()


# ──────────────────────────────────────────────────────────────────────────
# Exploración del schema (read-only)
# ──────────────────────────────────────────────────────────────────────────

def explorar_schema(schema='DW', sample_rows=5, table=None):
    """Lista tablas/views del schema indicado (default DW) con columnas y filas
    de ejemplo. Read-only, no toca nada en Observer.

    Args:
        schema: schema a explorar (default 'DW').
        sample_rows: cuántas filas de muestra traer por tabla (default 5, max 20).
        table: si está, solo explora esa tabla específica (más rápido).

    Returns:
        dict {
            'tables': [
                {
                    'name': 'ProductosVendidos',
                    'columns': [{'name': 'IdProducto', 'type': 'int', 'nullable': False}, ...],
                    'sample': [{col: val, ...}, ...],
                    'error': None | str,
                },
                ...
            ],
            'errors': [...]
        }
    """
    sample_rows = max(1, min(20, int(sample_rows or 5)))
    conn = _connect(timeout=60)
    if conn is None:
        raise RuntimeError('ObServer no configurado')
    out = {'tables': [], 'errors': []}
    try:
        with conn.cursor(as_dict=True) as cur:
            if table:
                tablas = [{'TABLE_NAME': table, 'TABLE_TYPE': 'BASE TABLE'}]
            else:
                cur.execute("""
                    SELECT TABLE_NAME, TABLE_TYPE
                    FROM INFORMATION_SCHEMA.TABLES
                    WHERE TABLE_SCHEMA = %s
                    ORDER BY TABLE_NAME
                """, (schema,))
                tablas = list(cur.fetchall())

            for t in tablas:
                tname = t['TABLE_NAME']
                entry = {
                    'name': tname,
                    'type': t.get('TABLE_TYPE', '?'),
                    'columns': [],
                    'sample': [],
                    'row_count': None,
                    'error': None,
                }
                try:
                    cur.execute("""
                        SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, CHARACTER_MAXIMUM_LENGTH
                        FROM INFORMATION_SCHEMA.COLUMNS
                        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                        ORDER BY ORDINAL_POSITION
                    """, (schema, tname))
                    for c in cur.fetchall():
                        t_str = c['DATA_TYPE']
                        if c.get('CHARACTER_MAXIMUM_LENGTH') and c['CHARACTER_MAXIMUM_LENGTH'] != -1:
                            t_str += f"({c['CHARACTER_MAXIMUM_LENGTH']})"
                        entry['columns'].append({
                            'name':     c['COLUMN_NAME'],
                            'type':     t_str,
                            'nullable': c['IS_NULLABLE'] == 'YES',
                        })

                    # Muestra: TOP N
                    cur.execute(f"SELECT TOP {sample_rows} * FROM [{schema}].[{tname}]")
                    rows = cur.fetchall()
                    # Convertir tipos no serializables a string
                    for r in rows:
                        clean = {}
                        for k, v in r.items():
                            if v is None:
                                clean[k] = None
                            elif isinstance(v, (int, float, str, bool)):
                                clean[k] = v
                            else:
                                clean[k] = str(v)
                        entry['sample'].append(clean)
                except Exception as e:
                    entry['error'] = str(e)
                    out['errors'].append(f'{tname}: {e}')
                out['tables'].append(entry)
    finally:
        conn.close()
    return out


# ──────────────────────────────────────────────────────────────────────────
# SQL playground read-only (admin/dev) — para explorar Observer ad-hoc.
# ──────────────────────────────────────────────────────────────────────────

import re as _re


def ejecutar_sql_readonly(query, max_rows=200, timeout=30):
    """Ejecuta una query SOLO si empieza con SELECT (whitelist). Sin DDL/DML.
    Devuelve {'cols': [...], 'rows': [...], 'truncated': bool}.
    Lanza ValueError si la query no es read-only.
    """
    if not query or not query.strip():
        raise ValueError('Query vacía')
    q = query.strip().rstrip(';').strip()
    # Strip leading comments (-- o /* */) si los hay
    q_stripped = _re.sub(r'^(--[^\n]*\n|/\*.*?\*/\s*)+', '', q, flags=_re.DOTALL).lstrip()
    # Whitelist: solo SELECT o WITH (CTE) — todo lo demás es write o DDL.
    primer_token = q_stripped.split(None, 1)[0].upper() if q_stripped else ''
    if primer_token not in ('SELECT', 'WITH'):
        raise ValueError(f'Solo SELECT o WITH (CTE) permitidos. Recibí: {primer_token!r}')
    # Blacklist defensiva: aunque empiece con SELECT, no permitir keywords destructivos.
    bad = _re.search(
        r'\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|EXEC|EXECUTE|MERGE|GRANT|REVOKE|BACKUP|RESTORE)\b',
        q_stripped, _re.IGNORECASE)
    if bad:
        raise ValueError(f'Keyword no permitido: {bad.group(1).upper()}')
    max_rows = max(1, min(int(max_rows or 200), 5000))
    conn = _connect(timeout=timeout)
    if conn is None:
        raise RuntimeError('ObServer no disponible')
    try:
        with conn.cursor(as_dict=True) as cur:
            cur.execute(q)
            try:
                rows = cur.fetchmany(max_rows + 1)
            except Exception:
                rows = []
            truncated = len(rows) > max_rows
            if truncated:
                rows = rows[:max_rows]
            cols = [d[0] for d in (cur.description or [])]
            return {'cols': cols, 'rows': rows, 'truncated': truncated, 'count': len(rows)}
    finally:
        try: conn.close()
        except Exception: pass


# ──────────────────────────────────────────────────────────────────────────
# Syncs — cada función abre su propia conexión al source y recibe la session
# local como parámetro para el upsert. Devuelve dict con stats.
# ──────────────────────────────────────────────────────────────────────────

def _log_sync(session, entidad, upsert, duracion_ms, error=None, info=None):
    """Registra el resultado de un sync.

    - error: solo para errores REALES (excepción, mensaje de falla). Se pinta rojo.
    - info: nota descriptiva del sync exitoso (rango de meses, contadores, etc.). Se pinta gris.
    """
    from database import ObsSyncLog
    session.add(ObsSyncLog(entidad=entidad, filas_upsert=upsert,
                            duracion_ms=duracion_ms, error=error, info=info))


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
                       CodigoAlfabeta, IdTipoVentaYControl, Troquel, CantidadDelEnvase,
                       EsHabilitadoVenta, RequiereCadenaFrio, EsFraccionable, FechaBaja
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
                    id_tipo_venta_control=(r['IdTipoVentaYControl'] or '').strip() or None,
                    troquel=int(r['Troquel']) if r['Troquel'] is not None else None,
                    cantidad_envase=r['CantidadDelEnvase'],
                    es_habilitado_venta=bool(r['EsHabilitadoVenta']),
                    requiere_cadena_frio=bool(r['RequiereCadenaFrio']),
                    es_fraccionable=bool(r['EsFraccionable']) if r['EsFraccionable'] is not None else False,
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


def sync_precios_vigentes(session):
    """Sync de Gestion.ProductosPreciosVigentes → obs_productos.precio_lista.

    Esta vista de Observer expone el precio actual vigente por producto
    (lo mismo que se ve en la UI de Observer). Sincronizando esto tenemos
    el precio EXACTO, sin promedios históricos ni dumps manuales.

    Requiere usuario con permisos sobre el schema `Gestion`. El `usuarioDW`
    no lee ese schema → si la query falla por permisos, loggea warning y
    devuelve sin tocar nada. Diego 2026-06-24: pasar a `sa` para tener el
    precio actualizado automático.
    """
    from sqlalchemy import text

    from database import now_ar
    t0 = time.time()
    conn = _connect(timeout=120)
    if conn is None:
        raise RuntimeError('ObServer no configurado')
    actualizados = 0
    sin_match = 0
    try:
        ahora = now_ar()
        # Traer todos los observer_id existentes localmente (universo a updatear)
        existentes = {r[0] for r in session.execute(
            text('SELECT observer_id FROM obs_productos')
        ).fetchall()}
        # Pull masivo de la vista. Procesamos en batches para no llenar memoria.
        with conn.cursor(as_dict=True) as cur:
            try:
                cur.execute("""
                    SELECT IdProducto, Precio, FechaVigencia
                      FROM Gestion.ProductosPreciosVigentes
                """)
            except Exception as e:  # noqa: BLE001
                _log.warning('sync_precios_vigentes: sin acceso a '
                             'Gestion.ProductosPreciosVigentes (%s). Asegurate '
                             'de usar OBSERVER_USER con permiso al schema Gestion.', e)
                return {'upsert': 0, 'duracion_ms': int((time.time() - t0) * 1000),
                        'error': 'permiso denegado'}

            # UPDATE en batches de 1000 vía SQL crudo (el bulk ORM se queja
            # con bindparam en la WHERE clause). Suficiente para 123k
            # productos en ~10s.
            stmt = text("""
                UPDATE obs_productos
                   SET precio_lista = :precio,
                       precio_lista_fecha_vigencia = :fv,
                       precio_lista_actualizado_en = :ahora
                 WHERE observer_id = :oid
            """)
            BATCH = 1000
            buffer = []
            for r in cur:
                obs_id = int(r['IdProducto'])
                if obs_id not in existentes:
                    sin_match += 1
                    continue
                buffer.append({
                    'oid': obs_id, 'precio': float(r['Precio']),
                    'fv': r['FechaVigencia'], 'ahora': ahora,
                })
                if len(buffer) >= BATCH:
                    session.execute(stmt, buffer)
                    actualizados += len(buffer)
                    buffer.clear()
            if buffer:
                session.execute(stmt, buffer)
                actualizados += len(buffer)
        session.flush()
    finally:
        conn.close()
    duracion = int((time.time() - t0) * 1000)
    _log_sync(session, 'precios_vigentes', actualizados, duracion)
    _log.info('sync_precios_vigentes: %d actualizados, %d sin_match local, %d ms',
              actualizados, sin_match, duracion)
    return {'upsert': actualizados, 'sin_match': sin_match,
            'duracion_ms': duracion}


def sync_condiciones_comerciales(session):
    """Sync de Gestion.CondicionesComerciales (descuentos / promos vigentes).

    Diego 2026-06-24: la vista Observer ya filtra las VIGENTES (149 filas
    típicas vs 7.646 del historial). Hacemos un reemplazo total — borramos
    todas las locales y volvemos a insertar — porque al ser tan pocas no
    vale la pena un upsert. Requiere SA (el schema Gestion no es accesible
    para usuarioDW).
    """
    # Skip limpio si no hay acceso al schema Gestion (otras farmacias).
    if not _test_acceso_gestion():
        return {'upsert': 0, 'skipped': True,
                'razon': 'sin acceso al schema Gestion (requiere usuario sa)'}
    from database import ObsCondicionComercial, now_ar
    t0 = time.time()
    conn = _connect(timeout=120)
    if conn is None:
        raise RuntimeError('ObServer no configurado')
    insertadas = 0
    try:
        ahora = now_ar()
        with conn.cursor(as_dict=True) as cur:
            try:
                # Filtro: dejar afuera las ya vencidas (VigenciaHasta < hoy).
                # Sí traemos las sin VigenciaHasta (NULL = sin límite) y las
                # futuras (vigencia_desde > hoy). El filtro de "vigente HOY"
                # lo aplica el caller en runtime. Diego 2026-06-24.
                cur.execute("""
                    SELECT IdCondicionComercial, HIS_IdCondicionComercial,
                           IdTipoCondicionComercial, TipoPromocion,
                           Descripcion, Orden, Porcentaje, AplicaSobrePVP,
                           AplicaEnOfertas, AplicaEnParticularObraSocial,
                           AplicaEnFraccionado, AplicaConCuotas,
                           MxN, CantidadPara2daUnidad,
                           VigenciaDesde, VigenciaHasta,
                           HoraDesde, HoraHasta,
                           Lunes, Martes, Miercoles, Jueves,
                           Viernes, Sabado, Domingo,
                           ConjuntoProductos, ConjuntoLaboratorios,
                           ConjuntoRubros, ConjuntoSubrubros,
                           ConjuntoFormasDePago, ConjuntoTarjetas, IdTipoTarjeta,
                           ConjuntoConvenios, ConjuntoPlanes,
                           ConjuntoTiposProducto, ConjuntoTiposVenta,
                           ConjuntoGrupos, ConjuntoClientes, ConjuntoFarmacias,
                           Formula
                      FROM Gestion.CondicionesComerciales
                     WHERE VigenciaHasta IS NULL
                        OR VigenciaHasta >= CAST(GETDATE() AS DATE)
                """)
            except Exception as e:  # noqa: BLE001
                _log.warning('sync_condiciones_comerciales: sin acceso a '
                             'Gestion.CondicionesComerciales (%s). Requiere '
                             'OBSERVER_USER con permiso al schema Gestion (ej. sa).', e)
                return {'upsert': 0,
                        'duracion_ms': int((time.time() - t0) * 1000),
                        'error': 'permiso denegado'}
            filas = cur.fetchall()

        # Reemplazo total: 149 filas no vale la pena el overhead de upsert.
        session.query(ObsCondicionComercial).delete()
        session.flush()

        def _str(v):
            return (v or '').strip() or None if isinstance(v, str) else None

        for r in filas:
            cc = ObsCondicionComercial(
                observer_id              = r['IdCondicionComercial'],
                his_id                   = r['HIS_IdCondicionComercial'],
                tipo                     = _str(r['IdTipoCondicionComercial']),
                tipo_promocion           = _str(r['TipoPromocion']),
                descripcion              = _str(r['Descripcion']),
                orden                    = r['Orden'],
                porcentaje               = r['Porcentaje'],
                aplica_sobre_pvp         = bool(r['AplicaSobrePVP']),
                aplica_en_ofertas        = _str(r['AplicaEnOfertas']),
                aplica_en_particular_os  = _str(r['AplicaEnParticularObraSocial']),
                aplica_en_fraccionado    = _str(r['AplicaEnFraccionado']),
                aplica_con_cuotas        = _str(r['AplicaConCuotas']),
                mxn                      = _str(r['MxN']),
                cantidad_para_2da        = r['CantidadPara2daUnidad'],
                vigencia_desde           = r['VigenciaDesde'],
                vigencia_hasta           = r['VigenciaHasta'],
                hora_desde               = _str(r['HoraDesde']),
                hora_hasta               = _str(r['HoraHasta']),
                lunes                    = bool(r['Lunes']),
                martes                   = bool(r['Martes']),
                miercoles                = bool(r['Miercoles']),
                jueves                   = bool(r['Jueves']),
                viernes                  = bool(r['Viernes']),
                sabado                   = bool(r['Sabado']),
                domingo                  = bool(r['Domingo']),
                conj_productos           = r['ConjuntoProductos'],
                conj_laboratorios        = r['ConjuntoLaboratorios'],
                conj_rubros              = r['ConjuntoRubros'],
                conj_subrubros           = r['ConjuntoSubrubros'],
                conj_formas_pago         = r['ConjuntoFormasDePago'],
                conj_tarjetas            = r['ConjuntoTarjetas'],
                id_tipo_tarjeta          = _str(r['IdTipoTarjeta']),
                conj_convenios           = r['ConjuntoConvenios'],
                conj_planes              = r['ConjuntoPlanes'],
                conj_tipos_producto      = r['ConjuntoTiposProducto'],
                conj_tipos_venta         = r['ConjuntoTiposVenta'],
                conj_grupos              = r['ConjuntoGrupos'],
                conj_clientes            = r['ConjuntoClientes'],
                conj_farmacias           = r['ConjuntoFarmacias'],
                formula                  = r['Formula'],
                sync_en                  = ahora,
            )
            session.add(cc)
            insertadas += 1
        session.flush()
    finally:
        conn.close()
    duracion = int((time.time() - t0) * 1000)
    _log_sync(session, 'condiciones_comerciales', insertadas, duracion)
    _log.info('sync_condiciones_comerciales: %d filas en %d ms', insertadas, duracion)
    return {'upsert': insertadas, 'duracion_ms': duracion}


def sync_fraccionado_master(session):
    """Deriva el flag fraccionado del master desde ObServer + el envase.

    ObServer MANDA en presentación (decisión 2026-05-27): para productos con
    observer_id, `Producto.fraccionado` refleja `obs_productos.es_fraccionable`,
    y `ProductoAtributo.cantidad_envase` se sincroniza desde obs **pisando incluso
    ediciones manuales** (lo cargado a mano es un stopgap; ObServer es la verdad).
    Los productos sin observer_id no se tocan (ahí el marcado manual sigue siendo
    la única fuente). PostgreSQL-only; correr después de sync_productos.

    Paso 0: materializa la fila de master para las fraccionables de ObServer que
    todavía no la tengan. La pantalla de presentación lee el master
    (`Producto.fraccionado`); en una instancia nueva el master está casi vacío
    aunque ObServer tenga las fraccionables, así que sin esto no habría filas
    sobre las cuales setear flag/envase.
    """
    from sqlalchemy import text

    from database import now_ar
    from helpers import materializar_producto
    t0 = time.time()
    # 0) Materializar el master de las fraccionables de ObServer sin fila local.
    faltantes = [r[0] for r in session.execute(text("""
        SELECT o.observer_id
        FROM obs_productos o
        LEFT JOIN productos p ON p.observer_id = o.observer_id
        WHERE o.es_fraccionable AND o.fecha_baja IS NULL AND p.id IS NULL
    """)).fetchall()]
    mat_nuevos = 0
    for oid in faltantes:
        prod, _err = materializar_producto(session, oid)
        if prod is not None:
            mat_nuevos += 1
    if mat_nuevos:
        session.flush()
    # 1) Espejo del flag fraccionado (solo donde difiere).
    #    Sin alias en la tabla destino: Postgres y SQLite (>=3.33) lo aceptan así.
    flag = session.execute(text("""
        UPDATE productos
        SET fraccionado = o.es_fraccionable
        FROM obs_productos o
        WHERE o.observer_id = productos.observer_id
          AND productos.fraccionado IS DISTINCT FROM o.es_fraccionable
    """)).rowcount or 0
    # 2a) Crear ProductoAtributo con el envase de obs para fraccionables sin atributo.
    env_nuevos = session.execute(text("""
        INSERT INTO producto_atributos (producto_id, cantidad_envase, fuente, confianza, extraido_en)
        SELECT p.id, o.cantidad_envase, 'observer', 'ALTA', :ts
        FROM productos p
        JOIN obs_productos o ON o.observer_id = p.observer_id
        LEFT JOIN producto_atributos a ON a.producto_id = p.id
        WHERE o.es_fraccionable AND o.cantidad_envase IS NOT NULL
          AND a.producto_id IS NULL
    """), {'ts': now_ar()}).rowcount or 0
    # 2b) Sincronizar envase desde obs en fraccionables, PISANDO el valor actual
    #     (incluido manual) cuando difiere. ObServer manda en presentación.
    env_completados = session.execute(text("""
        UPDATE producto_atributos
        SET cantidad_envase = o.cantidad_envase, fuente = 'observer'
        FROM productos p, obs_productos o
        WHERE producto_atributos.producto_id = p.id AND o.observer_id = p.observer_id
          AND o.es_fraccionable AND o.cantidad_envase IS NOT NULL
          AND producto_atributos.cantidad_envase IS DISTINCT FROM o.cantidad_envase
    """)).rowcount or 0
    duracion = int((time.time() - t0) * 1000)
    _log_sync(session, 'fraccionado_master', flag, duracion,
              info=(f'master: {mat_nuevos} materializados; '
                    f'envase: {env_nuevos} nuevos, {env_completados} completados'))
    return {'upsert': flag, 'duracion_ms': duracion, 'flag_updates': flag,
            'mat_nuevos': mat_nuevos,
            'env_nuevos': env_nuevos, 'env_completados': env_completados}


def sync_colegios_medicos(session):
    from database import ObsColegioMedico, now_ar
    t0 = time.time()
    conn = _connect()
    if conn is None:
        raise RuntimeError('ObServer no configurado')
    n = 0
    try:
        with conn.cursor(as_dict=True) as cur:
            cur.execute("SELECT IdColegioMedico, Descripcion, IdProvincia, IdTipoColegio, FechaBaja FROM DW.ColegiosMedicos")
            for r in cur.fetchall():
                _upsert_obs(
                    session, ObsColegioMedico, 'observer_id',
                    int(r['IdColegioMedico']),
                    descripcion=(r['Descripcion'] or '').strip() or None,
                    id_provincia=(r['IdProvincia'] or '').strip() or None,
                    id_tipo_colegio=(r['IdTipoColegio'] or '').strip() or None,
                    fecha_baja=r['FechaBaja'],
                    sync_en=now_ar(),
                )
                n += 1
    finally:
        conn.close()
    duracion = int((time.time() - t0) * 1000)
    _log_sync(session, 'colegios_medicos', n, duracion)
    return {'upsert': n, 'duracion_ms': duracion}


def sync_medicos(session):
    from database import ObsMedico, now_ar
    t0 = time.time()
    conn = _connect(timeout=120)
    if conn is None:
        raise RuntimeError('ObServer no configurado')
    n = 0
    try:
        with conn.cursor(as_dict=True) as cur:
            cur.execute("SELECT IdMedico, Medico, CUIT, Habilitado, FechaBaja FROM DW.Medicos")
            for r in cur.fetchall():
                _upsert_obs(
                    session, ObsMedico, 'observer_id',
                    int(r['IdMedico']),
                    nombre=(r['Medico'] or '').strip() or '(sin nombre)',
                    cuit=(r['CUIT'] or '').strip() or None,
                    habilitado=bool(r['Habilitado']) if r['Habilitado'] is not None else None,
                    fecha_baja=r['FechaBaja'],
                    sync_en=now_ar(),
                )
                n += 1
                if n % 5000 == 0:
                    session.flush()
    finally:
        conn.close()
    duracion = int((time.time() - t0) * 1000)
    _log_sync(session, 'medicos', n, duracion)
    return {'upsert': n, 'duracion_ms': duracion}


def sync_medicos_matriculas(session):
    from database import ObsColegioMedico, ObsMedico, ObsMedicoMatricula, now_ar
    t0 = time.time()
    conn = _connect(timeout=120)
    if conn is None:
        raise RuntimeError('ObServer no configurado')
    medicos_validos = {i for (i,) in session.query(ObsMedico.observer_id).all()}
    colegios_validos = {i for (i,) in session.query(ObsColegioMedico.observer_id).all()}
    n = skipped = 0
    try:
        with conn.cursor(as_dict=True) as cur:
            cur.execute("SELECT IdMedicoMatricula, IdMedico, Matricula, IdColegioMedico, FechaBaja FROM DW.MedicosMatriculas")
            for r in cur.fetchall():
                med = int(r['IdMedico']) if r['IdMedico'] is not None else None
                if med is None or med not in medicos_validos:
                    skipped += 1
                    continue
                col = int(r['IdColegioMedico']) if r['IdColegioMedico'] is not None else None
                if col is not None and col not in colegios_validos:
                    col = None
                _upsert_obs(
                    session, ObsMedicoMatricula, 'observer_id',
                    int(r['IdMedicoMatricula']),
                    medico_observer=med,
                    matricula=(r['Matricula'] or '').strip() or None,
                    colegio_observer=col,
                    fecha_baja=r['FechaBaja'],
                    sync_en=now_ar(),
                )
                n += 1
                if n % 5000 == 0:
                    session.flush()
    finally:
        conn.close()
    duracion = int((time.time() - t0) * 1000)
    _log_sync(session, 'medicos_matriculas', n, duracion)
    return {'upsert': n, 'duracion_ms': duracion, 'skipped_fk': skipped}


def sync_ventas_detalle(session, desde_fecha=None, meses_default=24, id_farmacia=None):
    """Sync incremental de DW.ProductosVendidos (detalle por venta).

    Args:
        desde_fecha: si está, trae ventas con FechaEstadistica >= desde_fecha.
                     Si None: usa MAX(fecha_estadistica) local + 1 día. Si no
                     hay datos locales, arranca desde hoy - meses_default.
        meses_default: cuántos meses traer en el primer sync (default 24).
        id_farmacia: filtrar por farmacia (default OBSERVER_ID_FARMACIA).

    Devuelve: {'upsert': n, 'duracion_ms': X, 'desde': fecha_iso, 'skipped_fk': N}.
    """
    from datetime import date, datetime, timedelta

    from database import ObsCliente, ObsObraSocial, ObsPlan, ObsProducto, ObsVentaDetalle, now_ar

    t0 = time.time()
    cfg = _config()
    if not cfg:
        raise RuntimeError('ObServer no configurado')
    if id_farmacia is None:
        id_farmacia = cfg['id_farmacia']

    # Resolver desde_fecha si no viene
    if desde_fecha is None:
        last = session.query(ObsVentaDetalle.fecha_estadistica)\
                      .filter(ObsVentaDetalle.id_farmacia == id_farmacia)\
                      .order_by(ObsVentaDetalle.fecha_estadistica.desc()).limit(1).first()
        if last and last[0]:
            desde_fecha = last[0] + timedelta(days=1)
        else:
            desde_fecha = (date.today().replace(day=1)
                           - timedelta(days=meses_default * 31))

    if isinstance(desde_fecha, datetime):
        desde_fecha = desde_fecha.date()

    # Sets de FKs válidas para skipear
    productos_validos    = {i for (i,) in session.query(ObsProducto.observer_id).all()}
    clientes_validos     = {i for (i,) in session.query(ObsCliente.observer_id).all()}
    obras_validas        = {i for (i,) in session.query(ObsObraSocial.observer_id).all()}
    planes_validos       = {i for (i,) in session.query(ObsPlan.observer_id).all()}

    conn = _connect(timeout=600)  # 10 min — es el sync más pesado

    if conn is None:
        raise RuntimeError('ObServer no configurado')

    # Bulk upsert por lote (PostgreSQL ON CONFLICT). Antes se hacía un
    # _upsert_obs (SELECT+INSERT) por fila + cur.fetchall() en RAM → en el
    # primer sync de 24 meses eran millones de roundtrips + ~2 GiB de buffer, y
    # el worker de gunicorn moría por timeout (900s) sin commitear → 0 filas.
    # Ahora: cursor en streaming (fetchmany) + INSERT ... ON CONFLICT por lote
    # + commit por lote, así el progreso parcial sobrevive y el sync incremental
    # retoma desde el MAX(fecha) local.
    from sqlalchemy.dialects.postgresql import insert as _pg_insert
    es_pg = session.get_bind().dialect.name == 'postgresql'
    _tabla = ObsVentaDetalle.__table__
    _cols_update = [c.name for c in _tabla.columns if c.name != 'id_producto_vendido']

    def _flush(mappings):
        if not mappings:
            return
        if es_pg:
            # Dedup por PK dentro del lote: ON CONFLICT no puede tocar la misma fila 2x.
            dedup = {m['id_producto_vendido']: m for m in mappings}
            stmt = _pg_insert(_tabla).values(list(dedup.values()))
            stmt = stmt.on_conflict_do_update(
                index_elements=['id_producto_vendido'],
                set_={c: stmt.excluded[c] for c in _cols_update},
            )
            session.execute(stmt)
        else:
            # Fallback no-PG (ej. SQLite en dev): upsert fila por fila.
            for m in mappings:
                pk = m.pop('id_producto_vendido')
                _upsert_obs(session, ObsVentaDetalle, 'id_producto_vendido', pk, **m)
        session.commit()

    BATCH = 5000
    n = skipped = 0
    ts = now_ar()
    try:
        with conn.cursor(as_dict=True) as cur:
            cur.execute("""
                SELECT IdProductoVendido, IdOperacion, NumeroRenglon,
                       IdProducto, IdCliente, IdMedico, IdMedicoMatricula,
                       EsVentaParticular, IdObraSocialPrincipal, IdPlanPrincipal,
                       IdPlanComplemento1, IdPlanComplemento2, IdPlanComplemento3,
                       Cantidad, CantidadReconocidaPlanPrincipal,
                       Importe, ImporteACargoOS, ACargoPlanPrincipal,
                       ImporteEfectivo, ImporteTarjeta, ImporteCheque, ImporteCuentaCorriente,
                       FechaDeOperacion, FechaEstadistica, [Año] AS Anio, Mes, Dia,
                       IdFarmacia, IdCanalDeVenta, IdTipoOperacion, IdOperador
                FROM DW.ProductosVendidos
                WHERE FechaEstadistica >= %s AND IdFarmacia = %s
                ORDER BY FechaEstadistica
            """, (desde_fecha, id_farmacia))

            buffer = []
            while True:
                filas = cur.fetchmany(BATCH)
                if not filas:
                    break
                for r in filas:
                    # FKs: skipear si producto no existe local (debería estar)
                    pid = int(r['IdProducto']) if r['IdProducto'] is not None else None
                    if pid is None or pid not in productos_validos:
                        skipped += 1
                        continue
                    cli = int(r['IdCliente']) if r['IdCliente'] is not None else None
                    if cli is not None and cli not in clientes_validos:
                        cli = None
                    os_id = int(r['IdObraSocialPrincipal']) if r['IdObraSocialPrincipal'] is not None else None
                    if os_id is not None and os_id not in obras_validas:
                        os_id = None
                    plan = int(r['IdPlanPrincipal']) if r['IdPlanPrincipal'] is not None else None
                    if plan is not None and plan not in planes_validos:
                        plan = None

                    buffer.append({
                        'id_producto_vendido': int(r['IdProductoVendido']),
                        'id_operacion': int(r['IdOperacion']) if r['IdOperacion'] is not None else None,
                        'numero_renglon': int(r['NumeroRenglon']) if r['NumeroRenglon'] is not None else None,
                        'producto_observer': pid,
                        'cliente_observer': cli,
                        'medico_observer': int(r['IdMedico']) if r['IdMedico'] is not None else None,
                        'medico_matricula_observer': int(r['IdMedicoMatricula']) if r['IdMedicoMatricula'] is not None else None,
                        'es_venta_particular': bool(r['EsVentaParticular']) if r['EsVentaParticular'] is not None else None,
                        'obra_social_observer': os_id,
                        'plan_principal_observer': plan,
                        'plan_complemento1_observer': int(r['IdPlanComplemento1']) if r['IdPlanComplemento1'] is not None else None,
                        'plan_complemento2_observer': int(r['IdPlanComplemento2']) if r['IdPlanComplemento2'] is not None else None,
                        'plan_complemento3_observer': int(r['IdPlanComplemento3']) if r['IdPlanComplemento3'] is not None else None,
                        'cantidad': r['Cantidad'],
                        'cantidad_reconocida_principal': r['CantidadReconocidaPlanPrincipal'],
                        'importe': r['Importe'],
                        'importe_a_cargo_os': r['ImporteACargoOS'],
                        'a_cargo_plan_principal': r['ACargoPlanPrincipal'],
                        'importe_efectivo': r['ImporteEfectivo'],
                        'importe_tarjeta': r['ImporteTarjeta'],
                        'importe_cheque': r['ImporteCheque'],
                        'importe_cuenta_corriente': r['ImporteCuentaCorriente'],
                        'fecha_operacion': r['FechaDeOperacion'],
                        'fecha_estadistica': r['FechaEstadistica'],
                        'anio': int(r['Anio']) if r['Anio'] is not None else None,
                        'mes': int(r['Mes']) if r['Mes'] is not None else None,
                        'dia': int(r['Dia']) if r['Dia'] is not None else None,
                        'id_farmacia': int(r['IdFarmacia']),
                        'canal_venta_observer': int(r['IdCanalDeVenta']) if r['IdCanalDeVenta'] is not None else None,
                        'tipo_operacion': r.get('IdTipoOperacion'),
                        'operador_observer': str(r['IdOperador']) if r.get('IdOperador') is not None else None,
                        'sync_en': ts,
                    })
                    n += 1
                    if len(buffer) >= BATCH:
                        _flush(buffer)
                        buffer = []
            _flush(buffer)
    finally:
        conn.close()
    duracion = int((time.time() - t0) * 1000)
    _log_sync(session, 'ventas_detalle', n, duracion)
    return {
        'upsert': n,
        'duracion_ms': duracion,
        'desde': desde_fecha.isoformat(),
        'skipped_fk': skipped,
    }


def sync_operadores(session):
    """Espejo de DW.OperadoresVenta (vendedores/operadores del POS).

    `observer_id` = IdUsuario (UUID) = el `IdOperador` de DW.ProductosVendidos.
    Habilita las estadísticas de ventas por vendedor (obs_ventas_detalle.operador_observer)."""
    from database import ObsOperador, now_ar
    t0 = time.time()
    conn = _connect()
    if conn is None:
        raise RuntimeError('ObServer no configurado')
    n = 0
    try:
        with conn.cursor(as_dict=True) as cur:
            cur.execute("SELECT IdUsuario, Vendedor FROM DW.OperadoresVenta")
            for r in cur.fetchall():
                uid = r['IdUsuario']
                if uid is None:
                    continue
                _upsert_obs(
                    session, ObsOperador, 'observer_id', str(uid),
                    nombre=(r['Vendedor'] or '').strip() or '(sin nombre)',
                    sync_en=now_ar(),
                )
                n += 1
    finally:
        conn.close()
    duracion = int((time.time() - t0) * 1000)
    _log_sync(session, 'operadores', n, duracion)
    return {'upsert': n, 'duracion_ms': duracion}


def sync_grupos_clientes(session):
    from database import ObsGrupoCliente, now_ar
    t0 = time.time()
    conn = _connect()
    if conn is None:
        raise RuntimeError('ObServer no configurado')
    n = 0
    try:
        with conn.cursor(as_dict=True) as cur:
            cur.execute("SELECT IdGrupoCliente, Descripcion, FechaBaja FROM DW.GruposClientes")
            for r in cur.fetchall():
                _upsert_obs(
                    session, ObsGrupoCliente, 'observer_id',
                    int(r['IdGrupoCliente']),
                    descripcion=(r['Descripcion'] or '').strip() or '(sin descripcion)',
                    fecha_baja=r['FechaBaja'],
                    sync_en=now_ar(),
                )
                n += 1
    finally:
        conn.close()
    duracion = int((time.time() - t0) * 1000)
    _log_sync(session, 'grupos_clientes', n, duracion)
    return {'upsert': n, 'duracion_ms': duracion}


def sync_categorias_clientes(session):
    from database import ObsCategoriaCliente, now_ar
    t0 = time.time()
    conn = _connect()
    if conn is None:
        raise RuntimeError('ObServer no configurado')
    n = 0
    try:
        with conn.cursor(as_dict=True) as cur:
            cur.execute("SELECT IdCategoriaCliente, Descripcion, FechaBaja FROM DW.CategoriasClientes")
            for r in cur.fetchall():
                _upsert_obs(
                    session, ObsCategoriaCliente, 'observer_id',
                    int(r['IdCategoriaCliente']),
                    descripcion=(r['Descripcion'] or '').strip() or '(sin descripcion)',
                    fecha_baja=r['FechaBaja'],
                    sync_en=now_ar(),
                )
                n += 1
    finally:
        conn.close()
    duracion = int((time.time() - t0) * 1000)
    _log_sync(session, 'categorias_clientes', n, duracion)
    return {'upsert': n, 'duracion_ms': duracion}


def sync_obras_sociales(session):
    from database import ObsObraSocial, now_ar
    t0 = time.time()
    conn = _connect()
    if conn is None:
        raise RuntimeError('ObServer no configurado')
    n = 0
    try:
        with conn.cursor(as_dict=True) as cur:
            cur.execute("SELECT IdObraSocial, Descripcion, FechaBaja FROM DW.ObrasSociales")
            for r in cur.fetchall():
                _upsert_obs(
                    session, ObsObraSocial, 'observer_id',
                    int(r['IdObraSocial']),
                    descripcion=(r['Descripcion'] or '').strip() or '(sin descripcion)',
                    fecha_baja=r['FechaBaja'],
                    sync_en=now_ar(),
                )
                n += 1
    finally:
        conn.close()
    duracion = int((time.time() - t0) * 1000)
    _log_sync(session, 'obras_sociales', n, duracion)
    return {'upsert': n, 'duracion_ms': duracion}


def sync_convenios(session):
    from database import ObsConvenio, ObsObraSocial, now_ar
    t0 = time.time()
    conn = _connect()
    if conn is None:
        raise RuntimeError('ObServer no configurado')
    os_validas = {i for (i,) in session.query(ObsObraSocial.observer_id).all()}
    n = skipped = 0
    try:
        with conn.cursor(as_dict=True) as cur:
            cur.execute("SELECT IdConvenio, Descripcion, IdObraSocial, FechaBaja FROM DW.Convenios")
            for r in cur.fetchall():
                os_id = int(r['IdObraSocial']) if r['IdObraSocial'] is not None else None
                if os_id is not None and os_id not in os_validas:
                    os_id = None
                    skipped += 1
                _upsert_obs(
                    session, ObsConvenio, 'observer_id',
                    int(r['IdConvenio']),
                    descripcion=(r['Descripcion'] or '').strip() or None,
                    obra_social_observer=os_id,
                    fecha_baja=r['FechaBaja'],
                    sync_en=now_ar(),
                )
                n += 1
    finally:
        conn.close()
    duracion = int((time.time() - t0) * 1000)
    _log_sync(session, 'convenios', n, duracion)
    return {'upsert': n, 'duracion_ms': duracion, 'skipped_fk': skipped}


def sync_planes(session):
    from database import ObsConvenio, ObsPlan, now_ar
    t0 = time.time()
    conn = _connect(timeout=60)
    if conn is None:
        raise RuntimeError('ObServer no configurado')
    conv_validos = {i for (i,) in session.query(ObsConvenio.observer_id).all()}
    n = skipped = 0
    try:
        with conn.cursor(as_dict=True) as cur:
            cur.execute("SELECT IdPlan, Descripcion, IdConvenio, Habilitado, FechaBaja FROM DW.Planes")
            for r in cur.fetchall():
                conv_id = int(r['IdConvenio']) if r['IdConvenio'] is not None else None
                if conv_id is not None and conv_id not in conv_validos:
                    conv_id = None
                    skipped += 1
                _upsert_obs(
                    session, ObsPlan, 'observer_id',
                    int(r['IdPlan']),
                    descripcion=(r['Descripcion'] or '').strip() or '(sin descripcion)',
                    convenio_observer=conv_id,
                    habilitado=bool(r['Habilitado']),
                    fecha_baja=r['FechaBaja'],
                    sync_en=now_ar(),
                )
                n += 1
    finally:
        conn.close()
    duracion = int((time.time() - t0) * 1000)
    _log_sync(session, 'planes', n, duracion)
    return {'upsert': n, 'duracion_ms': duracion, 'skipped_fk': skipped}


def sync_clientes(session, id_farmacia=None):
    """Sync DW.Clientes. Requiere obs_grupos_clientes + obs_categorias_clientes sincronizados.
    84k filas: commit parcial cada 5000."""
    from database import ObsCategoriaCliente, ObsCliente, ObsGrupoCliente, now_ar
    t0 = time.time()
    cfg = _config()
    if not cfg:
        raise RuntimeError('ObServer no configurado')
    if id_farmacia is None:
        id_farmacia = cfg['id_farmacia']

    grupos_validos = {i for (i,) in session.query(ObsGrupoCliente.observer_id).all()}
    cats_validas = {i for (i,) in session.query(ObsCategoriaCliente.observer_id).all()}

    conn = _connect(timeout=120)
    if conn is None:
        raise RuntimeError('ObServer no configurado')
    n = 0
    try:
        with conn.cursor(as_dict=True) as cur:
            cur.execute("""
                SELECT IdCliente, ApellidoNombre, Documento_Tipo, Documento_Numero,
                       Domicilio_CodigoPostal, Domicilio_Direccion, Localidad,
                       IdProvincia, IdGrupoCliente, IdCategoriaCliente,
                       IdFarmacia, Telefono
                FROM DW.Clientes
            """)
            for r in cur.fetchall():
                gid = int(r['IdGrupoCliente']) if r['IdGrupoCliente'] is not None else None
                if gid is not None and gid not in grupos_validos:
                    gid = None
                cid = int(r['IdCategoriaCliente']) if r['IdCategoriaCliente'] is not None else None
                if cid is not None and cid not in cats_validas:
                    cid = None
                _upsert_obs(
                    session, ObsCliente, 'observer_id',
                    int(r['IdCliente']),
                    apellido_nombre=(r['ApellidoNombre'] or '').strip() or '(sin nombre)',
                    documento_tipo=(r['Documento_Tipo'] or '').strip() or None,
                    documento_numero=int(r['Documento_Numero']) if r['Documento_Numero'] is not None else None,
                    domicilio_cp=(r['Domicilio_CodigoPostal'] or '').strip() or None,
                    domicilio_direccion=(r['Domicilio_Direccion'] or '').strip() or None,
                    localidad=(r['Localidad'] or '').strip() or None,
                    provincia=(r['IdProvincia'] or '').strip() or None,
                    grupo_observer=gid,
                    categoria_observer=cid,
                    id_farmacia=int(r['IdFarmacia']),
                    telefono=(r['Telefono'] or '').strip() or None,
                    sync_en=now_ar(),
                )
                n += 1
                if n % 5000 == 0:
                    session.flush()
    finally:
        conn.close()
    duracion = int((time.time() - t0) * 1000)
    _log_sync(session, 'clientes', n, duracion)
    return {'upsert': n, 'duracion_ms': duracion}


def sync_stock(session, id_farmacia=None):
    """Sync DW.StockFarmaciasProductos. Requiere obs_productos poblado primero.
    Si id_farmacia=None usa OBSERVER_ID_FARMACIA del env."""
    from database import ObsProducto, ObsStock, now_ar
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
                SELECT IdProducto, StockActual, Maximo, Minimo, Fraccionado
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
                obj.fraccionado = bool(r['Fraccionado']) if r['Fraccionado'] is not None else False
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


def sync_ventas_mensuales(session, meses=None, id_farmacia=None):
    """Agrega DW.ProductosVendidos por (IdProducto, Año, Mes) y upsertea en obs_ventas_mensuales.

    Args:
        session: SQLAlchemy session.
        meses: cantidad de meses hacia atrás a sincronizar desde el mes actual inclusive.
               Si None, lee OBSERVER_VENTAS_MESES del env (default 16).
        id_farmacia: si None, usa OBSERVER_ID_FARMACIA del env.

    Estrategia:
    - Query con GROUP BY del lado SQL Server (mucho menos data que traer filas).
    - Filtramos IdTipoOperacion='V' (solo ventas, no devoluciones/otros).
    - Upsert por (id_farmacia, producto_observer, anio, mes).
    - Skip filas cuyo IdProducto no esté en obs_productos local (FK).
    """
    from datetime import datetime

    from database import ObsProducto, ObsVentaMensual, now_ar
    t0 = time.time()
    cfg = _config()
    if not cfg:
        raise RuntimeError('ObServer no configurado')
    if id_farmacia is None:
        id_farmacia = cfg['id_farmacia']
    if meses is None:
        # Prioridad: Config.observer_ventas_meses → env → 16
        from database import Config as _Cfg
        row = session.query(_Cfg).first()
        if row and row.observer_ventas_meses:
            meses = int(row.observer_ventas_meses)
        else:
            try:
                meses = int(os.environ.get('OBSERVER_VENTAS_MESES', '16'))
            except ValueError:
                meses = 16
    meses = max(1, min(120, meses))

    # Calcular (anio, mes) desde y hasta
    ahora = datetime.now()
    hasta_anio, hasta_mes = ahora.year, ahora.month
    # Retroceder `meses - 1` para incluir el mes actual
    m = hasta_mes - (meses - 1)
    y = hasta_anio
    while m <= 0:
        m += 12
        y -= 1
    desde_anio, desde_mes = y, m
    desde_key = desde_anio * 100 + desde_mes
    hasta_key = hasta_anio * 100 + hasta_mes

    from sqlalchemy import text as _sqltext
    ids_validos = {pid for (pid,) in session.query(ObsProducto.observer_id).all()}

    # Estrategia delete+insert: borramos el rango que vamos a re-traer y
    # bulk-inserteamos. Idempotente, rápido y sin duplicados.
    session.execute(_sqltext("""
        DELETE FROM obs_ventas_mensuales
        WHERE id_farmacia = :fid
          AND (anio * 100 + mes) BETWEEN :d AND :h
    """), {'fid': int(id_farmacia), 'd': int(desde_key), 'h': int(hasta_key)})
    session.flush()

    conn = _connect(timeout=180)
    if conn is None:
        raise RuntimeError('ObServer no configurado')
    n = skipped = 0
    ts = now_ar()
    try:
        with conn.cursor(as_dict=True) as cur:
            # Ventas NETAS: incluimos 'V' (venta) y 'D' (devolución / nota de
            # crédito). Las 'D' vienen con Cantidad e ImporteNeto NEGATIVOS, así
            # que el SUM resta automáticamente lo devuelto. Antes solo contaba
            # 'V' → las ventas quedaban infladas por las devoluciones no restadas.
            # Trx cuenta solo ventas reales (no las devoluciones).
            cur.execute("""
                SELECT IdProducto,
                       Anio = [Año],
                       Mes,
                       Unidades = SUM(Cantidad),
                       Monto    = SUM(ImporteNeto),
                       Trx      = SUM(CASE WHEN IdTipoOperacion = 'V' THEN 1 ELSE 0 END)
                FROM DW.ProductosVendidos
                WHERE IdFarmacia = %d
                  AND IdTipoOperacion IN ('V', 'D')
                  AND ([Año] * 100 + Mes) BETWEEN %d AND %d
                GROUP BY IdProducto, [Año], Mes
            """, (int(id_farmacia), int(desde_key), int(hasta_key)))
            buffer = []
            for r in cur.fetchall():
                pid = int(r['IdProducto'])
                if pid not in ids_validos:
                    skipped += 1
                    continue
                buffer.append({
                    'id_farmacia': int(id_farmacia),
                    'producto_observer': pid,
                    'anio': int(r['Anio']),
                    'mes': int(r['Mes']),
                    'unidades': r['Unidades'] or 0,
                    'monto': r['Monto'] or 0,
                    'transacciones': int(r['Trx'] or 0),
                    'sync_en': ts,
                })
                n += 1
                if len(buffer) >= 2000:
                    session.execute(ObsVentaMensual.__table__.insert(), buffer)
                    buffer.clear()
            if buffer:
                session.execute(ObsVentaMensual.__table__.insert(), buffer)
    finally:
        conn.close()
    duracion = int((time.time() - t0) * 1000)
    extra = f'{meses} meses · {desde_key}-{hasta_key}'
    if skipped:
        extra += f' · {skipped} huerfanos'
    _log_sync(session, 'ventas_mensuales', n, duracion, info=extra)
    return {'upsert': n, 'duracion_ms': duracion, 'meses': meses, 'skipped': skipped}


def estado_syncs(session):
    """Devuelve estado de frescura de cada entidad sincronizable.

    Para cada entidad: { 'estado', 'horas', 'ultimo_sync', 'filas', 'mensaje' }
    Estado: 'ok' | 'warning' | 'error' | 'nunca' | 'externo'

    Thresholds (horas):
        - ventas_mensuales:  warn 24h,  err 72h   (crítico para análisis)
        - stock:             warn 24h,  err 72h
        - productos:         warn 168h, err 720h  (1 sem / 1 mes)
        - laboratorios:      warn 720h, err 2160h (1 mes / 3 meses)
        - clientes:          warn 168h, err 720h
    """
    from database import ObsCliente, ObsLaboratorio, ObsProducto, ObsStock, ObsSyncLog, ObsVentaMensual, now_ar

    config = [
        ('ventas_mensuales', ObsVentaMensual, 24,   72,   'Ventas mensuales'),
        ('stock',            ObsStock,        24,   72,   'Stock'),
        ('productos',        ObsProducto,     168,  720,  'Productos'),
        ('laboratorios',     ObsLaboratorio,  720,  2160, 'Laboratorios'),
        ('clientes',         ObsCliente,      168,  720,  'Clientes'),
    ]

    ahora = now_ar()
    out = {}
    for entidad, Modelo, warn_h, err_h, label in config:
        filas = session.query(Modelo).count()
        ultimo = (session.query(ObsSyncLog)
                  .filter(ObsSyncLog.entidad == entidad)
                  .order_by(ObsSyncLog.ejecutado_en.desc()).first())

        if filas == 0:
            out[entidad] = {'label': label, 'estado': 'nunca', 'horas': None,
                            'ultimo_sync': None, 'filas': 0,
                            'mensaje': f'{label}: nunca se sincronizó.'}
            continue
        if not ultimo:
            # Hay datos pero no hay log (ej. pull_from_render)
            out[entidad] = {'label': label, 'estado': 'externo', 'horas': None,
                            'ultimo_sync': None, 'filas': filas,
                            'mensaje': f'{label}: {filas:,} filas (origen externo, sin log local).'}
            continue
        delta_h = (ahora - ultimo.ejecutado_en).total_seconds() / 3600
        if delta_h >= err_h:
            estado = 'error'
        elif delta_h >= warn_h:
            estado = 'warning'
        else:
            estado = 'ok'
        out[entidad] = {
            'label':       label,
            'estado':      estado,
            'horas':       round(delta_h, 1),
            'ultimo_sync': ultimo.ejecutado_en.isoformat(),
            'filas':       filas,
            'mensaje':     f'{label}: última sync hace {_fmt_delta(delta_h)} ({filas:,} filas).',
        }
    return out


def _fmt_delta(horas):
    if horas < 1:
        return f'{int(horas * 60)} min'
    if horas < 24:
        return f'{int(horas)}h'
    dias = int(horas / 24)
    return f'{dias} día{"s" if dias != 1 else ""}'


def estado_ventas_mensuales(session, dias_fresco=7):
    """Estado de frescura de los datos de ObServer (ventas + stock).

    El template muestra dos líneas (stock + ventas) para que se vea cuál de
    los dos está desfasado. El campo `estado` global toma el peor — sirve
    para colorear el banner (ok/warn). Se mantiene el nombre por compat con
    los call-sites (procesos.py / consulta_stock).

    {
      'estado': 'fresco' | 'viejo' | 'nunca',   # global = peor de los dos
      'ultimo_sync': datetime o None,            # del peor
      'dias': int,                               # del peor
      'filas': int,                              # filas de ventas_mensuales
      'mensaje': str,                            # legacy: una línea
      'stock':  {'estado', 'ultimo_sync', 'dias'},
      'ventas': {'estado', 'ultimo_sync', 'dias'},
    }
    """
    from database import ObsSyncLog, ObsVentaMensual, now_ar
    filas = session.query(ObsVentaMensual).count()
    if filas == 0:
        return {'estado': 'nunca', 'ultimo_sync': None, 'dias': None, 'filas': 0,
                'mensaje': 'Todavía no se importaron ventas desde ObServer.',
                'stock':  {'estado': 'nunca', 'ultimo_sync': None, 'dias': None},
                'ventas': {'estado': 'nunca', 'ultimo_sync': None, 'dias': None}}

    def _sub(entidad):
        u = (session.query(ObsSyncLog)
             .filter(ObsSyncLog.entidad == entidad)
             .order_by(ObsSyncLog.ejecutado_en.desc()).first())
        if not u:
            return {'estado': 'nunca', 'ultimo_sync': None, 'dias': None}
        d = (now_ar() - u.ejecutado_en).days
        return {'estado': 'fresco' if d <= dias_fresco else 'viejo',
                'ultimo_sync': u.ejecutado_en, 'dias': d}

    sub_v = _sub('ventas_mensuales')
    sub_s = _sub('stock')

    # Si ninguno tiene log (datos importados por pull/push desde otra máquina —
    # típico en Render donde push_obs_to_render copia stock+ventas pero no
    # obs_sync_log): consideramos frescos pero sin medir días. Los sub-estados
    # también van a 'fresco' — si no, el template muestra "⛔ Stock: sin sync"
    # aunque el estado global sea 'fresco'.
    if sub_v['estado'] == 'nunca' and sub_s['estado'] == 'nunca':
        externo = {'estado': 'fresco', 'ultimo_sync': None, 'dias': None}
        return {'estado': 'fresco', 'ultimo_sync': None, 'dias': 0, 'filas': filas,
                'mensaje': f'{filas} filas de ventas disponibles (origen externo).',
                'stock': externo, 'ventas': externo}

    # Global = peor de los dos (ignorando 'nunca' si el otro tiene dato).
    ranking = {'fresco': 0, 'viejo': 1, 'nunca': 2}
    peor = max([sub_v, sub_s], key=lambda x: (ranking[x['estado']], x['dias'] or 0))
    cual = 'stock' if peor is sub_s else 'ventas'
    delta = peor['dias'] or 0
    if peor['estado'] == 'fresco':
        msg = f'Estadísticas al día — {cual} hace {delta} día(s).'
    else:
        msg = f'Estadísticas desactualizadas — {cual} hace {delta} día(s).'
    return {'estado': peor['estado'], 'ultimo_sync': peor['ultimo_sync'],
            'dias': delta, 'filas': filas, 'mensaje': msg,
            'stock': sub_s, 'ventas': sub_v}


def listar_obras_sociales_con_ventas(id_farmacia=None, meses_atras=12):
    """Devuelve OS que tuvieron al menos 1 venta a cargo OS en los últimos N meses.

    Returns: [{'id_obra_social': int, 'nombre': str, 'n_recetas': int}]
    """
    from datetime import date, timedelta
    cfg = _config()
    if not cfg:
        raise RuntimeError('ObServer no configurado')
    if id_farmacia is None:
        id_farmacia = cfg['id_farmacia']
    desde = date.today() - timedelta(days=meses_atras * 31)

    conn = _connect(timeout=60)
    if conn is None:
        raise RuntimeError('ObServer no disponible')
    out = []
    try:
        with conn.cursor(as_dict=True) as cur:
            cur.execute("""
                SELECT
                    os.IdObraSocial AS Id,
                    os.Descripcion  AS Nombre,
                    COUNT(DISTINCT pv.IdOperacion) AS NRecetas
                FROM DW.ObrasSociales os
                JOIN DW.ProductosVendidos pv
                  ON pv.IdObraSocialPrincipal = os.IdObraSocial
                WHERE pv.IdFarmacia = %d
                  AND pv.FechaDeOperacion >= %s
                  AND pv.IdTipoOperacion = 'V'
                  AND pv.ImporteACargoOS > 0
                  AND os.FechaBaja IS NULL
                GROUP BY os.IdObraSocial, os.Descripcion
                HAVING COUNT(DISTINCT pv.IdOperacion) > 0
                ORDER BY os.Descripcion
            """, (int(id_farmacia), desde))
            for r in cur.fetchall():
                out.append({
                    'id_obra_social': int(r['Id']),
                    'nombre': (r['Nombre'] or '').strip(),
                    'n_recetas': int(r['NRecetas']),
                })
    finally:
        conn.close()
    return out


def buscar_recetas_os(obra_social_id, desde, hasta, id_farmacia=None,
                      vendedor_uuid=None):
    """Busca recetas a una OS específica en un rango (rendición).

    Args:
        obra_social_id: int — IdObraSocial.
        desde, hasta: date.
        id_farmacia: int (default OBSERVER_ID_FARMACIA).
        vendedor_uuid: opcional, si se quiere filtrar también por operador.

    Returns: igual formato que buscar_recetas_vendedor.
    """
    from datetime import datetime, time, timedelta
    cfg = _config()
    if not cfg:
        raise RuntimeError('ObServer no configurado')
    if id_farmacia is None:
        id_farmacia = cfg['id_farmacia']

    if hasattr(hasta, 'date'):
        hasta_dt = hasta
    else:
        hasta_dt = datetime.combine(hasta, time(0, 0)) + timedelta(days=1)
    if hasattr(desde, 'date'):
        desde_dt = desde
    else:
        desde_dt = datetime.combine(desde, time(0, 0))

    conn = _connect(timeout=60)
    if conn is None:
        raise RuntimeError('ObServer no disponible')
    try:
        with conn.cursor(as_dict=True) as cur:
            extra_filter = ""
            params = [int(obra_social_id), int(id_farmacia), desde_dt, hasta_dt]
            if vendedor_uuid:
                extra_filter = " AND pv.IdOperador = %s"
                params.append(vendedor_uuid)
            cur.execute(f"""
                SELECT
                    pv.IdOperacion,
                    pv.IdProductoVendido,
                    pv.NumeroRenglon,
                    pv.FechaDeOperacion,
                    pv.Cantidad,
                    pv.Importe,
                    pv.ImporteACargoOS,
                    pv.Comprobante_IdFormularioAFIP AS TipoComp,
                    pv.Comprobante_PuntoDeVenta     AS PV,
                    pv.Comprobante_Numero           AS NroComp,
                    pv.IdOperador                   AS IdOperador,
                    ov.Vendedor                     AS OperadorNombre,
                    pr.Producto                     AS Producto,
                    os.Descripcion                  AS ObraSocial
                FROM DW.ProductosVendidos pv
                LEFT JOIN DW.Productos       pr ON pr.IdProducto = pv.IdProducto
                LEFT JOIN DW.ObrasSociales   os ON os.IdObraSocial = pv.IdObraSocialPrincipal
                LEFT JOIN DW.OperadoresVenta ov ON ov.IdUsuario = pv.IdOperador
                WHERE pv.IdObraSocialPrincipal = %d
                  AND pv.IdFarmacia = %d
                  AND pv.FechaDeOperacion >= %s
                  AND pv.FechaDeOperacion < %s
                  AND pv.IdTipoOperacion = 'V'
                  AND pv.ImporteACargoOS > 0
                  {extra_filter}
                ORDER BY pv.FechaDeOperacion, pv.IdOperacion, pv.NumeroRenglon
            """, tuple(params))
            rows = cur.fetchall()
    finally:
        conn.close()

    ops = {}
    for r in rows:
        op_id = int(r['IdOperacion'])
        if op_id not in ops:
            tipo = (r['TipoComp'] or '').strip() or None
            pv = r['PV']
            nro = r['NroComp']
            comp = None
            if tipo and pv is not None and nro is not None:
                comp = f'{tipo} {pv:04d}-{nro:08d}'
            ops[op_id] = {
                'id_operacion':       op_id,
                'fecha_operacion':    r['FechaDeOperacion'],
                'obra_social':        (r['ObraSocial'] or '').strip() or '—',
                'operador_nombre':    (r['OperadorNombre'] or '').strip() or '—',
                'importe_total':      0.0,
                'importe_a_cargo_os': 0.0,
                'comprobante':        comp,
                'items':              [],
            }
        ops[op_id]['importe_total']      += float(r['Importe'] or 0)
        ops[op_id]['importe_a_cargo_os'] += float(r['ImporteACargoOS'] or 0)
        ops[op_id]['items'].append({
            'producto': (r['Producto'] or '').strip() or f"prod#{r['IdProductoVendido']}",
            'cantidad': float(r['Cantidad'] or 0),
        })

    return sorted(ops.values(), key=lambda x: x['fecha_operacion'] or datetime.min)


def buscar_recetas(vendedor_uuid=None, obra_social_id=None,
                    desde=None, hasta=None, id_farmacia=None,
                    solo_a_cargo_os=False):
    """Búsqueda flexible de recetas para rendición.

    Args:
        vendedor_uuid: UUID de operador (opcional pero recomendado).
        obra_social_id: filtro adicional por OS (opcional).
        desde, hasta: rango de fechas (obligatorio).
        id_farmacia: default OBSERVER_ID_FARMACIA.
        solo_a_cargo_os: si True filtra ImporteACargoOS > 0. Si False trae todas
            las ventas a OS (incluyendo descuentos parciales con 0 a cargo).
    """
    from datetime import datetime, time, timedelta
    cfg = _config()
    if not cfg:
        raise RuntimeError('ObServer no configurado')
    if id_farmacia is None:
        id_farmacia = cfg['id_farmacia']
    if desde is None or hasta is None:
        raise ValueError('desde y hasta son obligatorios')

    hasta_dt = (hasta if hasattr(hasta, 'date')
                else datetime.combine(hasta, time(0, 0)) + timedelta(days=1))
    desde_dt = (desde if hasattr(desde, 'date')
                else datetime.combine(desde, time(0, 0)))

    conn = _connect(timeout=60)
    if conn is None:
        raise RuntimeError('ObServer no disponible')
    try:
        with conn.cursor(as_dict=True) as cur:
            extra = []
            params = [int(id_farmacia), desde_dt, hasta_dt]
            if vendedor_uuid:
                extra.append(" AND pv.IdOperador = %s")
                params.append(vendedor_uuid)
            if obra_social_id:
                extra.append(" AND pv.IdObraSocialPrincipal = %d")
                params.append(int(obra_social_id))
            if solo_a_cargo_os:
                extra.append(" AND pv.ImporteACargoOS > 0")
            else:
                # Cualquier venta NO particular (a OS, aunque a_cargo_os sea 0)
                extra.append(" AND pv.IdObraSocialPrincipal IS NOT NULL")
            cur.execute(f"""
                SELECT
                    pv.IdOperacion,
                    pv.IdProductoVendido,
                    pv.NumeroRenglon,
                    pv.FechaDeOperacion,
                    pv.Cantidad,
                    pv.Importe,
                    pv.ImporteACargoOS,
                    pv.Comprobante_IdFormularioAFIP AS TipoComp,
                    pv.Comprobante_PuntoDeVenta     AS PV,
                    pv.Comprobante_Numero           AS NroComp,
                    pv.IdOperador                   AS IdOperador,
                    ov.Vendedor                     AS OperadorNombre,
                    pr.Producto                     AS Producto,
                    os.Descripcion                  AS ObraSocial
                FROM DW.ProductosVendidos pv
                LEFT JOIN DW.Productos       pr ON pr.IdProducto = pv.IdProducto
                LEFT JOIN DW.ObrasSociales   os ON os.IdObraSocial = pv.IdObraSocialPrincipal
                LEFT JOIN DW.OperadoresVenta ov ON ov.IdUsuario = pv.IdOperador
                WHERE pv.IdFarmacia = %d
                  AND pv.FechaDeOperacion >= %s
                  AND pv.FechaDeOperacion < %s
                  AND pv.IdTipoOperacion = 'V'
                  {''.join(extra)}
                ORDER BY pv.FechaDeOperacion, pv.IdOperacion, pv.NumeroRenglon
            """, tuple(params))
            rows = cur.fetchall()
    finally:
        conn.close()

    ops = {}
    for r in rows:
        op_id = int(r['IdOperacion'])
        if op_id not in ops:
            tipo = (r['TipoComp'] or '').strip() or None
            pv = r['PV']
            nro = r['NroComp']
            comp = None
            if tipo and pv is not None and nro is not None:
                comp = f'{tipo} {pv:04d}-{nro:08d}'
            ops[op_id] = {
                'id_operacion':       op_id,
                'fecha_operacion':    r['FechaDeOperacion'],
                'obra_social':        (r['ObraSocial'] or '').strip() or '—',
                'operador_id':        str(r['IdOperador']) if r['IdOperador'] else None,
                'operador_nombre':    (r['OperadorNombre'] or '').strip() or '—',
                'importe_total':      0.0,
                'importe_a_cargo_os': 0.0,
                'comprobante':        comp,
                'items':              [],
            }
        ops[op_id]['importe_total']      += float(r['Importe'] or 0)
        ops[op_id]['importe_a_cargo_os'] += float(r['ImporteACargoOS'] or 0)
        ops[op_id]['items'].append({
            'producto': (r['Producto'] or '').strip() or f"prod#{r['IdProductoVendido']}",
            'cantidad': float(r['Cantidad'] or 0),
        })

    return sorted(ops.values(), key=lambda x: (x['obra_social'], x['fecha_operacion'] or datetime.min))


def buscar_recetas_vendedor(vendedor_uuid, desde, hasta, id_farmacia=None,
                             solo_os=True):
    """Busca recetas vendidas por un vendedor en un rango de fechas.

    Devuelve operaciones agregadas (1 receta = 1 IdOperacion), con la lista
    de ítems incluida. Pensado para la pantalla de devoluciones.

    Args:
        vendedor_uuid: str — UUID de DW.OperadoresVenta.IdUsuario.
        desde, hasta: date — rango (inclusivo en `hasta` hasta 23:59).
        id_farmacia: int — si None usa OBSERVER_ID_FARMACIA del env.
        solo_os: si True, solo recetas con ImporteACargoOS > 0.

    Returns: [{
        'id_operacion', 'fecha_operacion', 'obra_social',
        'importe_total', 'importe_a_cargo_os', 'comprobante',
        'items': [{'producto', 'cantidad'}, ...]
    }]
    """
    from datetime import datetime, time, timedelta
    cfg = _config()
    if not cfg:
        raise RuntimeError('ObServer no configurado')
    if id_farmacia is None:
        id_farmacia = cfg['id_farmacia']

    # hasta exclusive (sumar 1 día y usar <)
    if hasattr(hasta, 'date'):
        hasta_dt = hasta
    else:
        hasta_dt = datetime.combine(hasta, time(0, 0)) + timedelta(days=1)
    if hasattr(desde, 'date'):
        desde_dt = desde
    else:
        desde_dt = datetime.combine(desde, time(0, 0))

    conn = _connect(timeout=60)
    if conn is None:
        raise RuntimeError('ObServer no disponible')
    try:
        with conn.cursor(as_dict=True) as cur:
            extra_filter = " AND pv.ImporteACargoOS > 0" if solo_os else ""
            cur.execute(f"""
                SELECT
                    pv.IdOperacion,
                    pv.IdProductoVendido,
                    pv.NumeroRenglon,
                    pv.FechaDeOperacion,
                    pv.Cantidad,
                    pv.Importe,
                    pv.ImporteACargoOS,
                    pv.Comprobante_IdFormularioAFIP AS TipoComp,
                    pv.Comprobante_PuntoDeVenta     AS PV,
                    pv.Comprobante_Numero           AS NroComp,
                    pr.Producto                     AS Producto,
                    os.Descripcion                  AS ObraSocial
                FROM DW.ProductosVendidos pv
                LEFT JOIN DW.Productos      pr ON pr.IdProducto = pv.IdProducto
                LEFT JOIN DW.ObrasSociales  os ON os.IdObraSocial = pv.IdObraSocialPrincipal
                WHERE pv.IdOperador = %s
                  AND pv.IdFarmacia = %d
                  AND pv.FechaDeOperacion >= %s
                  AND pv.FechaDeOperacion < %s
                  AND pv.IdTipoOperacion = 'V'
                  {extra_filter}
                ORDER BY pv.FechaDeOperacion, pv.IdOperacion, pv.NumeroRenglon
            """, (vendedor_uuid, int(id_farmacia), desde_dt, hasta_dt))
            rows = cur.fetchall()
    finally:
        conn.close()

    # Agrupar por IdOperacion
    ops = {}
    for r in rows:
        op_id = int(r['IdOperacion'])
        if op_id not in ops:
            tipo = (r['TipoComp'] or '').strip() or None
            pv = r['PV']
            nro = r['NroComp']
            comp = None
            if tipo and pv is not None and nro is not None:
                comp = f'{tipo} {pv:04d}-{nro:08d}'
            ops[op_id] = {
                'id_operacion':       op_id,
                'fecha_operacion':    r['FechaDeOperacion'],
                'obra_social':        (r['ObraSocial'] or '').strip() or '—',
                'importe_total':      0.0,
                'importe_a_cargo_os': 0.0,
                'comprobante':        comp,
                'items':              [],
            }
        ops[op_id]['importe_total']      += float(r['Importe'] or 0)
        ops[op_id]['importe_a_cargo_os'] += float(r['ImporteACargoOS'] or 0)
        ops[op_id]['items'].append({
            'producto': (r['Producto'] or '').strip() or f"prod#{r['IdProductoVendido']}",
            'cantidad': float(r['Cantidad'] or 0),
        })

    return sorted(ops.values(), key=lambda x: x['fecha_operacion'] or datetime.min)


def listar_vendedores(solo_habilitados=True):
    """Devuelve [{'id_usuario': uuid_str, 'nombre': str}] de DW.OperadoresVenta."""
    conn = _connect(timeout=30)
    if conn is None:
        raise RuntimeError('ObServer no disponible')
    out = []
    try:
        with conn.cursor(as_dict=True) as cur:
            sql = "SELECT IdUsuario, Vendedor, Habilitado, FechaBaja FROM DW.OperadoresVenta"
            if solo_habilitados:
                sql += " WHERE Habilitado = 1 AND FechaBaja IS NULL"
            sql += " ORDER BY Vendedor"
            cur.execute(sql)
            for r in cur.fetchall():
                out.append({
                    'id_usuario': str(r['IdUsuario']),
                    'nombre': r['Vendedor'],
                })
    finally:
        conn.close()
    return out


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

def get_ventas_laboratorio(laboratorio, anio_hasta, mes_hasta):
    """Devuelve productos del laboratorio con ventas de 12 meses terminando en (anio_hasta, mes_hasta).

    Formato compatible con lo que devolvían los parsers de sales_history:
        [{'codigo_barra', 'nombre', 'precio_pvp', 'stock', 'ventas': [12 valores]}]

    El 'codigo_barra' es el IdProducto de ObServer convertido a string. Hasta
    que tengamos mapeo EAN↔IdProducto, esto permite trabajar pero los
    matchings contra tabla productos se hacen por observer_id vía el puente.
    """
    from database import ObsLaboratorio, ObsProducto, ObsStock, ObsVentaMensual, get_db
    cfg = _config()
    id_farmacia = cfg['id_farmacia'] if cfg else int(os.environ.get('OBSERVER_ID_FARMACIA', '10525'))

    # 12 meses hacia atrás desde (anio_hasta, mes_hasta)
    meses = []
    y, m = anio_hasta, mes_hasta
    for _ in range(12):
        meses.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    meses.reverse()  # viejo → nuevo
    desde_key = meses[0][0] * 100 + meses[0][1]
    hasta_key = meses[-1][0] * 100 + meses[-1][1]

    with get_db() as session:
        lab = (session.query(ObsLaboratorio)
               .filter(ObsLaboratorio.descripcion == laboratorio).first())
        if not lab:
            return []

        productos = (session.query(ObsProducto)
                     .filter(ObsProducto.laboratorio_observer == lab.observer_id,
                             ObsProducto.fecha_baja.is_(None))
                     .all())
        if not productos:
            return []

        prod_ids = [p.observer_id for p in productos]

        ventas_rows = (session.query(ObsVentaMensual)
                       .filter(ObsVentaMensual.id_farmacia == id_farmacia,
                               ObsVentaMensual.producto_observer.in_(prod_ids),
                               ObsVentaMensual.anio * 100 + ObsVentaMensual.mes >= desde_key,
                               ObsVentaMensual.anio * 100 + ObsVentaMensual.mes <= hasta_key)
                       .all())
        mapa_ventas = {}
        # PVP estimado por producto = sum(monto) / sum(unidades) de los 12 meses
        # disponibles. Es el precio promedio efectivo de venta — para muchas
        # vistas alcanza, pero no captura aumentos recientes con precisión.
        # Si en el futuro tenemos PVP "actual" (DW.Productos) se reemplaza acá.
        agg_pvp = {}  # producto_observer → [monto_total, unidades_total]
        for v in ventas_rows:
            mapa_ventas.setdefault(v.producto_observer, {})[(v.anio, v.mes)] = float(v.unidades or 0)
            a = agg_pvp.setdefault(v.producto_observer, [0.0, 0.0])
            a[0] += float(v.monto or 0)
            a[1] += float(v.unidades or 0)
        mapa_pvp = {pid: (m / u) for pid, (m, u) in agg_pvp.items() if u > 0}

        stock_rows = (session.query(ObsStock)
                      .filter(ObsStock.id_farmacia == id_farmacia,
                              ObsStock.producto_observer.in_(prod_ids)).all())
        mapa_stock = {s.producto_observer: int(s.stock_actual or 0) for s in stock_rows}
        mapa_minimo = {s.producto_observer: int(s.minimo or 0)
                       for s in stock_rows if s.minimo}
        mapa_maximo = {s.producto_observer: int(s.maximo or 0)
                       for s in stock_rows if s.maximo}

        # Resolver rubro de cada producto vía subrubro → rubro. Una sola query
        # con join para no hacer N+1.
        from database import ObsRubro, ObsSubrubro
        subrubro_ids = {p.subrubro_observer for p in productos if p.subrubro_observer}
        mapa_rubro = {}  # subrubro_observer → 'Rubro · Subrubro'
        if subrubro_ids:
            rows_rub = (session.query(ObsSubrubro.observer_id,
                                       ObsSubrubro.descripcion,
                                       ObsRubro.descripcion)
                        .outerjoin(ObsRubro,
                                   ObsRubro.observer_id == ObsSubrubro.rubro_observer)
                        .filter(ObsSubrubro.observer_id.in_(list(subrubro_ids)))
                        .all())
            for sub_id, sub_desc, rub_desc in rows_rub:
                # Solo guardamos el rubro (no el subrubro) — el filtro es a
                # nivel rubro. En ObServer muchos rubros tienen un único
                # subrubro homónimo ("Medicamentos · Medicamentos") que
                # ensuciaría el dropdown. Si no hay rubro, fallback al
                # subrubro como mejor esfuerzo.
                etiq = (rub_desc or sub_desc or '').strip()
                if etiq:
                    mapa_rubro[sub_id] = etiq

        # Puente EAN ↔ IdProducto: traer el codigo_barra real de la tabla
        # local `productos` cuando esté vinculada por observer_id.
        # NUEVA LÓGICA (post import codbarras.txt 2026-04-27):
        # Resolver EAN directamente desde obs_codigos_barras (Orden=1 = principal).
        # Caída a productos.codigo_barra solo para casos sin EAN registrado en
        # ObServer — quedará deprecado cuando obs_codigos_barras esté completo.
        from database import ObsCodigoBarras, Producto
        ean_por_observer = dict(
            session.query(ObsCodigoBarras.producto_observer,
                          ObsCodigoBarras.codigo_barras)
            .filter(ObsCodigoBarras.producto_observer.in_(prod_ids),
                    ObsCodigoBarras.orden == 1,
                    ObsCodigoBarras.fecha_baja.is_(None)).all()
        )
        # Fallback: si algún obs_id no tiene EAN en codigos_barras, usar el
        # bridge viejo de productos local.
        ids_sin_ean = [i for i in prod_ids if i not in ean_por_observer]
        if ids_sin_ean:
            for (obs_id, cb) in session.query(
                Producto.observer_id, Producto.codigo_barra
            ).filter(Producto.observer_id.in_(ids_sin_ean),
                     Producto.codigo_barra.isnot(None),
                     ~Producto.codigo_barra.like('OBS:%')).all():
                if cb:
                    ean_por_observer[obs_id] = cb

        resultado = []
        for p in productos:
            v_mapa = mapa_ventas.get(p.observer_id, {})
            ventas = [v_mapa.get((y, m), 0) for (y, m) in meses]
            if sum(ventas) == 0 and mapa_stock.get(p.observer_id, 0) == 0:
                continue  # sin ventas ni stock → lo filtramos
            tvc = (p.id_tipo_venta_control or '').strip()
            resultado.append({
                'codigo_barra': ean_por_observer.get(p.observer_id) or '',
                'observer_id': p.observer_id,
                'sin_vincular': p.observer_id not in ean_por_observer,
                'nombre': p.descripcion,
                # PVP actual no está expuesto directamente en DW.Productos.
                # Lo derivamos del histórico (m12m/u12m) cuando hace falta;
                # acá lo dejamos en 0 a propósito porque el caller no lo
                # consume hoy. Si se necesita, calcular desde obs_ventas_mensuales.
                'precio_pvp': 0,
                'stock': mapa_stock.get(p.observer_id, 0),
                'minimo': mapa_minimo.get(p.observer_id, 0),
                'maximo': mapa_maximo.get(p.observer_id, 0),
                'rubro': mapa_rubro.get(p.subrubro_observer, ''),
                'ventas': ventas,
                'tvc': tvc,
                'es_libre': tvc == 'L',
                'es_receta': tvc in ('R', 'A'),
                'es_controlado': tvc in ('1','2','3','4','5','6','7','8'),
            })
        return resultado


def get_laboratorios_disponibles():
    """Lee laboratorios del espejo local con conteo real de productos.

    Excluye los labs con 0 productos activos — no aportan al análisis.
    """
    from sqlalchemy import func as _func

    from database import ObsLaboratorio, ObsProducto, get_db
    with get_db() as session:
        conteo = dict(
            session.query(ObsProducto.laboratorio_observer,
                          _func.count(ObsProducto.observer_id))
            .filter(ObsProducto.laboratorio_observer.isnot(None),
                    ObsProducto.fecha_baja.is_(None))
            .group_by(ObsProducto.laboratorio_observer).all()
        )
        labs = (session.query(ObsLaboratorio)
                .filter(ObsLaboratorio.fecha_baja.is_(None))
                .order_by(ObsLaboratorio.descripcion).all())
        return [{'nombre': l.descripcion,
                 'n_articulos': int(conteo.get(l.observer_id, 0)),
                 'observer_id': l.observer_id}
                for l in labs
                if conteo.get(l.observer_id, 0) > 0]


# ──────────────────────────────────────────────────────────────────────────
# Pedidos a droguería (DW.Pedidos) — lectura directa, no se espeja en obs_*.
# Usado por routes/filtro_drogueria.py para separar un pedido por la matriz
# lab×droguería. DW.Pedidos es a nivel renglón (un row por producto).
# ──────────────────────────────────────────────────────────────────────────

def get_pedidos_recientes(limit=10):
    """Últimos pedidos de DW.Pedidos agrupados por IdPedido, con nombre de proveedor.

    Devuelve [{'id_pedido', 'fecha' (iso), 'proveedor_id', 'proveedor', 'items'}].
    """
    import re
    conn = _connect(timeout=30)
    if conn is None:
        raise RuntimeError('ObServer no configurado')
    try:
        with conn.cursor(as_dict=True) as cur:
            cur.execute(f"""
                SELECT TOP {int(limit)} p.IdPedido, MIN(p.FechaPedido) AS fecha,
                       p.IdProveedor, COUNT(*) AS items, MAX(p.Comprobante) AS comprobante,
                       MAX(u.Nombre) AS usuario
                FROM DW.Pedidos p
                LEFT JOIN DW.Usuarios u ON u.IdUsuario = p.SolicitadoPor
                GROUP BY p.IdPedido, p.IdProveedor
                ORDER BY MIN(p.FechaPedido) DESC
            """)
            peds = cur.fetchall()
            ids = {p['IdProveedor'] for p in peds if p['IdProveedor'] is not None}
            nombres = {}
            if ids:
                inlist = ','.join(str(int(x)) for x in ids)
                cur.execute(f"SELECT IdProveedor, RazonSocial FROM DW.Proveedores "
                            f"WHERE IdProveedor IN ({inlist})")
                nombres = {r['IdProveedor']: r['RazonSocial'] for r in cur.fetchall()}
            out = []
            for p in peds:
                comp = p['comprobante'] or ''
                nums = re.findall(r'\d+', comp)
                nro = str(int(nums[-1])) if nums else str(p['IdPedido'])
                out.append({
                    'id_pedido':    p['IdPedido'],
                    'nro':          nro,                 # nº de comprobante (lo que se ve en ObServer)
                    'comprobante':  comp,
                    'usuario':      (p['usuario'] or '').strip(),   # "Creado por" en ObServer
                    'fecha':        p['fecha'].isoformat() if p['fecha'] else None,
                    'proveedor_id': p['IdProveedor'],
                    'proveedor':    nombres.get(p['IdProveedor'], '?'),
                    'n_items':      p['items'],
                })
            return out
    finally:
        conn.close()


def get_pedido_items(id_pedido):
    """Renglones de un pedido: DW.Pedidos + DW.Productos + DW.Laboratorios.

    Devuelve [{'cantidad', 'encargada', 'comprobante', 'id_producto', 'producto',
               'troquel', 'alfabeta', 'id_laboratorio', 'laboratorio'}].
    """
    conn = _connect(timeout=30)
    if conn is None:
        raise RuntimeError('ObServer no configurado')
    try:
        with conn.cursor(as_dict=True) as cur:
            cur.execute("""
                SELECT p.CantidadPedida, p.CantidadEncargada, p.Comprobante,
                       pr.IdProducto, pr.Producto, pr.Troquel, pr.CodigoAlfabeta,
                       pr.IdLaboratorio, l.Descripcion AS Laboratorio
                FROM DW.Pedidos p
                JOIN DW.Productos pr ON pr.IdProducto = p.IdProducto
                LEFT JOIN DW.Laboratorios l ON l.IdLaboratorio = pr.IdLaboratorio
                WHERE p.IdPedido = %s
                ORDER BY pr.Producto
            """, (int(id_pedido),))
            out = []
            for r in cur.fetchall():
                out.append({
                    'cantidad':       int(r['CantidadPedida'] or 0),
                    'encargada':      int(r['CantidadEncargada'] or 0),
                    'comprobante':    r['Comprobante'],
                    'id_producto':    r['IdProducto'],
                    'producto':       (r['Producto'] or '').strip(),
                    'troquel':        str(r['Troquel']) if r['Troquel'] is not None else '',
                    'alfabeta':       str(r['CodigoAlfabeta']) if r['CodigoAlfabeta'] is not None else '',
                    'id_laboratorio': r['IdLaboratorio'],
                    'laboratorio':    (r['Laboratorio'] or '').strip(),
                })
            return out
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Consulta de recetas por afiliado — Gestion.Recetas (schema premium)
#
# A diferencia de `buscar_recetas*` (arriba) que agregan desde `DW.ProductosVendidos`
# (1 fila por producto vendido), esta función lee `Gestion.Recetas` que tiene
# 1 fila por RECETA con campos únicos: OPF, NumeroReceta, NumeroAfiliado,
# MatriculaMedico, TotalReceta, TotalACargoOS, estado de Trazabilidad, etc.
#
# Es la fuente correcta para reproducir la vista "recetas por afiliado" del PDV
# (screenshot Diego 2026-07-11).
#
# Requiere acceso al schema Gestion (user sa). Ver docs/observer_gestion_recetas.md.
# ─────────────────────────────────────────────────────────────────────────────

_TRAZ_MAP = {'N': 'No requerido', 'P': 'Trazabilidad pendiente',
             'A': 'Asociada', 'X': 'Excluida'}


def buscar_recetas_por_afiliado(numero_afiliado, desde=None, hasta=None,
                                 id_farmacia=None, incluir_anuladas=False,
                                 incluir_productos=True, limit=500):
    """Devuelve las recetas de un afiliado desde `Gestion.Recetas`, opcionalmente
    con el detalle de productos (renglones).

    Args:
        numero_afiliado: str o int — NumeroAfiliado exacto (ej. '14004503970600').
        desde, hasta: date/datetime opcionales — filtra por FechaDeOperacion.
        id_farmacia: int opcional (default OBSERVER_ID_FARMACIA).
        incluir_anuladas: bool — si False (default), excluye Anulada=1.
        incluir_productos: bool — si True (default), incluye el detalle de
            productos (una query extra por lote a `Gestion.RecetasRenglones`).
        limit: int — tope de recetas (default 500).

    Returns:
        list[dict] ordenado por FechaDeOperacion DESC:
            id_receta, opf, numero_receta, numero_afiliado, nombre_afiliado,
            matricula_medico, plan_descripcion, plan_id,
            fecha_operacion, fecha_autorizacion, fecha_venta,
            total_receta, total_a_cargo_os, total_afiliado,
            trazabilidad_estado (código), trazabilidad_desc (texto),
            anulada, autorizada, rendida,
            productos: list[{id_producto, producto, cantidad, precio_pvp,
                             importe_renglon, importe_a_cargo_os, rechazado}]
                (vacío si incluir_productos=False).

    Requiere user con acceso a schema `Gestion` (sa en Badia).
    """
    from datetime import datetime, time, timedelta
    cfg = _config()
    if not cfg:
        raise RuntimeError('ObServer no configurado')
    if id_farmacia is None:
        id_farmacia = cfg['id_farmacia']

    filtro_fecha = ''
    params = [str(numero_afiliado), int(id_farmacia)]
    if desde is not None:
        desde_dt = desde if hasattr(desde, 'hour') else datetime.combine(desde, time(0, 0))
        filtro_fecha += ' AND r.FechaDeOperacion >= %s'
        params.append(desde_dt)
    if hasta is not None:
        if hasattr(hasta, 'hour'):
            hasta_dt = hasta
        else:
            hasta_dt = datetime.combine(hasta, time(0, 0)) + timedelta(days=1)
        filtro_fecha += ' AND r.FechaDeOperacion < %s'
        params.append(hasta_dt)
    filtro_anul = '' if incluir_anuladas else ' AND r.Anulada = 0'

    conn = _connect(timeout=60)
    if conn is None:
        raise RuntimeError('ObServer no disponible')
    try:
        with conn.cursor(as_dict=True) as cur:
            sql = f"""
                SELECT TOP {int(limit)}
                    r.IdReceta, r.OPF, r.NumeroReceta, r.NumeroAfiliado,
                    r.NombreAfiliado, r.MatriculaMedico,
                    r.IdPlan, p.Descripcion AS PlanDescripcion,
                    r.FechaDeOperacion, r.FechaAutorizacionOnLine, r.FechaDeVenta,
                    r.TotalReceta, r.TotalACargoOS, r.TotalAfiliado,
                    r.IdEstadoAsociacionTrazabilidad AS TrazEstado,
                    r.Anulada, r.Autorizada, r.Rendida
                  FROM Gestion.Recetas r
                  LEFT JOIN DW.Planes p ON p.IdPlan = r.IdPlan
                 WHERE r.NumeroAfiliado = %s
                   AND r.IdFarmacia = %d
                   {filtro_fecha}
                   {filtro_anul}
                 ORDER BY r.FechaDeOperacion DESC
            """
            cur.execute(sql, tuple(params))
            recetas = cur.fetchall()

            productos_por_receta = {}
            if incluir_productos and recetas:
                ids = [r['IdReceta'] for r in recetas]
                # pymssql no soporta expansión de lista → armo placeholders.
                placeholders = ','.join(['%d'] * len(ids))
                cur.execute(f"""
                    SELECT rr.IdReceta, rr.IdProducto, rr.Cantidad,
                           rr.PrecioPVP, rr.ImporteRenglon,
                           rr.ImporteACargoOS, rr.Rechazado,
                           pr.Producto
                      FROM Gestion.RecetasRenglones rr
                      LEFT JOIN DW.Productos pr ON pr.IdProducto = rr.IdProducto
                     WHERE rr.IdReceta IN ({placeholders})
                     ORDER BY rr.IdReceta, rr.NumeroRenglon
                """, tuple(ids))
                for rr in cur.fetchall():
                    productos_por_receta.setdefault(rr['IdReceta'], []).append({
                        'id_producto':        rr['IdProducto'],
                        'producto':           (rr['Producto'] or '').strip() or f"prod#{rr['IdProducto']}",
                        'cantidad':           int(rr['Cantidad'] or 0),
                        'precio_pvp':         float(rr['PrecioPVP'] or 0),
                        'importe_renglon':    float(rr['ImporteRenglon'] or 0),
                        'importe_a_cargo_os': float(rr['ImporteACargoOS'] or 0),
                        'rechazado':          bool(rr['Rechazado']),
                    })
    finally:
        conn.close()

    out = []
    for r in recetas:
        traz = (r['TrazEstado'] or '').strip()
        out.append({
            'id_receta':            r['IdReceta'],
            'opf':                  r['OPF'],
            'numero_receta':        r['NumeroReceta'],
            'numero_afiliado':      r['NumeroAfiliado'],
            'nombre_afiliado':      (r['NombreAfiliado'] or '').strip(),
            'matricula_medico':     r['MatriculaMedico'],
            'plan_id':              r['IdPlan'],
            'plan_descripcion':     (r['PlanDescripcion'] or '').strip(),
            'fecha_operacion':      r['FechaDeOperacion'],
            'fecha_autorizacion':   r['FechaAutorizacionOnLine'],
            'fecha_venta':          r['FechaDeVenta'],
            'total_receta':         float(r['TotalReceta'] or 0),
            'total_a_cargo_os':     float(r['TotalACargoOS'] or 0),
            'total_afiliado':       float(r['TotalAfiliado'] or 0),
            'trazabilidad_estado':  traz or None,
            'trazabilidad_desc':    _TRAZ_MAP.get(traz, traz) if traz else None,
            'anulada':              bool(r['Anulada']),
            'autorizada':           bool(r['Autorizada']),
            'rendida':              bool(r['Rendida']),
            'productos':            productos_por_receta.get(r['IdReceta'], []),
        })
    return out
