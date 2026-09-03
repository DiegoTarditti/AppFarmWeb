"""Backfill `pack_equivalencias.cantidad` leyendo el factor de `desc_pack`.

Las filas de `pack_equivalencias` que entraron por importación de Excel quedaron
todas con `cantidad = 1`: el Excel no traía esa columna y el import usa 1 como
default (`routes/laboratorios.py`, `cant = ... if _cell('cantidad') else 1`).
Medido el 2026-09-03 en producción: las 10 filas de la tabla tienen `cantidad=1`
y `ean_unidad` vacío, así que la equivalencia no sirve para convertir nada —
`compare_invoice_vs_erp` la consulta filtrando `cantidad > 1` y no encuentra
ninguna.

Pero el factor SÍ está, en texto, dentro de `desc_pack`: "OPTAMOX DUO 1G COMP
REC X 8 PACK X 10", "SERTAL COMP X 200 (PACK X 20 ) VL", "TAURAL F 10 COMP.
PACK X 100". Este script lo extrae con los mismos patrones que ya usa el
detector de packs de módulos (`pack_detector.PACK_PATTERNS`) y lo persiste.

Por qué acá y no en el cruce: aplicar la regex en cada comparación sería una
inferencia silenciosa sobre texto libre de un proveedor. Persistida es un dato
que una persona ve en /laboratorio/<id>/pack-equivalencias, corrige de un click
si está mal, y queda igual para todos los consumidores (cruce, módulos,
pedidos). Misma regex, otra categoría de decisión.

Solo toca filas con `cantidad <= 1` (no pisa nada cargado a mano) y solo cuando
la descripción declara el factor explícitamente.

Uso:
    python -m scripts.backfill_pack_equivalencias_cantidad --dry-run   # default
    python -m scripts.backfill_pack_equivalencias_cantidad --escribir
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault('SECRET_KEY', 'backfill-pack-equiv-not-used-for-http')


def ejecutar(dry_run: bool = True) -> dict:
    """Completa `cantidad` desde `desc_pack`. Devuelve stats + el detalle.

    Args:
        dry_run: si es True (default) no escribe, solo informa qué haría.

    Returns:
        dict con `actualizadas`, `sin_factor`, `ya_tenian`, `total` y `detalle`
        (lista de (ean_pack, desc_pack, cantidad_nueva)).
    """
    import database
    from database import PackEquivalencia
    from pack_detector import _match_pack

    database.init_engine()
    session = database.SessionLocal()
    try:
        filas = session.query(PackEquivalencia).all()
        stats = {'actualizadas': 0, 'sin_factor': 0, 'ya_tenian': 0,
                 'total': len(filas), 'detalle': []}
        for pe in filas:
            if (pe.cantidad or 0) > 1:
                stats['ya_tenian'] += 1
                continue
            _m, n = _match_pack(pe.desc_pack or '')
            if not n or n <= 1:
                stats['sin_factor'] += 1
                continue
            stats['actualizadas'] += 1
            stats['detalle'].append((pe.ean_pack, pe.desc_pack, n))
            if not dry_run:
                pe.cantidad = n
        if not dry_run:
            session.commit()
        return stats
    finally:
        session.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--escribir', action='store_true',
                    help='aplica los cambios (sin esto solo informa)')
    args = ap.parse_args()

    stats = ejecutar(dry_run=not args.escribir)
    modo = 'APLICADO' if args.escribir else 'DRY-RUN (no se escribió nada)'
    print(f'== {modo} ==')
    print(f'  filas totales:            {stats["total"]}')
    print(f'  ya tenían cantidad > 1:   {stats["ya_tenian"]}')
    print(f'  sin factor en desc_pack:  {stats["sin_factor"]}')
    print(f'  a completar:              {stats["actualizadas"]}')
    for ean, desc, n in stats['detalle']:
        print(f'    {ean}  x{n:<4} {desc}')


if __name__ == '__main__':
    main()
