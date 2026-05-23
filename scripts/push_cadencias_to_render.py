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
# Lista EXPLÍCITA de columnas: el orden físico difiere entre local y Render
# (local agregó los *_monto al final vía ALTER; Render los creó en el orden del
# modelo). Sin lista explícita, COPY desalinea las columnas.
COLS = (
    'lab_id, lab_nombre, core, ocasional, caida, dormido, '
    'alta, media_alta, media, baja, muy_baja, '
    'core_monto, ocasional_monto, caida_monto, dormido_monto, '
    'alta_monto, media_alta_monto, media_monto, baja_monto, muy_baja_monto, '
    'con_ventas, sin_ventas, monto_mensual, dormido_valor, '
    'dormido_con_stock, dormido_stock_u, cobertura, meses_rot, actualizado_en'
)


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
            lc.copy_expert(f'COPY {TABLA} ({COLS}) TO STDOUT', buf)
            buf.seek(0)
            rc.copy_expert(f'COPY {TABLA} ({COLS}) FROM STDIN', buf)
            rc.execute(f'SELECT COUNT(*) FROM {TABLA}')
            n = rc.fetchone()[0]
        remote.commit()
    ms = int((time.time() - t0) * 1000)
    log(f'  {TABLA}: {n:,} filas en {ms} ms')
    return {TABLA: {'filas': n, 'ms': ms}, 'TOTAL_MS': ms}


def generar_y_pushear(render_url=None, cobertura=30, meses_rot=3, log=print):
    """Computa el snapshot LOCAL (todos los labs) y lo copia a Render. Pensado
    para correr dentro del container (`python -m scripts.push_cadencias_to_render`),
    disparado por el comando encolado del panel remoto. No requiere login/token."""
    import database
    from helpers import recalcular_snapshot_cadencias
    database.init_engine()  # setea engine + SessionLocal sin correr migraciones
    t0 = time.time()
    with database.get_db() as session:
        n_local = recalcular_snapshot_cadencias(session, cobertura, meses_rot)
    log(f'  snapshot local: {n_local} labs en {int((time.time()-t0)*1000)} ms')
    res = push(render_url=render_url, log=log)
    res['labs_local'] = n_local
    return res


if __name__ == '__main__':
    cob = int(os.environ.get('CAD_COBERTURA', '30'))
    rot = int(os.environ.get('CAD_MESES_ROT', '3'))
    try:
        res = generar_y_pushear(cobertura=cob, meses_rot=rot)
        print(f"\nOK: {res['labs_local']} labs computados, "
              f"{res[TABLA]['filas']} subidos a Render ({res['TOTAL_MS']} ms)")
        sys.exit(0)
    except Exception as e:
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(1)
