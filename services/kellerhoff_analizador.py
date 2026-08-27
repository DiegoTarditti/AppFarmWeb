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


# Renglón del cuerpo de la factura (con precios, a diferencia de los faltantes):
#   BARCODE CANT DESC [flags] PRECIO_PUB %DTO PRECIO_UNIT IMPORTE
# Los 4 números finales en formato AR ('12.918,24'). El dto es el 2º — es
# justo lo que la tabla HTML del portal NO trae. La desc absorbe flags (TL/WEB/
# TRZ); no importan para el dto.
_PRECIO_AR = r'[\d.]+,\d{2}'
_RE_ITEM_PDF = re.compile(
    r'^(\d{7,14})\s+(\d+)\s+(.+?)\s+'
    rf'({_PRECIO_AR})\s+({_PRECIO_AR})\s+({_PRECIO_AR})\s+({_PRECIO_AR})\s*$',
    re.MULTILINE)


def _num_ar(s: str) -> float:
    try:
        return float(s.replace('.', '').replace(',', '.'))
    except (ValueError, AttributeError):
        return 0.0


def parsear_items_pdf(full_text: str) -> list[dict]:
    """Ítems del cuerpo de la factura desde el texto del PDF, CON descuento.

    La tabla HTML del portal no trae la columna de %Dto (ni el cuerpo cuando el
    HTML falla); el PDF sí. Devuelve por renglón: barcode, cantidad, descripcion,
    precio_pub, dto_pct, precio_unitario, importe. Corta antes de la sección
    '*** PRODUCTOS EN FALTA ***' (que no lleva precios, para no mezclarla)."""
    m = _RE_MARCADOR_FALTA.search(full_text)
    cuerpo = full_text[:m.start()] if m else full_text
    items = []
    for it in _RE_ITEM_PDF.finditer(cuerpo):
        bc, cant, desc, pub, dto, unit, imp = it.groups()
        items.append({
            'barcode': bc,
            'cantidad': int(cant),
            'descripcion': ' '.join(desc.split()),
            'precio_pub': _num_ar(pub),
            'dto_pct': _num_ar(dto),
            'precio_unitario': _num_ar(unit),
            'importe': _num_ar(imp),
        })
    return items


def dto_por_barcode(full_text: str) -> dict:
    """{barcode: dto%} para completar los ítems que vienen del HTML sin dto."""
    return {it['barcode']: it['dto_pct'] for it in parsear_items_pdf(full_text)}


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
