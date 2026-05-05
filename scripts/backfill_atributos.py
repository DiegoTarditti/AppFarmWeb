"""Backfill de atributos estructurados (`producto_atributos`).

Procesa todos los productos extrayendo droga/concentración/forma/cantidad
desde `obs_productos` (cuando hay) o regex sobre la descripción.

Uso:
    python -m scripts.backfill_atributos             # ejecuta
    python -m scripts.backfill_atributos --dry-run   # solo cuenta

Idempotente: respeta los registros existentes, recalcula solo los que
cambiaron o nunca se procesaron.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault('SECRET_KEY', 'backfill-atributos')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true',
                        help='No commit — solo procesa y descarta.')
    args = parser.parse_args()

    from database import init_db
    db_url = os.environ.get('DATABASE_URL', 'sqlite:///farmacia.db')
    print(f'Conectando: {db_url[:50]}...')
    init_db(db_url)

    print('Backfill de producto_atributos...')
    if args.dry_run:
        print('(DRY-RUN: no commitea)')

    from catalogacion import backfill_todos
    import database as _db

    if args.dry_run:
        # Abrir sesión propia y rollback al final.
        with _db.get_db() as s:
            try:
                n_total, n_act, n_sin = backfill_todos(session=s, log=print)
                s.rollback()
            except Exception:
                s.rollback()
                raise
    else:
        n_total, n_act, n_sin = backfill_todos(log=print)

    print()
    print(f'  Productos totales:    {n_total:>8}')
    print(f'  Actualizados:         {n_act:>8}')
    print(f'  Sin datos:            {n_sin:>8}')


if __name__ == '__main__':
    main()
