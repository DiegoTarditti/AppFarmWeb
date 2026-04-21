"""Capa de acceso a la DB de ObServer (o su simulación en docker).

Expone funciones que devuelven datos en el formato que el resto de la app ya usa,
para que sea intercambiable con la fuente de archivos (PDF/Excel).

Si OBSERVER_DATABASE_URL no está seteado, todas las funciones devuelven
observer_disponible()=False y cada consulta retorna None o lista vacía.
"""

import os
import logging
from contextlib import contextmanager
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

_log = logging.getLogger(__name__)

_engine = None
_Session = None
_url = None


def _init():
    global _engine, _Session, _url
    _url = os.environ.get('OBSERVER_DATABASE_URL')
    if not _url:
        return
    if _url.startswith('postgres://'):
        _url = _url.replace('postgres://', 'postgresql://', 1)
    _engine = create_engine(_url, echo=False, future=True, pool_pre_ping=True)
    _Session = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


_init()


@contextmanager
def _session():
    if _Session is None:
        yield None
        return
    s = _Session()
    try:
        yield s
    finally:
        s.close()


def observer_disponible():
    """True si la DB de ObServer responde a un ping simple."""
    if _engine is None:
        return False
    try:
        with _engine.connect() as conn:
            conn.execute(text('SELECT 1'))
        return True
    except Exception as e:
        _log.warning('ObServer no responde: %s', e)
        return False


def get_articulo(codigo_barra):
    """Devuelve dict con datos maestros del artículo o None."""
    if not codigo_barra:
        return None
    with _session() as s:
        if s is None:
            return None
        row = s.execute(text("""
            SELECT codigo_barra, descripcion, laboratorio, monodroga,
                   presentacion, accion_terapeutica, precio_pvp, stock_actual
            FROM articulos WHERE codigo_barra = :ean
        """), {'ean': codigo_barra}).first()
        if not row:
            return None
        return {
            'codigo_barra': row.codigo_barra, 'descripcion': row.descripcion,
            'laboratorio': row.laboratorio, 'monodroga': row.monodroga,
            'presentacion': row.presentacion, 'accion_terapeutica': row.accion_terapeutica,
            'precio_pvp': float(row.precio_pvp or 0), 'stock': int(row.stock_actual or 0),
        }


def get_ventas_12_meses(codigo_barra, anio_hasta, mes_hasta):
    """Devuelve lista de 12 unidades mensuales terminando en (anio_hasta, mes_hasta).
    Los meses sin ventas devuelven 0. El orden es del más viejo al más nuevo.
    """
    if not codigo_barra:
        return [0] * 12
    # Calcular los 12 (año, mes) hacia atrás
    meses = []
    y, m = anio_hasta, mes_hasta
    for _ in range(12):
        meses.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    meses.reverse()  # del más viejo al más nuevo

    with _session() as s:
        if s is None:
            return [0] * 12
        rows = s.execute(text("""
            SELECT anio, mes, unidades FROM ventas_mensuales
            WHERE codigo_barra = :ean AND (anio, mes) IN :tuplas
        """).bindparams().execution_options(), {
            'ean': codigo_barra, 'tuplas': tuple(meses)
        }).all() if False else None
        # Fallback con query más portable
        rows = s.execute(text("""
            SELECT anio, mes, unidades FROM ventas_mensuales
            WHERE codigo_barra = :ean
              AND (anio * 100 + mes) BETWEEN :desde AND :hasta
        """), {
            'ean': codigo_barra,
            'desde': meses[0][0] * 100 + meses[0][1],
            'hasta': meses[-1][0] * 100 + meses[-1][1],
        }).all()
        mapa = {(r.anio, r.mes): int(r.unidades or 0) for r in rows}
        return [mapa.get((y, m), 0) for (y, m) in meses]


def get_ventas_laboratorio(laboratorio, anio_hasta, mes_hasta):
    """Devuelve lista de productos del laboratorio con sus ventas 12 meses.
    Formato compatible con lo que devuelve el parser de sales_history.
    """
    if not laboratorio:
        return []
    # Reutilizar cálculo de meses
    meses = []
    y, m = anio_hasta, mes_hasta
    for _ in range(12):
        meses.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    meses.reverse()
    desde_key = meses[0][0] * 100 + meses[0][1]
    hasta_key = meses[-1][0] * 100 + meses[-1][1]

    with _session() as s:
        if s is None:
            return []
        arts = s.execute(text("""
            SELECT codigo_barra, descripcion, precio_pvp, stock_actual
            FROM articulos WHERE laboratorio = :lab
            ORDER BY descripcion
        """), {'lab': laboratorio}).all()
        if not arts:
            return []
        eans = [a.codigo_barra for a in arts]
        rows = s.execute(text("""
            SELECT codigo_barra, anio, mes, unidades
            FROM ventas_mensuales
            WHERE codigo_barra = ANY(:eans)
              AND (anio * 100 + mes) BETWEEN :desde AND :hasta
        """), {'eans': eans, 'desde': desde_key, 'hasta': hasta_key}).all()
        ventas_por_ean = {}
        for r in rows:
            ventas_por_ean.setdefault(r.codigo_barra, {})[(r.anio, r.mes)] = int(r.unidades or 0)
        productos = []
        for a in arts:
            v_mapa = ventas_por_ean.get(a.codigo_barra, {})
            ventas = [v_mapa.get((y, m), 0) for (y, m) in meses]
            productos.append({
                'codigo_barra': a.codigo_barra,
                'nombre': a.descripcion,
                'precio_pvp': float(a.precio_pvp or 0),
                'stock': int(a.stock_actual or 0),
                'ventas': ventas,
            })
        return productos


def get_laboratorios_disponibles():
    """Lista de laboratorios con al menos un producto en ObServer."""
    with _session() as s:
        if s is None:
            return []
        rows = s.execute(text("""
            SELECT laboratorio, COUNT(*) AS n_articulos
            FROM articulos WHERE laboratorio IS NOT NULL AND laboratorio <> ''
            GROUP BY laboratorio ORDER BY laboratorio
        """)).all()
        return [{'nombre': r.laboratorio, 'n_articulos': int(r.n_articulos)} for r in rows]


def get_recepciones_factura(numero_factura, proveedor_cuit=None):
    """Devuelve los ítems recepcionados de una factura (para el cruce)."""
    if not numero_factura:
        return []
    with _session() as s:
        if s is None:
            return []
        params = {'numero': numero_factura}
        q = """
            SELECT codigo_barra, descripcion, cantidad, precio_unitario,
                   lote, vencimiento, fecha_recepcion
            FROM recepciones WHERE numero_factura = :numero
        """
        if proveedor_cuit:
            q += " AND proveedor_cuit = :cuit"
            params['cuit'] = proveedor_cuit
        q += " ORDER BY descripcion"
        rows = s.execute(text(q), params).all()
        return [{
            'codigo_barra': r.codigo_barra, 'descripcion': r.descripcion,
            'cantidad': int(r.cantidad or 0),
            'precio_unitario': float(r.precio_unitario or 0),
            'lote': r.lote or '',
            'vencimiento': r.vencimiento.strftime('%d/%m/%Y') if r.vencimiento else '',
            'fecha_recepcion': r.fecha_recepcion.strftime('%d/%m/%Y') if r.fecha_recepcion else '',
        } for r in rows]


def get_stock(codigo_barra):
    """Stock actual de un producto según ObServer."""
    art = get_articulo(codigo_barra)
    return art['stock'] if art else None
