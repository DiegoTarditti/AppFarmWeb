"""Replica el catálogo MASTER (Laboratorio + Producto) de local → Render.

Distinto de push_obs_to_render.py (ese sincroniza las tablas obs_* — espejo
de ObServer). Este script sincroniza las tablas "propias" de la app:

    - laboratorios       (UPSERT por nombre UNIQUE)
    - productos          (UPSERT por codigo_barra UNIQUE; remapea laboratorio_id
                          local → Render por nombre del lab)

NO toca: pedidos, claims, borradores, ni nada que apunte a productos.id —
porque los IDs locales y de Render son distintos, y migrarlos requeriría
remapeo masivo. v1 solo bootstrappea el catálogo para que /productos en
Render muestre algo. Alts (producto_codigos_barra) y ProductoAtributo
quedan para v2 si hace falta.

Uso standalone:
    RENDER_DATABASE_URL='postgresql://...' python scripts/push_productos_master_to_render.py
"""
import os
import time

import psycopg2
import psycopg2.extras


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

            # ── 1. Laboratorios (upsert por nombre UNIQUE) ──
            t0 = time.time()
            lc.execute("""
                SELECT nombre, activo, observer_id, descuento_base
                FROM laboratorios
            """)
            labs = lc.fetchall()
            if labs:
                psycopg2.extras.execute_values(rc, """
                    INSERT INTO laboratorios (nombre, activo, observer_id, descuento_base)
                    VALUES %s
                    ON CONFLICT (nombre) DO UPDATE SET
                        activo         = EXCLUDED.activo,
                        observer_id    = EXCLUDED.observer_id,
                        descuento_base = EXCLUDED.descuento_base
                """, labs, page_size=500)
            ms = int((time.time() - t0) * 1000)
            resultados['laboratorios'] = {'filas': len(labs), 'ms': ms}
            log(f'  laboratorios: {len(labs):,} upserts en {ms} ms')

            # ── 2. Mapping local_lab_id → render_lab_id (por nombre) ──
            lc.execute("SELECT id, nombre FROM laboratorios")
            local_labs = {row[0]: row[1] for row in lc.fetchall()}
            rc.execute("SELECT id, nombre FROM laboratorios")
            render_labs_by_nombre = {row[1]: row[0] for row in rc.fetchall()}
            lab_map = {
                lid: render_labs_by_nombre.get(nombre)
                for lid, nombre in local_labs.items()
            }

            # ── 3. Productos (upsert por codigo_barra UNIQUE) ──
            t0 = time.time()
            lc.execute("""
                SELECT codigo_barra, descripcion,
                       codigo_barra_alt1, codigo_barra_alt2, codigo_barra_alt3,
                       es_pack, precio_pvp, laboratorio_id, observer_id,
                       codigo_alfabeta, monodroga, presentacion, accion_terapeutica,
                       actualizado_en, ultima_compra, fuente_creacion,
                       excluido_armado_actual, no_pedir, cantidad_reposicion_fija
                FROM productos
            """)
            rows = lc.fetchall()
            # Remapear laboratorio_id local → Render (None si el lab no existe en Render)
            mapped = [
                (
                    r[0], r[1], r[2], r[3], r[4], r[5], r[6],
                    lab_map.get(r[7]),  # ← laboratorio_id remapeado
                    r[8], r[9], r[10], r[11], r[12], r[13], r[14], r[15],
                    r[16], r[17], r[18],
                )
                for r in rows
            ]
            if mapped:
                psycopg2.extras.execute_values(rc, """
                    INSERT INTO productos (
                        codigo_barra, descripcion,
                        codigo_barra_alt1, codigo_barra_alt2, codigo_barra_alt3,
                        es_pack, precio_pvp, laboratorio_id, observer_id,
                        codigo_alfabeta, monodroga, presentacion, accion_terapeutica,
                        actualizado_en, ultima_compra, fuente_creacion,
                        excluido_armado_actual, no_pedir, cantidad_reposicion_fija
                    )
                    VALUES %s
                    ON CONFLICT (codigo_barra) DO UPDATE SET
                        descripcion              = EXCLUDED.descripcion,
                        codigo_barra_alt1        = EXCLUDED.codigo_barra_alt1,
                        codigo_barra_alt2        = EXCLUDED.codigo_barra_alt2,
                        codigo_barra_alt3        = EXCLUDED.codigo_barra_alt3,
                        es_pack                  = EXCLUDED.es_pack,
                        precio_pvp               = EXCLUDED.precio_pvp,
                        laboratorio_id           = COALESCE(EXCLUDED.laboratorio_id, productos.laboratorio_id),
                        observer_id              = COALESCE(EXCLUDED.observer_id, productos.observer_id),
                        codigo_alfabeta          = EXCLUDED.codigo_alfabeta,
                        monodroga                = EXCLUDED.monodroga,
                        presentacion             = EXCLUDED.presentacion,
                        accion_terapeutica       = EXCLUDED.accion_terapeutica,
                        actualizado_en           = EXCLUDED.actualizado_en,
                        ultima_compra            = EXCLUDED.ultima_compra,
                        fuente_creacion          = COALESCE(productos.fuente_creacion, EXCLUDED.fuente_creacion),
                        cantidad_reposicion_fija = EXCLUDED.cantidad_reposicion_fija
                """, mapped, page_size=500)
            ms = int((time.time() - t0) * 1000)
            resultados['productos'] = {'filas': len(mapped), 'ms': ms}
            log(f'  productos: {len(mapped):,} upserts en {ms} ms')

            # Cuántos quedaron sin lab por no encontrarse el match en Render
            sin_lab = sum(1 for r in rows if r[7] is not None and lab_map.get(r[7]) is None)
            if sin_lab:
                log(f'  ⚠ {sin_lab:,} productos quedaron sin laboratorio_id en Render '
                    f'(lab existe en local pero no en Render — pushear laboratorios primero).')

        remote.commit()
        local.commit()

    resultados['TOTAL_MS'] = int((time.time() - t_total) * 1000)
    log(f'TOTAL: {resultados["TOTAL_MS"]} ms')
    return resultados


if __name__ == '__main__':
    push()
