"""Análisis de transferencias entre sucursales (Pieri <-> Badia).

Compara stock vs venta mensual de cada producto en ambas farmacias, cruzando por
`codigo_alfabeta` (el `observer_id`/IdProducto difiere entre instalaciones de
ObServer, el Alfabeta del vademécum NO). Sugiere mover producto de la sucursal
con EXCEDENTE a la que lo NECESITA.

Solo lectura sobre ambas DBs. La DB local es `DATABASE_URL`; la otra sucursal,
`BADIA_DATABASE_URL`. Qué farmacia es la local depende de la instancia:
`TRANSFER_LOCAL_ES` = 'pieri' (default, instancia Pieri) o 'badia' (instancia
Badia). Las columnas Badia/Pieri salen correctas en cualquiera de las dos.
"""
import os
from datetime import date

import sqlalchemy as sa


def _engine(url, render=False):
    if url.startswith('postgresql://'):
        url = url.replace('postgresql://', 'postgresql+psycopg2://', 1)
    args = {'connect_timeout': 30}
    if render or 'render.com' in url:
        args['sslmode'] = 'require'
    return sa.create_engine(url, connect_args=args)


def _pull(eng, desde_key):
    """Devuelve (stock_por_alfa, avg_mensual_por_alfa, desc_por_alfa)."""
    with eng.connect() as c:
        stock = {a: float(s or 0) for a, s in c.execute(sa.text("""
            SELECT op.codigo_alfabeta, SUM(s.stock_actual)
            FROM obs_stock s JOIN obs_productos op ON op.observer_id = s.producto_observer
            WHERE op.codigo_alfabeta IS NOT NULL AND op.fecha_baja IS NULL
            GROUP BY op.codigo_alfabeta""")).fetchall()}
        avg = {a: float(u or 0) / 12.0 for a, u in c.execute(sa.text("""
            SELECT op.codigo_alfabeta, SUM(v.unidades)
            FROM obs_ventas_mensuales v JOIN obs_productos op ON op.observer_id = v.producto_observer
            WHERE op.codigo_alfabeta IS NOT NULL AND (v.anio * 100 + v.mes) >= :d
            GROUP BY op.codigo_alfabeta"""), {'d': desde_key}).fetchall()}
        desc = {a: d for a, d in c.execute(sa.text("""
            SELECT codigo_alfabeta, MIN(descripcion) FROM obs_productos
            WHERE codigo_alfabeta IS NOT NULL AND fecha_baja IS NULL
            GROUP BY codigo_alfabeta""")).fetchall()}
    return stock, avg, desc


def _cob(stock, avg):
    """Meses de cobertura. 999 = stock sin ventas (cobertura 'infinita')."""
    if avg and avg > 0:
        return stock / avg
    return 999.0 if stock > 0 else 0.0


def analizar(excedente_meses=6.0, necesita_meses=2.0, local_url=None, badia_url=None,
             local_es=None):
    local_url = local_url or os.environ.get('DATABASE_URL', '')
    badia_url = badia_url or os.environ.get('BADIA_DATABASE_URL', '')
    if not badia_url:
        return {'ok': False, 'error': 'BADIA_DATABASE_URL no configurada en el entorno (.env).'}
    # Qué farmacia es la DB local. En la instancia de Pieri (default) la local es
    # Pieri; en la de Badia hay que setear TRANSFER_LOCAL_ES=badia. Así las
    # columnas Badia/Pieri salen correctas corra donde corra. BADIA_DATABASE_URL
    # es siempre "la OTRA sucursal": en Pieri apunta a Badia, en Badia a Pieri.
    local_es = (local_es or os.environ.get('TRANSFER_LOCAL_ES', 'pieri')).strip().lower()

    hoy = date.today()
    desde_key = (hoy.year - 1) * 100 + hoy.month  # últimos ~12 meses

    try:
        loc = _pull(_engine(local_url), desde_key)
        rem = _pull(_engine(badia_url, render=True), desde_key)
    except Exception as e:
        return {'ok': False, 'error': f'No pude leer alguna DB: {e}'}

    # b_* = datos de Badia, p_* = datos de Pieri SIEMPRE (el resto del cálculo y
    # el template lo asumen). Mapear cada pull a su sucursal según la instancia.
    if local_es == 'badia':
        (b_stock, b_avg, b_desc), (p_stock, p_avg, p_desc) = loc, rem
    else:
        (p_stock, p_avg, p_desc), (b_stock, b_avg, b_desc) = loc, rem

    U, L = float(excedente_meses), float(necesita_meses)
    filas = []
    for a in (set(p_stock) | set(b_stock)):
        bs, ba = b_stock.get(a, 0.0), b_avg.get(a, 0.0)
        ps, pa = p_stock.get(a, 0.0), p_avg.get(a, 0.0)
        bcob, pcob = _cob(bs, ba), _cob(ps, pa)
        direccion = qty = None
        # Badia excedente -> Pieri necesita
        if bcob > U and bs > 0 and pa > 0 and pcob < L:
            q = int(min(bs - U * ba, L * pa - ps))
            if q >= 1:
                direccion, qty = 'badia_a_pieri', q
        # Pieri excedente -> Badia necesita
        if direccion is None and pcob > U and ps > 0 and ba > 0 and bcob < L:
            q = int(min(ps - U * pa, L * ba - bs))
            if q >= 1:
                direccion, qty = 'pieri_a_badia', q
        if not direccion:
            continue
        filas.append({
            'alfabeta': a,
            'descripcion': (b_desc.get(a) or p_desc.get(a) or a),
            'b_stock': round(bs), 'b_avg': round(ba, 1), 'b_cob': round(bcob, 1),
            'p_stock': round(ps), 'p_avg': round(pa, 1), 'p_cob': round(pcob, 1),
            'direccion': direccion, 'qty': qty,
        })
    filas.sort(key=lambda f: -f['qty'])
    n_bp = sum(1 for f in filas if f['direccion'] == 'badia_a_pieri')
    return {
        'ok': True, 'filas': filas, 'total': len(filas),
        'n_badia_a_pieri': n_bp, 'n_pieri_a_badia': len(filas) - n_bp,
        'excedente_meses': U, 'necesita_meses': L,
    }
