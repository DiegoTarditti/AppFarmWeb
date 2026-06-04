"""Acceso a la data de la farmacia para el bot (compartido por las acciones y
por la IA). Reusa `product_analytics` (descripción + stock + precio_pvp).
"""
import sqlalchemy as sa

import database


def buscar_productos(query, limite=8):
    """Busca productos por descripción (match de todas las palabras, AND).
    Devuelve [{producto, precio, stock}] ordenado por stock desc."""
    palabras = [p for p in (query or '').split() if p][:6]
    if not palabras:
        return []
    conds = ' AND '.join([f"descripcion ILIKE :p{i}" for i in range(len(palabras))])
    params = {f'p{i}': f'%{p}%' for i, p in enumerate(palabras)}
    params['lim'] = limite
    sql = sa.text(
        f"SELECT descripcion, stock, precio_pvp FROM product_analytics "
        f"WHERE {conds} ORDER BY stock DESC, descripcion LIMIT :lim"
    )
    with database.SessionLocal() as s:
        rows = s.execute(sql, params).fetchall()
    return [{'producto': r.descripcion,
             'precio': float(r.precio_pvp) if r.precio_pvp else None,
             'stock': int(r.stock or 0)} for r in rows]
