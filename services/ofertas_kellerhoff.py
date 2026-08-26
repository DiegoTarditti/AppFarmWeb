"""Ofertas Kellerhoff por mínimo de compra: análisis del CSV → "vigentes".

El portal publica `ProductosEnOferta.csv` (columnas
Nombre;Codigo Barra;Unidades Minimas;Unidades Maximas;Unidades Multiplo;
Unidades Bonificadas;Descripcion Oferta). Este módulo lo mastica UNA vez y
produce el artefacto **vigente** que después consume el armado de pedido.

Reglas de negocio (decididas con Diego, ver docs/controles_kellerhoff.md):
  - Solo interesan las ofertas con `Unidades Minimas > 1` (las de mínimo 1 son
    descuento de todos los días, no "por cantidad"). Los múltiplos (Minimas=0,
    Multiplo>0) quedan afuera por ahora.
  - Por EAN se toma la fila de **menor mínimo > 1**.
  - `delta` = % al mínimo − % que ya se consigue comprando poco (la fila de
    mínimo 1 de la MISMA base; 0 si no hay). Mide cuánto se gana de verdad.
      · Escalonada (hay mínimo 1): delta chico (ej. ALMAXIMO 76.99→79.99 = +3).
      · Todo-o-nada (no hay mínimo 1): delta = el % entero (30–70%).
  - Se descarta si `delta < UMBRAL_DELTA` (default 10): los saltos marginales no
    justifican el sobre-stock. Con 10 muere casi todo lo escalonado y quedan las
    todo-o-nada.

La rotación (cobertura del mínimo ≤ 1 mes) NO se aplica acá: depende de la venta
del momento y va en el armado del pedido, no en el vigente.
"""
from __future__ import annotations

import csv
import io
import re

UMBRAL_DELTA_DEFAULT = 10.0
COBERTURA_MAX_MESES_DEFAULT = 1.0   # cuántos meses de venta banco comprar por una oferta

# "44.82% de Dto. s/PVP"  |  "2% de Dto. s/P.Farmacia"
_RE_DESC = re.compile(r'([\d.,]+)\s*%\s*de\s*Dto\.\s*s/\s*(.+)', re.IGNORECASE)


def decodificar(datos: bytes) -> str:
    """El CSV del portal es UTF-8, pero por las dudas caemos a latin-1 (el
    mojibake `NIÃO` aparece cuando se lee UTF-8 como latin-1, no al revés)."""
    for enc in ('utf-8-sig', 'utf-8', 'latin-1'):
        try:
            return datos.decode(enc)
        except UnicodeDecodeError:
            continue
    return datos.decode('utf-8', errors='replace')


def _num(txt: str):
    """'44.82' o '44,82' → 44.82; '' → None."""
    txt = (txt or '').strip().replace(',', '.')
    if not txt:
        return None
    try:
        return float(txt)
    except ValueError:
        return None


def _parse_descuento(desc: str):
    """'44.82% de Dto. s/PVP' → (44.82, 'PVP'). Devuelve (None, None) si no matchea."""
    m = _RE_DESC.search(desc or '')
    if not m:
        return None, None
    pct = _num(m.group(1))
    base_raw = m.group(2).strip().upper()
    base = 'P.Farmacia' if 'FARMACIA' in base_raw else ('PVP' if 'PVP' in base_raw else base_raw.title())
    return pct, base


def _leer_filas(texto: str):
    """Filas crudas del CSV, deduplicadas. Cada una: dict con los 7 campos."""
    reader = csv.reader(io.StringIO(texto), delimiter=';')
    filas, vistas = [], set()
    for i, row in enumerate(reader):
        if i == 0 and row and row[0].strip().lower().startswith('nombre'):
            continue  # header
        if len(row) < 7:
            continue
        nombre = row[0].strip()
        ean = row[1].strip()
        minimas = _num(row[2]) or 0
        multiplo = _num(row[4]) or 0
        pct, base = _parse_descuento(row[6])
        if not ean or pct is None:
            continue
        clave = (ean, int(minimas), int(multiplo), pct, base)
        if clave in vistas:
            continue
        vistas.add(clave)
        filas.append({'nombre': nombre, 'ean': ean, 'minimas': int(minimas),
                      'multiplo': int(multiplo), 'pct': pct, 'base': base})
    return filas


def parsear_vigentes(texto: str, umbral_delta: float = UMBRAL_DELTA_DEFAULT) -> list[dict]:
    """CSV → lista de ofertas vigentes (una por EAN que califica).

    Cada dict: {ean, nombre, unidades_minimas, descuento_pct, base, baseline_pct,
                delta}.
    """
    filas = _leer_filas(texto)

    por_ean: dict[str, list[dict]] = {}
    for f in filas:
        por_ean.setdefault(f['ean'], []).append(f)

    vigentes = []
    for ean, rows in por_ean.items():
        candidatas = [r for r in rows if r['minimas'] > 1]
        if not candidatas:
            continue
        # Fila de menor mínimo (>1); si empatan, la de mayor descuento.
        min_cand = min(r['minimas'] for r in candidatas)
        cand = max((r for r in candidatas if r['minimas'] == min_cand),
                   key=lambda r: r['pct'])
        # Baseline: lo que ya se consigue comprando poco (mínimo 1), misma base.
        baseline = max((r['pct'] for r in rows
                        if r['minimas'] == 1 and r['base'] == cand['base']),
                       default=0.0)
        delta = round(cand['pct'] - baseline, 2)
        if delta < umbral_delta:
            continue
        vigentes.append({
            'ean': ean,
            'nombre': cand['nombre'],
            'unidades_minimas': min_cand,
            'descuento_pct': cand['pct'],
            'base': cand['base'],
            'baseline_pct': baseline,
            'delta': delta,
        })
    vigentes.sort(key=lambda v: v['nombre'].lower())
    return vigentes


# ── Persistencia (vigente + crudo) ───────────────────────────────────────────

def guardar_vigentes(session, vigentes) -> int:
    """Reemplaza la tabla de vigentes con la lista dada. Devuelve el count."""
    from database import KellerhoffOferta, now_ar
    session.query(KellerhoffOferta).delete()
    ahora = now_ar()
    for v in vigentes:
        session.add(KellerhoffOferta(
            ean=v['ean'], nombre=(v['nombre'] or '')[:200],
            unidades_minimas=v['unidades_minimas'],
            descuento_pct=v['descuento_pct'], base=v['base'],
            delta=v['delta'], actualizado_en=ahora))
    session.flush()
    return len(vigentes)


def importar_desde_texto(session, texto, umbral_delta=UMBRAL_DELTA_DEFAULT,
                         descargado_en=None) -> dict:
    """Guarda el CSV crudo + calcula y persiste el vigente. Devuelve stats.

    El crudo queda para reprocesar (cambiar umbral) sin volver a bajar del portal.
    """
    from database import KellerhoffOfertasFuente, now_ar
    vigentes = parsear_vigentes(texto, umbral_delta)
    n = guardar_vigentes(session, vigentes)
    fuente = session.get(KellerhoffOfertasFuente, 1)
    if fuente is None:
        fuente = KellerhoffOfertasFuente(id=1)
        session.add(fuente)
    if descargado_en is not None:
        fuente.descargado_en = descargado_en
    fuente.generado_en = now_ar()
    fuente.delta_umbral = umbral_delta
    fuente.n_vigentes = n
    fuente.csv_texto = texto
    session.flush()
    return {'vigentes': n, 'umbral': umbral_delta}


def reprocesar(session, umbral_delta=None) -> dict | None:
    """Re-corre el análisis sobre el CSV guardado (para cambiar el umbral sin
    redescargar). None si no hay CSV guardado."""
    from database import KellerhoffOfertasFuente
    fuente = session.get(KellerhoffOfertasFuente, 1)
    if fuente is None or not fuente.csv_texto:
        return None
    if umbral_delta is None:
        umbral_delta = (float(fuente.delta_umbral) if fuente.delta_umbral is not None
                        else UMBRAL_DELTA_DEFAULT)
    return importar_desde_texto(session, fuente.csv_texto, umbral_delta,
                                descargado_en=fuente.descargado_en)


def _fila_oferta(o) -> dict:
    return {'ean': o.ean, 'nombre': o.nombre,
            'unidades_minimas': o.unidades_minimas,
            'descuento_pct': float(o.descuento_pct),
            'base': o.base, 'delta': float(o.delta)}


def oferta_para_ean(session, ean):
    """La oferta vigente de ese EAN, o None."""
    from database import KellerhoffOferta
    o = session.get(KellerhoffOferta, str(ean))
    return _fila_oferta(o) if o is not None else None


def ofertas_por_ean(session, eans) -> dict:
    """{ean: oferta} para los EAN que tengan oferta vigente (bulk, para el pedido)."""
    from database import KellerhoffOferta
    eans = list({str(e) for e in eans if e})
    if not eans:
        return {}
    return {o.ean: _fila_oferta(o) for o in
            session.query(KellerhoffOferta).filter(KellerhoffOferta.ean.in_(eans))}


def estado_fuente(session) -> dict:
    """Meta del vigente: descargado_en, generado_en, n_vigentes, delta_umbral."""
    from database import KellerhoffOfertasFuente
    f = session.get(KellerhoffOfertasFuente, 1)
    if f is None:
        return {'descargado_en': None, 'generado_en': None,
                'n_vigentes': 0, 'delta_umbral': None}
    return {'descargado_en': f.descargado_en, 'generado_en': f.generado_en,
            'n_vigentes': f.n_vigentes or 0,
            'delta_umbral': (float(f.delta_umbral) if f.delta_umbral is not None
                             else None)}


def califica_por_rotacion(unidades_minimas, venta_mensual,
                          cobertura_max=COBERTURA_MAX_MESES_DEFAULT) -> bool:
    """True si comprar el mínimo es ≤ cobertura_max meses de venta. Sin venta
    (rotación 0) nunca califica: comprar el mínimo sería stock parado."""
    if not venta_mensual or venta_mensual <= 0:
        return False
    return (unidades_minimas / venta_mensual) <= cobertura_max


# ── Descarga del portal (reusa el login del scraper) ─────────────────────────

def descargar_csv() -> str:
    """Baja el ProductosEnOferta.csv del portal reusando el login del scraper.
    Devuelve el texto decodificado. Lanza si no puede.

    ⚠ Verificar en el server qué devuelve exactamente `/config/descarga` (si es el
    CSV directo o una pantalla con botón) y ajustar si hace falta — no se puede
    probar desde afuera de la red de la farmacia.
    """
    from playwright.sync_api import sync_playwright

    from services.kellerhoff_scraper import KH_URL, _login
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        try:
            _login(ctx.new_page())
            resp = ctx.request.get(f'{KH_URL}/config/descarga')
            return decodificar(resp.body())
        finally:
            browser.close()


def actualizar(session, umbral_delta=UMBRAL_DELTA_DEFAULT) -> dict:
    """Baja el CSV del portal e importa (crudo + vigente). Devuelve stats."""
    from database import now_ar
    texto = descargar_csv()
    return importar_desde_texto(session, texto, umbral_delta, descargado_en=now_ar())


def necesita_refresh(session, dias=30) -> bool:
    """True si nunca se bajó o si la última descarga tiene más de `dias`."""
    from datetime import timedelta

    from database import now_ar
    est = estado_fuente(session)
    if not est['descargado_en']:
        return True
    return (now_ar() - est['descargado_en']) > timedelta(days=dias)
