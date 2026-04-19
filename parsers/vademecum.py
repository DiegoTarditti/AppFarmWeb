"""Scraper para PR Vademécum Argentina (ar.prvademecum.com).

Busca medicamentos y extrae: principio activo (monodroga), laboratorio,
acción terapéutica y presentaciones.  Usa cache local en DB para no
repetir consultas.
"""

import re
import ssl
import urllib.request
from html import unescape

BASE = 'https://ar.prvademecum.com'
_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'


def _fetch(url):
    """GET con bypass de SSL (cert expirado en prvademecum)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={'User-Agent': _UA})
    with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
        return resp.read().decode('utf-8', errors='replace')


def _clean(text):
    """Limpia HTML tags y espacios extra."""
    text = re.sub(r'<[^>]+>', ' ', text)
    text = unescape(text)
    return re.sub(r'\s+', ' ', text).strip()


# ── Búsqueda ──────────────────────────────────────────────────────────

def search(query):
    """Busca en PR Vademécum.  Devuelve lista de dicts con name, url, tipo."""
    url = f'{BASE}/resultados/?q={urllib.request.quote(query)}'
    html = _fetch(url)

    results = []
    seen = set()
    # Estructura 2026: <a ... href="https://ar.prvademecum.com/medicamento/slug/">NOMBRE</a>
    # El href puede venir absoluto o relativo.
    for m in re.finditer(
        r'<a[^>]*href="(?:https?://[^/"]+)?(/medicamento/[^"]+)"[^>]*>(.*?)</a>',
        html, re.DOTALL
    ):
        href = m.group(1)
        if href in seen:
            continue
        seen.add(href)
        name = _clean(m.group(2))
        if not name:
            continue
        # Tipo: buscar label cercano (ej "Producto", "Sustancia")
        tipo = ''
        chunk = html[m.end():m.end() + 400]
        t = re.search(r'<(?:p|span|div)[^>]*>\s*(Producto|Sustancia|Laboratorio)\s*</', chunk, re.IGNORECASE)
        if t:
            tipo = t.group(1).strip().capitalize()
        results.append({
            'name': name,
            'url': BASE + href,
            'slug': href.rstrip('/').split('/')[-1],
            'tipo': tipo,
        })
    return results


# ── Detalle de medicamento ────────────────────────────────────────────

def detail(url_or_slug):
    """Extrae datos del detalle de un medicamento.

    Devuelve dict con: nombre, principio_activo, laboratorio,
    accion_terapeutica, presentaciones (lista de strings),
    composicion (texto).
    """
    if url_or_slug.startswith('http'):
        url = url_or_slug
    else:
        url = f'{BASE}/medicamento/{url_or_slug}/'

    html = _fetch(url)
    data = {
        'nombre': '',
        'principio_activo': '',
        'laboratorio': '',
        'accion_terapeutica': '',
        'presentaciones': [],
        'composicion': '',
    }

    # Nombre del producto (primer H1)
    m = re.search(r'<H1>(.*?)</H1>', html)
    if m:
        data['nombre'] = _clean(m.group(1))

    # Principio activo
    m = re.search(
        r'Principios?\s+Activos?.*?<div class="title-item">(.*?)</div>',
        html, re.DOTALL | re.IGNORECASE
    )
    if m:
        data['principio_activo'] = _clean(m.group(1)).rstrip('→').strip()

    # Laboratorio
    m = re.search(
        r'Laboratorio\s+que\s+comercializa.*?<div class="title-item">(.*?)</div>',
        html, re.DOTALL | re.IGNORECASE
    )
    if m:
        data['laboratorio'] = _clean(m.group(1)).rstrip('→').strip()

    # Acción terapéutica (primer <P><I>...</I></P>)
    m = re.search(r'<P><I>(.*?)</I></P>', html)
    if m:
        data['accion_terapeutica'] = _clean(m.group(1))

    # Presentaciones
    for pm in re.finditer(
        r'<h4>Presentaci[^<]*</[Hh]4>\s*<P>(.*?)</P>', html, re.DOTALL
    ):
        data['presentaciones'].append(_clean(pm.group(1)))

    # Composición (primera)
    m = re.search(
        r'<h4>Composici[^<]*</[Hh]4>\s*<P>(.*?)</P>', html, re.DOTALL
    )
    if m:
        data['composicion'] = _clean(m.group(1))

    return data
