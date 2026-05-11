"""Debug: por qué (I) DEXALERGIN comp.rec.x 20 no matchea con el local.

Lista TODOS los productos DEXALERGIN en local + Observer y muestra el score
de match contra la descripción del proveedor.

Usage:
    python scripts/debug_match_dexalergin.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
from producto_matcher import (  # noqa: E402
    jaccard,
    normalizar_texto,
    tokens_significativos,
)

DESC_PROVEEDOR = '(I) DEXALERGIN comp.rec.x 20'

with database.get_db() as session:
    print(f'Buscando: "{DESC_PROVEEDOR}"')
    print(f'  → normalizado: "{normalizar_texto(DESC_PROVEEDOR)}"')
    toks_in = tokens_significativos(DESC_PROVEEDOR)
    print(f'  → tokens significativos: {toks_in}')
    print()

    # Productos locales que contienen "DEXALERGIN"
    locales = (session.query(database.Producto)
               .filter(database.Producto.descripcion.ilike('%DEXALERGIN%'))
               .order_by(database.Producto.descripcion)
               .all())
    print(f'=== {len(locales)} productos locales con DEXALERGIN ===')
    for p in locales:
        toks_c = tokens_significativos(p.descripcion)
        score = jaccard(toks_in, toks_c)
        print(f'  [{score:.3f}] {p.descripcion}')
        print(f'         tokens: {toks_c}')
        print(f'         EAN: {p.codigo_barra}, alfa: {p.codigo_alfabeta}, lab_id: {p.laboratorio_id}')
    print()

    # Observer
    obs = (session.query(database.ObsProducto)
           .filter(database.ObsProducto.descripcion.ilike('%DEXALERGIN%'))
           .filter(database.ObsProducto.fecha_baja.is_(None))
           .order_by(database.ObsProducto.descripcion)
           .all())
    print(f'=== {len(obs)} productos Observer activos con DEXALERGIN ===')
    for o in obs:
        toks_c = tokens_significativos(o.descripcion)
        score = jaccard(toks_in, toks_c)
        print(f'  [{score:.3f}] {o.descripcion}')
        print(f'         tokens: {toks_c}')
        print(f'         observer_id: {o.observer_id}, alfa: {o.codigo_alfabeta}, lab_obs: {o.laboratorio_observer}')
