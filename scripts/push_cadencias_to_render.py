"""Copia la tabla `cadencia_lab_snapshot` de Postgres local → Render.

El snapshot se computa LOCAL (rápido, con datos frescos de ObServer) y después
se copia a Render con COPY (DB-a-DB), igual que push_obs_to_render. La pantalla
/informes/cadencias-resumen en Render solo LEE esta tabla.

Requiere que la tabla exista en Render con el mismo esquema (lo crea init_db al
deployar). Config:
    DATABASE_URL         Postgres local
    RENDER_DATABASE_URL  Postgres de Render (External Database URL)

Uso standalone:
    RENDER_DATABASE_URL='postgresql://...' python scripts/push_cadencias_to_render.py
"""
import io
import os
import sys
import time

import psycopg2

TABLA = 'cadencia_lab_snapshot'


def _normalize_url(url):
    if url and url.startswith('postgres://'):
        return url.replace('postgres://', 'postgresql://', 1)
    return url


def push(local_url=None, render_url=None, log=print):
    local_url = _normalize_url(local_url or os.environ.get('DATABASE_URL'))
    render_url = _normalize_url(render_url or os.environ.get('RENDER_DATABASE_URL'))
    if not local_url:
        raise RuntimeError('Falta DATABASE_URL (Postgres local)')
    if not render_url:
        raise RuntimeError('Falta RENDER_DATABASE_URL (externa de Render)')

    t0 = time.time()
    with psycopg2.connect(local_url) as local, psycopg2.connect(render_url) as remote:
        with local.cursor() as lc, remote.cursor() as rc:
            rc.execute(f'TRUNCATE TABLE {TABLA}')
            buf = io.StringIO()
            lc.copy_expert(f'COPY {TABLA} TO STDOUT', buf)
            buf.seek(0)
            rc.copy_expert(f'COPY {TABLA} FROM STDIN', buf)
            rc.execute(f'SELECT COUNT(*) FROM {TABLA}')
            n = rc.fetchone()[0]
        remote.commit()
    ms = int((time.time() - t0) * 1000)
    log(f'  {TABLA}: {n:,} filas en {ms} ms')
    return {TABLA: {'filas': n, 'ms': ms}, 'TOTAL_MS': ms}


if __name__ == '__main__':
    try:
        res = push()
        print(f"\nTotal: {res['TOTAL_MS']} ms")
        sys.exit(0)
    except Exception as e:
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(1)
