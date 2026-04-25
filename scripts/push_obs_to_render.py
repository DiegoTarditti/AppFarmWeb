"""Sincroniza las tablas obs_* de Postgres local → Postgres de Render.

Estrategia:
1. Descarga cada tabla obs_* del local con COPY TO stdout (streaming, sin cargar todo a memoria).
2. TRUNCATE la misma tabla en Render.
3. Re-sube con COPY FROM stdin.
4. Además propaga productos.observer_id a Render, matcheando por codigo_barra
   (el id numérico NO coincide entre ambas DBs).

Config:
    DATABASE_URL         Postgres local (del .env ya existente)
    RENDER_DATABASE_URL  Postgres de Render (External Database URL)

Uso standalone:
    RENDER_DATABASE_URL='postgresql://...' python scripts/push_obs_to_render.py
"""
import io
import os
import sys
import time

import psycopg2
import psycopg2.extras

# Orden que respeta FKs: padres antes que hijos
TABLAS = [
    'obs_laboratorios',
    'obs_rubros',
    'obs_subrubros',
    'obs_nombres_drogas',
    'obs_productos',
    'obs_stock',
    'obs_ventas_mensuales',
    # Clientes / Obras Sociales (FKs: convenios→OS, planes→convenios, clientes→grupos+categorias)
    'obs_grupos_clientes',
    'obs_categorias_clientes',
    'obs_obras_sociales',
    'obs_convenios',
    'obs_planes',
    'obs_clientes',
]


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

    resultados = {}
    t_total = time.time()

    with psycopg2.connect(local_url) as local, psycopg2.connect(render_url) as remote:
        with local.cursor() as lc, remote.cursor() as rc:

            # 1. TRUNCATE en Render en orden inverso (primero hijos, después padres)
            log('Limpiando Render…')
            for t in reversed(TABLAS):
                rc.execute(f'TRUNCATE TABLE {t} CASCADE')

            # 2. Streaming COPY por cada tabla
            for t in TABLAS:
                t0 = time.time()
                buf = io.StringIO()
                lc.copy_expert(f'COPY {t} TO STDOUT', buf)
                buf.seek(0)
                rc.copy_expert(f'COPY {t} FROM STDIN', buf)
                # Contar filas copiadas
                rc.execute(f'SELECT COUNT(*) FROM {t}')
                n = rc.fetchone()[0]
                ms = int((time.time() - t0) * 1000)
                resultados[t] = {'filas': n, 'ms': ms}
                log(f'  {t}: {n:,} filas en {ms} ms')

            # 3. Propagar productos.observer_id por codigo_barra
            t0 = time.time()
            lc.execute("""
                SELECT codigo_barra, observer_id
                FROM productos
                WHERE observer_id IS NOT NULL AND codigo_barra IS NOT NULL
            """)
            pares = lc.fetchall()
            if pares:
                rc.execute("""
                    CREATE TEMP TABLE _bridge (
                        codigo_barra VARCHAR(20) PRIMARY KEY,
                        observer_id INTEGER NOT NULL
                    ) ON COMMIT DROP
                """)
                psycopg2.extras.execute_values(
                    rc,
                    'INSERT INTO _bridge (codigo_barra, observer_id) VALUES %s',
                    pares, page_size=1000,
                )
                rc.execute("""
                    UPDATE productos p
                    SET observer_id = b.observer_id
                    FROM _bridge b
                    WHERE p.codigo_barra = b.codigo_barra
                      AND (p.observer_id IS NULL OR p.observer_id <> b.observer_id)
                """)
                n = rc.rowcount
                ms = int((time.time() - t0) * 1000)
                resultados['productos.observer_id'] = {'filas': n, 'ms': ms}
                log(f'  productos.observer_id: {n:,} actualizados en {ms} ms')

        remote.commit()
        local.commit()

    # Refrescar vistas materializadas en Render (datos ya están al día tras el push).
    _refresh_matviews_render(render_url, log)

    resultados['TOTAL_MS'] = int((time.time() - t_total) * 1000)
    return resultados


def _refresh_matviews_render(remote_url, log):
    """Refresca las vistas materializadas en Render después del push.

    Idempotente y best-effort: si una falla no bloquea el push entero.
    Logea en mv_refresh_log de Render para que el banner de la app web sepa
    cuándo fue el último refresh.
    """
    matviews = ['mv_stats_drogas']
    try:
        with psycopg2.connect(remote_url) as remote:
            with remote.cursor() as rc:
                for view in matviews:
                    t0 = time.time()
                    error = None
                    filas = None
                    try:
                        # Primero intentar CONCURRENTLY (no bloquea reads).
                        try:
                            rc.execute(f'REFRESH MATERIALIZED VIEW CONCURRENTLY {view}')
                        except Exception:
                            # Vista vacía / sin populate → fallback a refresh bloqueante.
                            remote.rollback()
                            rc.execute(f'REFRESH MATERIALIZED VIEW {view}')
                        rc.execute(f'SELECT COUNT(*) FROM {view}')
                        filas = rc.fetchone()[0]
                        log(f'  {view}: refrescada con {filas:,} filas')
                    except Exception as e:
                        error = str(e)[:500]
                        log(f'  {view}: ERROR — {error}')
                    duracion_ms = int((time.time() - t0) * 1000)
                    rc.execute("""
                        INSERT INTO mv_refresh_log (view_name, refrescada_en, duracion_ms, filas, error)
                        VALUES (%s, NOW(), %s, %s, %s)
                    """, (view, duracion_ms, filas, error))
                remote.commit()
    except Exception as e:
        # Si falla todo el bloque (ej. tabla mv_refresh_log no existe todavía),
        # no romper el push. El banner de la app va a mostrar "calculado en vivo".
        log(f'  refresh matviews: ERROR — {str(e)[:200]}')


if __name__ == '__main__':
    try:
        res = push()
        print(f"\nTotal: {res['TOTAL_MS']} ms")
        sys.exit(0)
    except Exception as e:
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(1)
