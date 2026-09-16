"""Elegir, entre los códigos de barra de un producto, el que el proveedor entiende.

El problema, medido sobre un pedido real a Kellerhoff (15/09/2026): de 139
renglones, **33 volvieron como "REGISTRO ERRONEO" con $0,00** — su sistema no
reconoció el código y esos renglones no se pidieron.

ObServer conoce 32 de esos 33 productos. Lo que pasa es que muchos tienen varios
EAN (packs, presentaciones, códigos de distintos orígenes) y el pedido mandaba
el de menor `orden`, que no es necesariamente el que usa la droguería.

**La fuente de verdad son sus propias facturas.** Si Kellerhoff factura un
producto con un EAN, ese EAN su sistema lo entiende. De los 33 rechazados, 10
tenían un alternativo que aparece en facturas de Kellerhoff — esos diez se
pedían mal por elegir el código equivocado, no por otra cosa.

Los otros 23 no tienen ningún código conocido: ahí no hay nada que elegir, pero
sí conviene avisarlo ANTES de exportar el pedido, para que nadie descubra en el
portal que veinte renglones no entraron.
"""
from __future__ import annotations

from sqlalchemy import text

SQL_EANS_DEL_PROVEEDOR = text("""
    SELECT DISTINCT fi.codigo_barra
      FROM factura_items fi
      JOIN facturas f ON f.id = fi.factura_id
     WHERE f.proveedor_id = :prov
       AND fi.codigo_barra = ANY(:eans)
""")


def eans_que_factura(session, proveedor_id, eans):
    """De `eans`, cuáles aparecen en alguna factura de ese proveedor."""
    eans = [e for e in eans if e]
    if not eans or not proveedor_id:
        return set()
    return {r[0] for r in session.execute(
        SQL_EANS_DEL_PROVEEDOR, {'prov': proveedor_id, 'eans': list(eans)})}


def elegir_ean(candidatos, conocidos_por_el_proveedor):
    """El primero que el proveedor factura; si no conoce ninguno, el primero.

    `candidatos` viene ordenado por `orden` de ObServer, que es el criterio
    anterior: se respeta como desempate para no cambiar nada donde no hay
    información nueva.
    """
    for ean in candidatos:
        if ean in conocidos_por_el_proveedor:
            return ean
    return candidatos[0] if candidatos else None


def mapear_eans(session, proveedor_id, eans_por_producto):
    """{producto: [eans...]} → ({producto: ean elegido}, [productos sin código conocido]).

    La segunda lista es para avisar: son los renglones que la droguería va a
    rechazar, y es mejor saberlo antes de mandar el pedido que después.
    """
    todos = {e for lista in eans_por_producto.values() for e in lista if e}
    conocidos = eans_que_factura(session, proveedor_id, todos)

    elegidos, sin_conocido = {}, []
    for producto, candidatos in eans_por_producto.items():
        ean = elegir_ean(candidatos, conocidos)
        if ean:
            elegidos[producto] = ean
        if not any(c in conocidos for c in candidatos):
            sin_conocido.append(producto)
    return elegidos, sin_conocido
