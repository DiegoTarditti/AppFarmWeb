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
    from database import ObsVentaMensual, ObsProducto, now_ar
    from datetime import datetime
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
            cur.execute("""
                SELECT IdProducto,
                       Anio = [Año],
                       Mes,
                       Unidades = SUM(Cantidad),
                       Monto    = SUM(ImporteNeto),
                       Trx      = COUNT(*)
                FROM DW.ProductosVendidos
                WHERE IdFarmacia = %d
                  AND IdTipoOperacion = 'V'
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
    _log_sync(session, 'ventas_mensuales', n, duracion, extra)
    return {'upsert': n, 'duracion_ms': duracion, 'meses': meses, 'skipped': skipped}


def estado_ventas_mensuales(session, dias_fresco=7):
    """Devuelve dict con estado de frescura de obs_ventas_mensuales.

    {
      'estado': 'fresco' | 'viejo' | 'nunca',
      'ultimo_sync': datetime o None,
      'dias': int,
      'filas': int,
      'mensaje': str,
    }
    """
    from database import ObsSyncLog, ObsVentaMensual, now_ar
    filas = session.query(ObsVentaMensual).count()
    ultimo = (session.query(ObsSyncLog)
              .filter(ObsSyncLog.entidad == 'ventas_mensuales')
              .order_by(ObsSyncLog.ejecutado_en.desc()).first())
    if filas == 0:
        return {'estado': 'nunca', 'ultimo_sync': None, 'dias': None, 'filas': 0,
                'mensaje': 'Todavía no se importaron ventas desde ObServer.'}
    if not ultimo:
        # Hay datos pero no hay log (ej. se importaron desde otra máquina vía pull).
        return {'estado': 'fresco', 'ultimo_sync': None, 'dias': 0, 'filas': filas,
                'mensaje': f'{filas} filas de ventas disponibles (origen externo).'}
    delta = (now_ar() - ultimo.ejecutado_en).days
    if delta <= dias_fresco:
        return {'estado': 'fresco', 'ultimo_sync': ultimo.ejecutado_en, 'dias': delta,
                'filas': filas,
                'mensaje': f'Estadísticas al día — última actualización hace {delta} día(s).'}
    return {'estado': 'viejo', 'ultimo_sync': ultimo.ejecutado_en, 'dias': delta,
            'filas': filas,
            'mensaje': f'Estadísticas desactualizadas — última sincronización hace {delta} día(s).'}


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
    from database import (get_db, ObsLaboratorio, ObsProducto,
                          ObsStock, ObsVentaMensual)
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
        for v in ventas_rows:
            mapa_ventas.setdefault(v.producto_observer, {})[(v.anio, v.mes)] = float(v.unidades or 0)

        stock_rows = (session.query(ObsStock)
                      .filter(ObsStock.id_farmacia == id_farmacia,
                              ObsStock.producto_observer.in_(prod_ids)).all())
        mapa_stock = {s.producto_observer: int(s.stock_actual or 0) for s in stock_rows}

        # Puente EAN ↔ IdProducto: traer el codigo_barra real de la tabla
        # local `productos` cuando esté vinculada por observer_id.
        # Si un producto de ObServer no está vinculado, queda sin EAN (None)
        # — así el cliente puede detectarlo y resolver la vinculación.
        from database import Producto
        ean_por_observer = {
            obs_id: cb for (obs_id, cb) in session.query(
                Producto.observer_id, Producto.codigo_barra
            ).filter(Producto.observer_id.in_(prod_ids)).all() if cb
        }

        resultado = []
        for p in productos:
            v_mapa = mapa_ventas.get(p.observer_id, {})
            ventas = [v_mapa.get((y, m), 0) for (y, m) in meses]
            if sum(ventas) == 0 and mapa_stock.get(p.observer_id, 0) == 0:
                continue  # sin ventas ni stock → lo filtramos
            resultado.append({
                'codigo_barra': ean_por_observer.get(p.observer_id) or '',
                'observer_id': p.observer_id,
                'sin_vincular': p.observer_id not in ean_por_observer,
                'nombre': p.descripcion,
                'precio_pvp': 0,  # TODO: cuando tengamos precio en DW
                'stock': mapa_stock.get(p.observer_id, 0),
                'ventas': ventas,
            })
        return resultado


def get_laboratorios_disponibles():
    """Lee laboratorios del espejo local con conteo real de productos."""
    from sqlalchemy import func as _func
    from database import get_db, ObsLaboratorio, ObsProducto
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
                for l in labs]
