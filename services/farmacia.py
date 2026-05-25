"""Resolución de la farmacia operativa (ObServer `id_farmacia`).

Cada instancia trabaja con UNA farmacia. El id sale de, en orden:
  1) env `OBSERVER_ID_FARMACIA` si está seteada;
  2) si no, AUTODETECCIÓN desde la data (`obs_stock` tiene un solo `id_farmacia`
     por instancia) — así una instancia nueva no muestra todo vacío por una env
     olvidada (le pasó a Pieri: data id_farmacia=764 vs default 10525 de Badia);
  3) último recurso `10525` (legacy) solo si no hay env NI data.

Reemplaza el patrón frágil `int(os.environ.get('OBSERVER_ID_FARMACIA','10525'))`
que estaba copy-pasteado en ~20 lugares y mentía en cualquier instancia != Badia.
"""
import os

_LEGACY_DEFAULT = 10525
_cache = {'autodetect': None}


def farmacia_operativa():
    """ID de la farmacia operativa (env → autodetección → legacy)."""
    env = os.environ.get('OBSERVER_ID_FARMACIA', '').strip()
    if env:
        try:
            return int(env)
        except ValueError:
            pass
    if _cache['autodetect'] is not None:
        return _cache['autodetect']
    val = _autodetectar()
    if val is not None:
        _cache['autodetect'] = val
        return val
    return _LEGACY_DEFAULT


def _autodetectar():
    """id_farmacia desde obs_stock (un solo valor por instancia). None si no se puede."""
    try:
        import database
        with database.get_db() as s:
            rows = (s.query(database.ObsStock.id_farmacia)
                    .filter(database.ObsStock.id_farmacia.isnot(None))
                    .distinct().limit(2).all())
        ids = [r[0] for r in rows]
        if len(ids) == 1:
            return int(ids[0])
    except Exception:
        pass
    return None
