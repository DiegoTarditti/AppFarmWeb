"""Sincroniza las tablas obs_* de Postgres local → Postgres de Render.

Estrategia:
1. Descarga cada tabla obs_* del local con COPY TO stdout (streaming, sin cargar todo a memoria).
2. TRUNCATE la misma tabla en Render.
3. Re-sube con COPY FROM stdin.
4. Además propaga productos.observer_id a Render, matcheando por codigo_barra
   (el id numérico NO coincide entre ambas DBs).

⚠ IMPORTANTE — enumeración de columnas obligatoria:
El COPY se hace SIEMPRE con lista explícita de columnas: la intersección de las
que existen en LOCAL y en RENDER. Sin esto, si los schemas divergen en orden
(las ALTER TABLE quedan al final en cada DB, y no siempre en el mismo orden),
COPY corre los valores y podés terminar metiendo un varchar "L" en un integer
(bug real detectado 2026-07-21 con id_tipo_venta_control cayendo en troquel).

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
    # Médicos (FKs: matriculas→medicos+colegios)
    'obs_colegios_medicos',
    'obs_medicos',
    'obs_medicos_matriculas',
    # Detalle de ventas (FKs: producto, cliente, OS, plan)
    'obs_ventas_detalle',
    # Códigos de barra (FK: producto)
    'obs_codigos_barras',
]


def _normalize_url(url):
    if url and url.startswith('postgres://'):
        return url.replace('postgres://', 'postgresql://', 1)
    return url


def _cols_de(cur, tabla: str) -> list[str]:
    """Devuelve la lista de columnas de una tabla, en su orden físico actual."""
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = %s ORDER BY ordinal_position",
        (tabla,),
    )
    return [r[0] for r in cur.fetchall()]


def _cols_comunes(lc, rc, tabla: str) -> list[str]:
    """Intersección de columnas local ∩ Render. Ordena por el orden del destino
    para que el COPY FROM sea determinista. Warns si hay columnas exclusivas.
    """
    local_cols = _cols_de(lc, tabla)
    render_cols = _cols_de(rc, tabla)
    l_set, r_set = set(local_cols), set(render_cols)
    comunes = [c for c in render_cols if c in l_set]
    solo_local = [c for c in local_cols if c not in r_set]
    solo_render = [c for c in render_cols if c not in l_set]
    if solo_local:
        print(f'    [warn] {tabla}: columnas solo en LOCAL (no se pushean): {solo_local}')
    if solo_render:
        print(f'    [warn] {tabla}: columnas solo en RENDER (quedan NULL/default): {solo_render}')
    return comunes


def push(local_url=None, render_url=None, log=print, tablas=None):
    local_url = _normalize_url(local_url or os.environ.get('DATABASE_URL'))
    render_url = _normalize_url(render_url or os.environ.get('RENDER_DATABASE_URL'))
    if not local_url:
        raise RuntimeError('Falta DATABASE_URL (Postgres local)')
    if not render_url:
        raise RuntimeError('Falta RENDER_DATABASE_URL (externa de Render)')

    # Push parcial (tablas=[...]): solo seguro para tablas HOJA (sin hijos FK).
    # Si el subset incluye una tabla padre (productos/refs), su TRUNCATE CASCADE
    # borraria hijos que no re-copiamos (ventas_detalle) -> hacemos push completo.
    LEAF_SEGURAS = {'obs_stock', 'obs_ventas_mensuales'}
    if tablas is not None:
        pedido = [t for t in TABLAS if t in set(tablas)]
        if not pedido:
            log('Push: subset vacio, nada que pushear.')
            return {'TOTAL_MS': 0}
        if set(pedido).issubset(LEAF_SEGURAS):
            tablas_push, es_parcial = pedido, True
        else:
            tablas_push, es_parcial = list(TABLAS), False
    else:
        tablas_push, es_parcial = list(TABLAS), False

    resultados = {}
    t_total = time.time()

    with psycopg2.connect(local_url) as local, psycopg2.connect(render_url) as remote:
        with local.cursor() as lc, remote.cursor() as rc:

            # 1. TRUNCATE en Render en orden inverso (primero hijos, después padres)
            log(f'Limpiando Render… ({"parcial: " + ",".join(tablas_push) if es_parcial else "completo"})')
            for t in reversed(tablas_push):
                rc.execute(f'TRUNCATE TABLE {t} CASCADE')

            # 2. Streaming COPY por cada tabla — SIEMPRE con lista explícita
            # de columnas (intersección local ∩ Render). Ver docstring: sin
            # esto, cualquier divergencia de orden entre schemas mete valores
            # en columnas equivocadas.
            for t in tablas_push:
                t0 = time.time()
                cols = _cols_comunes(lc, rc, t)
                if not cols:
                    log(f'  {t}: SKIP (sin columnas comunes)')
                    resultados[t] = {'filas': 0, 'ms': 0, 'skip': True}
                    continue
                col_list = ', '.join(cols)
                buf = io.StringIO()
                lc.copy_expert(f'COPY {t} ({col_list}) TO STDOUT', buf)
                buf.seek(0)
                rc.copy_expert(f'COPY {t} ({col_list}) FROM STDIN', buf)
                # Contar filas copiadas
                rc.execute(f'SELECT COUNT(*) FROM {t}')
                n = rc.fetchone()[0]
                ms = int((time.time() - t0) * 1000)
                resultados[t] = {'filas': n, 'ms': ms}
                log(f'  {t}: {n:,} filas en {ms} ms  ({len(cols)} cols)')

            # 3. Propagar productos.observer_id por codigo_barra (solo en push
            #    completo — en parcial de stock/ventas el master no cambió).
            t0 = time.time()
            pares = []
            if not es_parcial:
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
