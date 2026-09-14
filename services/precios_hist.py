"""Snapshot de precio por renglón de factura (`producto_precios_hist`).

Por qué existe este módulo y no se escribe la fila a mano en cada lugar: los
caminos que dan de alta renglones de factura son **ocho**, y hasta ahora sólo
tres escribían el histórico — `data_extract.py` y los dos de `routes/converter.py`
(las importaciones de archivo/PDF). Los otros cinco no, así que una compra
cargada por el portal de Kellerhoff o por la carga manual no dejaba rastro de
precio, y lo que se perdía era justo lo que nadie puede reconstruir después: el
**precio público** de esa fecha, que la droguería informa en el momento y no
vuelve a publicar.

Quién lee esto: la ficha de precios del producto (`routes/productos.py`) y
`services/pedido_estacional.py`, que usa `precio_publico` para estimar el PVP.

La tabla es **append-only**: no hay upsert ni clave única. La idempotencia la
pone el llamador, que en todos los casos escribe los renglones sólo si la
factura todavía no tiene (`if not inv.items`). Si algún camino nuevo no tiene esa
guarda, va a duplicar.
"""
from __future__ import annotations

import database


def registrar(session, inv, codigo_barra, precio_publico=None, dto_pct=None,
              precio_unitario=None, importe=None):
    """Agrega el snapshot de un renglón. Devuelve si lo escribió.

    Los datos del proveedor y la fecha salen de la factura, no del llamador:
    son los mismos para todos sus renglones y así no hay forma de que un camino
    los mande distinto. `proveedor_id` es el FK que se agregó en el PR #409 —
    antes de eso sólo quedaba el nombre en texto y el cruce era por CUIT.

    Sin código de barras o sin fecha la fila no sirve para nada (no se puede
    buscar ni ubicar en el tiempo), así que se descarta en silencio.
    """
    cb = (codigo_barra or '').strip()
    if not cb or not inv.fecha:
        return False
    session.add(database.ProductoPrecioHist(
        codigo_barra=cb[:20],
        proveedor_id=inv.proveedor_id,
        proveedor_razon=inv.proveedor_razon,
        fecha=inv.fecha,
        precio_publico=precio_publico,
        dto_pct=dto_pct,
        precio_unitario=precio_unitario,
        importe=importe,
        factura_id=inv.id,
        tipo_comprobante=inv.tipo_comprobante,
    ))
    return True
