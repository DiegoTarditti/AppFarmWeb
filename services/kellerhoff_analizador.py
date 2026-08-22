"""Clasificador de comprobantes del portal Kellerhoff.

Reemplaza el camino IA (`factura_ia`, descartado por el usuario) por reglas
deterministas sobre el texto del PDF: distinguen "esto es una factura/NC de
mercadería con ítems" de "esto es una NC financiera (recupero de publicidad/
descuento de laboratorio, sin barcode ni ítems)". Verificado contra 90
comprobantes reales bajados del portal (sesión 2026-08-21): el vocabulario es
chico y estable, cero casos ambiguos en la muestra — no hace falta IA acá.

Flujo por comprobante:
    analizar_pdf(pdf_path) → dict con 'categoria' ('nc_financiera' | 'factura')
    y, según el caso, o bien 'concepto'/'anunciante_nombre' (nc_financiera) o
    bien 'items'/'faltantes' (factura, vía el parser regex existente).
"""
from __future__ import annotations

import re

from helpers import _normalize_quadrupled, extract_text_with_ocr_fallback

# Las NC financieras del portal son un renglón único sin barcode:
# "RECUPERO NC PAP DCTOS AGO/2026/1Q" — no hay tabla de ítems debajo.
_RE_RECUPERO_NC = re.compile(r'RECUPERO\s+NC\s+(.+)', re.IGNORECASE)

# Sección de ítems sin stock al pie de la factura: barcode + cant + desc,
# SIN columnas de precio (a diferencia del cuerpo de la factura).
_RE_MARCADOR_FALTA = re.compile(r'\*{3}\s*PRODUCTOS\s+EN\s+FALTA\s+MOMENTANEA\s*\*{3}', re.IGNORECASE)
_RE_ITEM_FALTA = re.compile(r'^(\d{7,14})\s+(\d+)\s+(.+?)\s*$', re.MULTILINE)
_RE_CORTE_FALTA = re.compile(r'Perc\.|TOTAL:', re.IGNORECASE)

# Heurística para separar el "anunciante/concepto" del período que le sigue:
# el último token de "RECUPERO NC <concepto> <periodo>" siempre trae un dígito
# (JUL/2026, S33/2026, AGO/2026/1Q...) — se van sacando tokens finales con
# dígito hasta llegar al que no tiene.
_RE_TOKEN_CON_DIGITO = re.compile(r'\d')


def leer_texto_pdf(pdf_path: str) -> str:
    return _normalize_quadrupled(extract_text_with_ocr_fallback(pdf_path))


def es_nc_financiera(full_text: str) -> bool:
    return bool(_RE_RECUPERO_NC.search(full_text))


def extraer_concepto_nc(full_text: str) -> str | None:
    """'RECUPERO NC PAP DCTOS AGO/2026/1Q' → 'PAP DCTOS AGO/2026/1Q'."""
    m = _RE_RECUPERO_NC.search(full_text)
    if not m:
        return None
    return re.sub(r'\s+', ' ', m.group(1)).strip()


def normalizar_anunciante(concepto: str) -> str:
    """'PAP DCTOS AGO/2026/1Q' → 'PAP DCTOS' (saca el período final)."""
    tokens = concepto.split()
    while len(tokens) > 1 and _RE_TOKEN_CON_DIGITO.search(tokens[-1]):
        tokens.pop()
    return ' '.join(tokens).strip().upper()


def resolver_anunciante(session, nombre_crudo: str):
    """Get-or-create de `Anunciante` por nombre normalizado. Auto-alta: no
    hace falta whitelist — cada concepto nuevo que aparece en una NC se da
    de alta solo la primera vez que se ve."""
    import database
    nombre = normalizar_anunciante(nombre_crudo)
    if not nombre:
        return None
    existente = (
        session.query(database.Anunciante)
        .filter(database.Anunciante.nombre == nombre)
        .first()
    )
    if existente:
        return existente
    anunciante = database.Anunciante(nombre=nombre)
    session.add(anunciante)
    session.flush()
    return anunciante


def extraer_faltantes(full_text: str) -> list[dict]:
    """Ítems de '*** PRODUCTOS EN FALTA MOMENTANEA ***' — sin precio, no
    facturados. Se guardan aparte (FacturaFaltante), nunca en factura_items."""
    m = _RE_MARCADOR_FALTA.search(full_text)
    if not m:
        return []
    resto = full_text[m.end():]
    corte = _RE_CORTE_FALTA.search(resto)
    bloque = resto[:corte.start()] if corte else resto

    faltantes = []
    for fm in _RE_ITEM_FALTA.finditer(bloque):
        faltantes.append({
            'codigo_barra': fm.group(1),
            'cantidad': int(fm.group(2)),
            'descripcion': re.sub(r'\s+', ' ', fm.group(3).strip()),
        })
    return faltantes


def analizar_pdf(pdf_path: str) -> dict:
    """Punto de entrada único: clasifica y, si corresponde, parsea ítems.

    Devuelve:
        nc_financiera: {'categoria': 'nc_financiera', 'concepto': str,
                         'anunciante_nombre': str}
        factura:       {'categoria': 'factura', 'items': list[dict],
                         'faltantes': list[dict]}
    """
    full_text = leer_texto_pdf(pdf_path)

    if es_nc_financiera(full_text):
        concepto = extraer_concepto_nc(full_text) or ''
        return {
            'categoria': 'nc_financiera',
            'concepto': concepto,
            'anunciante_nombre': normalizar_anunciante(concepto) if concepto else '',
        }

    from data_extract import parse_invoice_pdf
    data = parse_invoice_pdf(pdf_path, 'droguer_a_kellerhoff_s_a')
    return {
        'categoria': 'factura',
        'items': data.get('items') or [],
        'faltantes': extraer_faltantes(full_text),
    }
