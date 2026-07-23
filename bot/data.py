"""Acceso a la data de la farmacia para el bot (compartido por las acciones y
por la IA). Busca en obs_productos + obs_stock (stock real de ObServer).
"""
import os

import sqlalchemy as sa

import database

_ID_FARMACIA = int(os.environ.get('OBSERVER_ID_FARMACIA', '10525'))


def buscar_productos(query, limite=8):
    """Busca productos por descripción de marca O por monodroga (AND de palabras).
    Devuelve [{producto, precio, stock}] ordenado por stock desc, con stock >= 0.

    Fuente: obs_productos + obs_stock (stock real ObServer) + product_analytics
    (precio_pvp). Incluye resultados sin stock para que el cliente sepa que
    existe aunque no haya unidades.
    """
    palabras = [p for p in (query or '').split() if p][:6]
    if not palabras:
        return []

    # Condiciones AND sobre descripción del producto O sobre la monodroga
    cond_prod  = ' AND '.join([f"op.descripcion ILIKE :p{i}" for i in range(len(palabras))])
    cond_droga = ' AND '.join([f"nd.descripcion ILIKE :p{i}" for i in range(len(palabras))])

    params = {f'p{i}': f'%{p}%' for i, p in enumerate(palabras)}
    params['fid'] = _ID_FARMACIA
    params['lim'] = limite

    sql = sa.text(f"""
        SELECT op.observer_id, op.descripcion,
               COALESCE(os.stock_actual, 0)            AS stock,
               COALESCE(pa.precio_pvp, p.precio_pvp)  AS precio_pvp
        FROM obs_productos op
        JOIN obs_stock os
          ON os.producto_observer = op.observer_id
         AND os.id_farmacia = :fid
        LEFT JOIN obs_nombres_drogas nd
          ON nd.observer_id = op.nombre_droga_observer
        LEFT JOIN obs_codigos_barras cb
          ON cb.producto_observer = op.observer_id AND cb.fecha_baja IS NULL
        LEFT JOIN product_analytics pa
          ON pa.codigo_barra = cb.codigo_barras
        LEFT JOIN productos p
          ON p.observer_id = op.observer_id
        WHERE op.fecha_baja IS NULL
          AND ({cond_prod} OR {cond_droga})
        ORDER BY os.stock_actual DESC, op.descripcion
        LIMIT :lim
    """)

    with database.SessionLocal() as s:
        rows = s.execute(sql, params).fetchall()
    return [{'observer_id': r.observer_id,
             'producto': r.descripcion,
             'precio': float(r.precio_pvp) if r.precio_pvp else None,
             'stock': int(r.stock or 0)} for r in rows]
