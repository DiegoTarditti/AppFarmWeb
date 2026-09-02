"""Detección de candidatos a equivalencia venta↔compra.

Un producto se VENDE de una forma (ej. "ALIKAL LIMON SOB x 1", sobre suelto,
con ventas reales) pero se COMPRA como OTRO producto del catálogo (ej.
"ALIKAL LIMON EXPENDEDOR SOB x 30", la caja, sin ventas ni stock propios).
Son dos filas distintas de obs_productos, sin relación en el modelo — no es
el caso de fraccionado+cantidad_envase (mismo producto).

Heurística (validada a mano sobre datos reales antes de escribir esto):
  - "suelto": cantidad_envase=1, descripción termina en "SOB x 1" / "AMP x 1"
    (sobres/ampollas sueltas — los envases unitarios "naturales", tipo un
    tubo de crema, no entran acá), con ventas reales recientes.
  - "caja": mismo laboratorio, cantidad_envase>1, nombre con overlap alto de
    tokens contra el suelto.

Nunca auto-confirma nada — sólo propone candidatos para revisión manual en
/productos/equivalencias-compra. Casos con dosis/concentración distinta en
el nombre pueden puntuar alto por texto y ser candidatos incorrectos: la
pantalla debe dejar bien visibles ventas/stock de ambos lados para que la
persona decida.
"""
import re
from datetime import date

from sqlalchemy import text

MIN_U12M_SUELTO = 12
MIN_MESES_CON_VENTA = 4
MIN_SCORE = 0.70

STOP_TOKENS = {
    'X', 'C', 'PROSP', 'C/PROSP', 'SOB', 'AMP', 'ENV', 'COM', 'CAJ',
    'BLIST', 'EXPENDEDOR', 'EFERV.',
}


def _tokens(desc):
    d = re.sub(r'\(.*?\)', ' ', (desc or '').upper())
    d = re.sub(r'[^A-Z0-9]+', ' ', d)
    return {t for t in d.split() if t not in STOP_TOKENS and not t.isdigit() and len(t) > 1}


def detectar_candidatos(session, limit=100):
    """Devuelve una lista de dicts con pares suelto→caja sugeridos, excluyendo
    los que ya tienen una fila en equivalencias_compra (confirmada o
    descartada) para el mismo producto_venta_observer_id."""
    ya_resueltos = {
        r[0] for r in session.execute(
            text("SELECT producto_venta_observer_id FROM equivalencias_compra")
        ).fetchall()
    }

    hoy = date.today()
    anio_desde = hoy.year - 1
    mes_desde = hoy.month

    sueltos = session.execute(text("""
        WITH ventas_12m AS (
            SELECT producto_observer, SUM(unidades) AS u12m, COUNT(*) AS meses
            FROM obs_ventas_mensuales
            WHERE (anio, mes) >= (:anio_desde, :mes_desde)
            GROUP BY producto_observer
        )
        SELECT op.observer_id, op.descripcion, op.laboratorio_observer,
               ol.descripcion, COALESCE(v.u12m, 0), COALESCE(v.meses, 0)
        FROM obs_productos op
        LEFT JOIN obs_laboratorios ol ON ol.observer_id = op.laboratorio_observer
        LEFT JOIN ventas_12m v ON v.producto_observer = op.observer_id
        WHERE op.cantidad_envase = 1
          AND op.fecha_baja IS NULL
          AND op.descripcion ~* '(SOB|AMP)\\s*x\\s*1$'
          AND COALESCE(v.u12m, 0) >= :min_u12m
          AND COALESCE(v.meses, 0) >= :min_meses
        ORDER BY v.u12m DESC
        LIMIT :limit
    """), {
        'anio_desde': anio_desde, 'mes_desde': mes_desde,
        'min_u12m': MIN_U12M_SUELTO, 'min_meses': MIN_MESES_CON_VENTA,
        'limit': limit,
    }).fetchall()

    resultados = []
    for oid, desc, lab_id, lab_nombre, u12m, meses in sueltos:
        if oid in ya_resueltos:
            continue
        base_tokens = _tokens(desc)
        if not base_tokens:
            continue
        candidatos = session.execute(text("""
            SELECT observer_id, descripcion, cantidad_envase
            FROM obs_productos
            WHERE laboratorio_observer = :lab_id
              AND cantidad_envase > 1
              AND fecha_baja IS NULL
              AND observer_id != :oid
        """), {'lab_id': lab_id, 'oid': oid}).fetchall()

        mejor = None
        for c_oid, c_desc, c_env in candidatos:
            c_tokens = _tokens(c_desc)
            if not c_tokens:
                continue
            score = len(base_tokens & c_tokens) / max(len(base_tokens), 1)
            if score >= MIN_SCORE and (mejor is None or score > mejor[3]):
                mejor = (c_oid, c_desc, c_env, score)
        if not mejor:
            continue

        c_oid, c_desc, c_env, score = mejor
        caja_u12m = session.execute(text("""
            SELECT COALESCE(SUM(unidades), 0) FROM obs_ventas_mensuales
            WHERE producto_observer = :oid AND (anio, mes) >= (:anio_desde, :mes_desde)
        """), {'oid': c_oid, 'anio_desde': anio_desde, 'mes_desde': mes_desde}).scalar()
        caja_stock = session.execute(text("""
            SELECT stock_actual FROM obs_stock WHERE producto_observer = :oid LIMIT 1
        """), {'oid': c_oid}).scalar()

        resultados.append({
            'venta_observer_id': oid,
            'venta_desc': desc,
            'venta_u12m': int(u12m),
            'venta_meses_con_venta': int(meses),
            'lab_nombre': lab_nombre or '',
            'compra_observer_id': c_oid,
            'compra_desc': c_desc,
            'compra_envase': int(c_env),
            'compra_u12m': int(caja_u12m or 0),
            'compra_stock': int(caja_stock) if caja_stock is not None else None,
            'score': round(score, 2),
        })

    return resultados
