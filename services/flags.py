"""Source of truth de la presentación de flags (comportamientos excepcionales).

`construir_flag_dict` y `flags_display_por_producto` centralizan el armado del
dict de display {slug, nombre, icono, color_clases, nota, ean_reemplazo,
efecto_armado} que antes estaba duplicado en routes/productos.py y compras_dia.py.

Distinto de `obtener_flags_bulk` (services/pedido_estacional.py), que devuelve
los objetos crudos para el cálculo. Este módulo es la capa de PRESENTACIÓN.
"""
import json

from database import ProductoFlag, TipoPedidoConfig

# Clases Tailwind por color de flag (config_json.color de TipoPedidoConfig).
FLAG_COLOR_CLASES = {
    'red':    'bg-red-100 text-red-800 border-red-300',
    'violet': 'bg-violet-100 text-violet-800 border-violet-300',
    'amber':  'bg-amber-100 text-amber-800 border-amber-300',
    'sky':    'bg-sky-100 text-sky-800 border-sky-300',
    'gray':   'bg-gray-100 text-gray-700 border-gray-300',
}


def construir_flag_dict(flag, cfg):
    """Display dict de un ProductoFlag + su TipoPedidoConfig (cfg puede ser None).

    Returns dict {slug, nombre, icono, color_clases, nota, ean_reemplazo,
    efecto_armado}. `efecto_armado` lo usa el armado para efectos sobre la
    cantidad (ej. 'tope_uno' → a_pedir máximo 1).
    """
    cfg_d = {}
    if cfg and cfg.config_json:
        try:
            cfg_d = json.loads(cfg.config_json)
        except (ValueError, TypeError):
            cfg_d = {}
    color = cfg_d.get('color', 'sky')
    return {
        'slug': flag.flag_slug,
        'nombre': cfg.nombre if cfg else flag.flag_slug,
        'icono': cfg_d.get('icono', '🚩'),
        'color_clases': FLAG_COLOR_CLASES.get(color, FLAG_COLOR_CLASES['sky']),
        'nota': flag.nota or '',
        'ean_reemplazo': flag.ean_reemplazo or '',
        'efecto_armado': cfg_d.get('efecto_armado', 'ninguno'),
    }


def flags_display_por_producto(session, eans_por_producto):
    """Resuelve el flag de display de cada producto a partir de sus EANs.

    Args:
        session: SQLAlchemy session abierta.
        eans_por_producto: dict {key: iterable[ean]}. `key` es lo que use el
            caller (observer_id, pid, etc.). Los EANs pueden ser principal + alts.

    Returns:
        dict {key: flag_dict | None} — el flag del PRIMER EAN del producto que
        tenga uno asignado. 1-2 queries totales (no N+1).
    """
    todos = {e for eans in eans_por_producto.values() for e in (eans or []) if e}
    if not todos:
        return {k: None for k in eans_por_producto}

    pf_rows = (session.query(ProductoFlag)
               .filter(ProductoFlag.ean.in_(list(todos))).all())
    flag_por_ean = {f.ean: f for f in pf_rows}
    cfg_por_slug = {}
    slugs = list({f.flag_slug for f in pf_rows})
    if slugs:
        cfg_por_slug = {c.slug: c for c in (
            session.query(TipoPedidoConfig)
            .filter(TipoPedidoConfig.slug.in_(slugs),
                    TipoPedidoConfig.categoria == 'flag').all())}

    out = {}
    for key, eans in eans_por_producto.items():
        out[key] = None
        for ean in (eans or []):
            f = flag_por_ean.get(ean)
            if f:
                out[key] = construir_flag_dict(f, cfg_por_slug.get(f.flag_slug))
                break
    return out
