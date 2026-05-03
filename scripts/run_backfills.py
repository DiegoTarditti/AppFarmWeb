"""Backfills opcionales de base de datos.

Correr una sola vez después del primer deploy, o cuando se agregue una tabla
nueva que necesite datos históricos.

Uso:
    python scripts/run_backfills.py
    python scripts/run_backfills.py --dry-run   # muestra qué haría sin ejecutar

Idempotente: cada backfill chequea si la tabla ya tiene datos y se saltea si sí.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault('SECRET_KEY', 'run-backfills-script-key-not-used-for-http')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true',
                        help='Mostrar qué haría sin ejecutar nada')
    args = parser.parse_args()

    from database import _ejecutar_backfills_async, init_db

    db_url = os.environ.get('DATABASE_URL', 'sqlite:///farmacia.db')
    if db_url.startswith('sqlite'):
        print('SQLite detectado — backfills no aplican. Saliendo.')
        return

    print(f'Conectando a: {db_url[:40]}...')
    init_db(db_url)

    if args.dry_run:
        print('--dry-run activo. Backfills que correrían:')
        print('  · producto_codigos_barra  (si tabla vacía)')
        print('  · producto_precios_hist   (si tabla vacía)')
        return

    print('Ejecutando backfills...')
    _ejecutar_backfills_async()
    print('Listo.')


if __name__ == '__main__':
    main()
