"""Backfill alt1/2/3 → producto_codigos_barra (Fase 1.2 migración EANs).

Para cada producto:
  - Inserta su `codigo_barra` principal en `producto_codigos_barra` con
    `es_principal=True`, `fuente='legacy_principal'`.
  - Inserta cada `codigo_barra_alt1/2/3` no vacío con `es_principal=False`,
    `fuente='legacy_alt'`.

Idempotente: usa UNIQUE(producto_id, codigo_barra) para evitar duplicados.
Si una fila ya existe (por ejemplo del upsert vía `_add_alt_barcode`), la skipea.

Uso:
    python -m scripts.backfill_codigos_barra              # ejecuta
    python -m scripts.backfill_codigos_barra --dry-run    # cuenta sin escribir

También se invoca desde el endpoint admin POST /api/admin/migrar/backfill-codigos-barra.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault('SECRET_KEY', 'backfill-codigos-barra-not-used-for-http')


def ejecutar(dry_run: bool = False) -> dict:
    """Corre el backfill. Devuelve stats: insertados / saltados / total productos.

    Args:
        dry_run: si True, no escribe — solo cuenta.

    Returns:
        dict con keys: 'productos_total', 'principales_insertados',
        'alts_insertados', 'saltados_existentes', 'productos_sin_alts',
        'dry_run', 'errores'.
    """
    from sqlalchemy import or_

    from database import Producto, ProductoCodigoBarra, get_db

    stats = {
        'productos_total': 0,
        'principales_insertados': 0,
        'alts_insertados': 0,
        'saltados_existentes': 0,
        'productos_sin_alts': 0,
        'dry_run': dry_run,
        'errores': [],
    }

    with get_db() as session:
        # Filtramos productos que tienen al menos UN alt o que probablemente no
        # tengan su `codigo_barra` principal en la 1-a-N todavía. Para ser
        # conservadores, procesamos todos. La query puede agregar filtro en el
        # futuro si se vuelve cara.
        productos = session.query(Producto.id, Producto.codigo_barra,
                                  Producto.codigo_barra_alt1,
                                  Producto.codigo_barra_alt2,
                                  Producto.codigo_barra_alt3).all()
        stats['productos_total'] = len(productos)

        # Pre-cargar todos los (producto_id, codigo_barra) ya en la 1-a-N
        # para evitar 4 queries por producto.
        existentes = set()
        for prod_id, ean in session.query(ProductoCodigoBarra.producto_id,
                                          ProductoCodigoBarra.codigo_barra).all():
            existentes.add((prod_id, ean))

        for p_id, p_cb, alt1, alt2, alt3 in productos:
            if not p_cb:
                continue
            p_cb = str(p_cb).strip()
            if not p_cb:
                continue

            tiene_alts = any(x and str(x).strip() for x in (alt1, alt2, alt3))
            if not tiene_alts:
                stats['productos_sin_alts'] += 1

            # 1) Principal
            if (p_id, p_cb) in existentes:
                stats['saltados_existentes'] += 1
            else:
                if not dry_run:
                    session.add(ProductoCodigoBarra(
                        producto_id=p_id,
                        codigo_barra=p_cb,
                        es_principal=True,
                        fuente='legacy_principal',
                    ))
                stats['principales_insertados'] += 1
                existentes.add((p_id, p_cb))

            # 2) Alts
            for ean in (alt1, alt2, alt3):
                if not ean:
                    continue
                ean = str(ean).strip()
                if not ean or ean == p_cb:
                    continue
                if (p_id, ean) in existentes:
                    stats['saltados_existentes'] += 1
                    continue
                if not dry_run:
                    session.add(ProductoCodigoBarra(
                        producto_id=p_id,
                        codigo_barra=ean,
                        es_principal=False,
                        fuente='legacy_alt',
                    ))
                stats['alts_insertados'] += 1
                existentes.add((p_id, ean))

        if not dry_run:
            try:
                session.commit()
            except Exception as e:
                session.rollback()
                stats['errores'].append(f'commit fallo: {e}')

    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true',
                        help='Mostrar qué insertaría sin escribir.')
    args = parser.parse_args()

    from database import init_db
    db_url = os.environ.get('DATABASE_URL', 'sqlite:///farmacia.db')
    if db_url.startswith('sqlite'):
        print('SQLite detectado. Continuando igual (útil en tests locales).')
    print(f'Conectando: {db_url[:50]}...')
    init_db(db_url)

    print(f'Backfill alt1/2/3 → producto_codigos_barra ({"DRY-RUN" if args.dry_run else "EJECUTANDO"})')
    stats = ejecutar(dry_run=args.dry_run)
    print()
    print(f'  Productos totales:        {stats["productos_total"]:>8}')
    print(f'  Productos sin alts:       {stats["productos_sin_alts"]:>8}')
    print(f'  Principales {"a insertar" if args.dry_run else "insertados "}:    {stats["principales_insertados"]:>8}')
    print(f'  Alts        {"a insertar" if args.dry_run else "insertados "}:    {stats["alts_insertados"]:>8}')
    print(f'  Filas ya existentes (skip): {stats["saltados_existentes"]:>8}')
    if stats['errores']:
        print(f'  Errores: {stats["errores"]}')


if __name__ == '__main__':
    main()
