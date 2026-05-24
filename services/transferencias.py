"""Análisis de transferencias entre sucursales (comparador N-way por par).

Compara stock vs venta mensual de cada producto en dos sucursales, cruzando por
`codigo_alfabeta` (el `observer_id`/IdProducto difiere entre instalaciones de
ObServer, el Alfabeta del vademécum NO). Sugiere mover producto de la sucursal
con EXCEDENTE a la que lo NECESITA.

Las sucursales viven en la tabla `sucursales` (modelo Sucursal), cada una con su
URL de conexión (`url_externa`, alcanzable desde local y desde Render). La DB
local de la instancia es `DATABASE_URL`; se compara contra UNA otra elegida.

Fallback: si la tabla está vacía, cae al viejo `BADIA_DATABASE_URL` para no
romper instalaciones sin migrar.
"""
import os
from datetime import date

import sqlalchemy as sa


def _engine(url):
    """Engine read-only. SSL require para URLs de Render (`.render.com`)."""
    if url.startswith('postgresql://'):
        url = url.replace('postgresql://', 'postgresql+psycopg2://', 1)
    args = {'connect_timeout': 30}
    if 'render.com' in url:
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


def _db_name_de(url):
    """Nombre de la base en una URL postgres (lo que va después del último '/')."""
    if not url:
        return ''
    return url.rsplit('/', 1)[-1].split('?', 1)[0]


def listar_sucursales():
    """Lee la tabla `sucursales` activas de la DB local. [] si no existe/vacía."""
    try:
        from database import Sucursal, get_db
        with get_db() as s:
            rows = (s.query(Sucursal).filter_by(activa=True)
                    .order_by(Sucursal.nombre).all())
            return [{'slug': r.slug, 'nombre': r.nombre, 'app_name': r.app_name,
                     'db_name': r.db_name, 'url_externa': r.url_externa} for r in rows]
    except Exception:
        return []


def _local_slug(sucs):
    """Slug de la sucursal de ESTA instancia: env SUCURSAL_LOCAL, o autodetectar
    matcheando db_name contra DATABASE_URL."""
    env = (os.environ.get('SUCURSAL_LOCAL') or '').strip().lower()
    if env:
        return env
    dbn = _db_name_de(os.environ.get('DATABASE_URL', '')).lower()
    for s in sucs:
        if s.get('db_name') and s['db_name'].lower() == dbn:
            return s['slug']
    return None


def _comparar(local_url, otra_url, excedente_meses, necesita_meses, local, otra, remotas):
    """Pull de ambas DBs + lógica de excedente/necesita. local/otra son
    {'slug','nombre'}; remotas = lista para el dropdown."""
    if not otra_url:
        return {'ok': False, 'error': f'La sucursal "{otra.get("nombre")}" no tiene URL configurada.'}
    hoy = date.today()
    desde_key = (hoy.year - 1) * 100 + hoy.month  # últimos ~12 meses
    try:
        l_stock, l_avg, l_desc = _pull(_engine(local_url), desde_key)
        o_stock, o_avg, o_desc = _pull(_engine(otra_url), desde_key)
    except Exception as e:
        return {'ok': False, 'error': f'No pude leer alguna DB: {e}'}

    U, L = float(excedente_meses), float(necesita_meses)
    filas = []
    for a in (set(l_stock) | set(o_stock)):
        ls, la = l_stock.get(a, 0.0), l_avg.get(a, 0.0)
        os_, oa = o_stock.get(a, 0.0), o_avg.get(a, 0.0)
        lcob, ocob = _cob(ls, la), _cob(os_, oa)
        direccion = qty = None
        # otra excedente -> local necesita
        if ocob > U and os_ > 0 and la > 0 and lcob < L:
            q = int(min(os_ - U * oa, L * la - ls))
            if q >= 1:
                direccion, qty = 'otra_a_local', q
        # local excedente -> otra necesita
        if direccion is None and lcob > U and ls > 0 and oa > 0 and ocob < L:
            q = int(min(ls - U * la, L * oa - os_))
            if q >= 1:
                direccion, qty = 'local_a_otra', q
        if not direccion:
            continue
        filas.append({
            'alfabeta': a,
            'descripcion': (o_desc.get(a) or l_desc.get(a) or a),
            'l_stock': round(ls), 'l_avg': round(la, 1), 'l_cob': round(lcob, 1),
            'o_stock': round(os_), 'o_avg': round(oa, 1), 'o_cob': round(ocob, 1),
            'direccion': direccion, 'qty': qty,
        })
    filas.sort(key=lambda f: -f['qty'])
    n_ol = sum(1 for f in filas if f['direccion'] == 'otra_a_local')
    return {
        'ok': True, 'filas': filas, 'total': len(filas),
        'local': local, 'otra': otra, 'remotas': remotas,
        'n_otra_a_local': n_ol, 'n_local_a_otra': len(filas) - n_ol,
        'excedente_meses': U, 'necesita_meses': L,
    }


def analizar(excedente_meses=6.0, necesita_meses=2.0, otra=None):
    """Compara la sucursal local (DATABASE_URL) contra otra del registro.

    `otra` = slug de la sucursal a comparar (default: la primera remota activa).
    """
    local_url = os.environ.get('DATABASE_URL', '')
    sucs = listar_sucursales()
    local_slug = _local_slug(sucs)
    remotas = [s for s in sucs if s['slug'] != local_slug]

    # Fallback: sin otras sucursales en la tabla → comportamiento viejo.
    if not remotas:
        badia_url = os.environ.get('BADIA_DATABASE_URL', '')
        if not badia_url:
            return {'ok': False,
                    'error': 'No hay otras sucursales cargadas. Agregá una en /sucursales.'}
        local_nombre = next((s['nombre'] for s in sucs if s['slug'] == local_slug), None) or 'Local'
        return _comparar(local_url, badia_url, excedente_meses, necesita_meses,
                         {'slug': local_slug or 'local', 'nombre': local_nombre},
                         {'slug': 'otra', 'nombre': 'Otra'},
                         [{'slug': 'otra', 'nombre': 'Otra'}])

    otra_suc = next((s for s in remotas if s['slug'] == otra), remotas[0])
    local_suc = next((s for s in sucs if s['slug'] == local_slug),
                     {'slug': local_slug or 'local', 'nombre': 'Local'})
    return _comparar(
        local_url, otra_suc.get('url_externa'), excedente_meses, necesita_meses,
        {'slug': local_suc['slug'], 'nombre': local_suc['nombre']},
        {'slug': otra_suc['slug'], 'nombre': otra_suc['nombre']},
        [{'slug': s['slug'], 'nombre': s['nombre']} for s in remotas])
