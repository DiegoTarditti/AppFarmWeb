"""Sync peer-to-peer de archivos compartidos (sin hub).

Cada instancia publica en SU propia tabla `archivos_compartidos` (INSERT local;
ver routes/compartido.py). Este módulo LEE read-only los compartidos de las
OTRAS sucursales vía `Sucursal.url_externa` (mismo patrón y mismo engine que
/transferencias) y resuelve qué queda pendiente según el log local
`compartido_importado`. No escribe nunca en la DB de otra instancia.
"""
import json
import time

import sqlalchemy as sa

from services.transferencias import _engine, _local_slug, listar_sucursales


def peers():
    """Sucursales activas distintas de la local, con url_externa. [] si no hay."""
    sucs = listar_sucursales()
    if not sucs:
        return []
    local = _local_slug(sucs)
    return [s for s in sucs if s['slug'] != local and s.get('url_externa')]


def _filas_peer(url, limit=100):
    """SELECT read-only del archivos_compartidos de un peer.

    Solo columnas presentes en el esquema viejo — NO incluye `destinatarios`
    (columna nueva) para poder leer peers que aún no migraron (deploy rolling)."""
    with _engine(url).connect() as c:
        return c.execute(sa.text("""
            SELECT id, tipo, nombre, descripcion, farmacia_origen,
                   n_items, creado_en
            FROM archivos_compartidos
            ORDER BY creado_en DESC
            LIMIT :lim
        """), {'lim': limit}).fetchall()


def listar_peers(limit=100):
    """Compartidos de todos los peers, con origen. Tolera peer caído (mete
    un dict con 'error' por sucursal inalcanzable, no rompe la pantalla)."""
    out = []
    for p in peers():
        try:
            for r in _filas_peer(p['url_externa'], limit):
                out.append({
                    'origen_slug': p['slug'], 'origen_nombre': p['nombre'],
                    'id': r[0], 'tipo': r[1], 'nombre': r[2], 'descripcion': r[3],
                    'farmacia_origen': r[4], 'n_items': r[5] or 0,
                    'creado_en': r[6], 'destinatarios': 'todos',
                })
        except Exception as e:
            out.append({'origen_slug': p['slug'], 'origen_nombre': p['nombre'],
                        'error': str(e)})
    return out


def leer_archivo(slug, archivo_id):
    """Trae el JSON de un compartido puntual de un peer.
    Devuelve {tipo, nombre, items} o None."""
    p = next((x for x in peers() if x['slug'] == slug), None)
    if not p:
        return None
    with _engine(p['url_externa']).connect() as c:
        row = c.execute(sa.text(
            "SELECT tipo, nombre, json_data FROM archivos_compartidos WHERE id = :id"
        ), {'id': archivo_id}).fetchone()
    if not row:
        return None
    return {'tipo': row[0], 'nombre': row[1], 'items': json.loads(row[2])}


# ── COUNT cacheado para el card del home (no pegarle al peer en cada carga) ──
_count_cache = {'ts': 0.0, 'val': 0}
_TTL = 300  # 5 min


def contar_nuevos(session, force=False):
    """N de compartidos de peers que esta instancia NO importó ni descartó.
    Cacheado 5 min por worker. `session` = DB local (lee compartido_importado).
    Ante peer caído / error → devuelve el último valor conocido (no rompe el home)."""
    now = time.time()
    if not force and (now - _count_cache['ts']) < _TTL:
        return _count_cache['val']
    try:
        from database import CompartidoImportado
        consumidos = {
            (r[0], r[1]) for r in session.query(
                CompartidoImportado.origen_slug, CompartidoImportado.archivo_id).all()
        }
        n = sum(1 for it in listar_peers()
                if 'error' not in it and (it['origen_slug'], it['id']) not in consumidos)
        _count_cache.update(ts=now, val=n)
        return n
    except Exception:
        return _count_cache['val']
