"""Corre las migraciones inline (init_db) UNA vez.

Usado como `preDeployCommand` en Render: se ejecuta después del build y ANTES
de que la versión nueva reciba tráfico, en la imagen nueva. Así el schema queda
al día antes de que arranquen los workers → no más 500 por columna inexistente
(ej. el caso `usa_packs`).

init_db() es idempotente (ALTER/CREATE ... IF NOT EXISTS) y solo hace DDL de
schema; los backfills pesados están gateados aparte (RUN_BACKFILLS=1, async),
así que esto corre en segundos.

Uso manual equivalente:
    python scripts/migrate.py
"""
import os
import sys

# Asegura que /app (raíz del proyecto) esté en el path, sin importar desde dónde
# se invoque el script (Render corre `python scripts/migrate.py` desde /app).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import init_db


def main():
    url = os.environ.get('DATABASE_URL')
    if not url:
        print('[migrate] ERROR: falta DATABASE_URL', file=sys.stderr)
        sys.exit(1)
    print('[migrate] init_db start…')
    init_db(url)
    print('[migrate] init_db OK — schema al día.')


if __name__ == '__main__':
    main()
