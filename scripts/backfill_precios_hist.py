"""Rellena `producto_precios_hist` desde los renglones de factura ya cargados.

Por qué hace falta: el snapshot sólo lo escribían las importaciones de archivo
de `routes/converter.py`, y en producción no hay ninguna factura de ese origen
(todas son `kh_portal` o `arca`). Resultado: la tabla quedó en **0 filas**, y sus
dos lectores —el gráfico de precios de la ficha de producto y
`services/pedido_estacional.py`, que usa `precio_publico` para estimar el PVP—
vienen trabajando sin datos.

Qué se puede recuperar y qué no: de `factura_items` salen código de barras,
fecha, proveedor, descuento, precio unitario, importe y tipo de comprobante.
**`precio_publico` NO**: `InvoiceItem` nunca tuvo dónde guardarlo, y por eso se
perdió. Se puede *derivar* como `precio_unitario / (1 - dto/100)` —la relación se
verificó exacta contra una factura real: 313.089,19 / (1 - 0,3341) = 470.174—
pero eso es un valor calculado, no uno que la droguería haya informado. Va sólo
si se pide con `--derivar-pvp`, y sabiendo que para `dto` nulo o 0 daría
simplemente el precio unitario, que sería falso.

Idempotente: saltea los renglones que ya tienen su fila (mismo factura_id +
código de barras). La tabla es append-only y no tiene clave única, así que la
guarda está acá.

    python scripts/backfill_precios_hist.py --dry-run
    python scripts/backfill_precios_hist.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
from services import precios_hist  # noqa: E402

LOTE = 2000


def _ya_tienen(session):
    """{(factura_id, codigo_barra)} de lo ya registrado."""
    return set(session.query(database.ProductoPrecioHist.factura_id,
                             database.ProductoPrecioHist.codigo_barra).all())


def _pvp_derivado(precio_unitario, dto):
    """`precio_unitario / (1 - dto/100)`. None si el descuento no lo permite."""
    if precio_unitario is None or dto is None:
        return None
    d = float(dto)
    if d <= 0 or d >= 100:          # sin descuento no hay nada que deshacer
        return None
    return round(float(precio_unitario) / (1 - d / 100), 2)


def backfill(session, derivar_pvp=False, dry_run=False):
    ya = _ya_tienen(session)
    q = (session.query(database.InvoiceItem, database.Invoice)
         .join(database.Invoice, database.Invoice.id == database.InvoiceItem.factura_id)
         .filter(database.InvoiceItem.codigo_barra.isnot(None),
                 database.InvoiceItem.codigo_barra != '',
                 database.Invoice.fecha.isnot(None)))

    escritos = saltados = sin_ean = 0
    for i, (item, inv) in enumerate(q.yield_per(LOTE), 1):
        cb = (item.codigo_barra or '').strip()
        if not cb:
            sin_ean += 1
            continue
        if (inv.id, cb[:20]) in ya:
            saltados += 1
            continue
        if not dry_run:
            precios_hist.registrar(
                session, inv, codigo_barra=cb,
                precio_publico=(_pvp_derivado(item.precio_unitario, item.dto)
                                if derivar_pvp else None),
                dto_pct=item.dto,
                precio_unitario=item.precio_unitario,
                importe=item.importe)
        escritos += 1
        if not dry_run and i % LOTE == 0:
            session.commit()
    if not dry_run:
        session.commit()
    return {'escritos': escritos, 'saltados': saltados, 'sin_ean': sin_ean}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dry-run', action='store_true', help='no escribe, sólo cuenta')
    ap.add_argument('--derivar-pvp', action='store_true',
                    help='calcula precio_publico desde precio_unitario y dto '
                         '(valor derivado, no informado por la droguería)')
    args = ap.parse_args()

    database.init_db()
    with database.get_db() as session:
        r = backfill(session, derivar_pvp=args.derivar_pvp, dry_run=args.dry_run)
    modo = 'SE ESCRIBIRÍAN' if args.dry_run else 'escritos'
    print(f'{modo}: {r["escritos"]} · ya estaban: {r["saltados"]} · sin EAN: {r["sin_ean"]}')
    if args.dry_run:
        print('(dry-run: no se escribió nada)')


if __name__ == '__main__':
    main()
