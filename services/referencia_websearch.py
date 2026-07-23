"""Recopilación de marcas estrella de un laboratorio vía web search de Claude.

PASO 1 del informe de gap de captura: para cualquiera de los 8 labs habilitados,
Claude busca en internet sus marcas líderes en Argentina (+ ranking, molécula,
indicación) y devuelve un JSON estructurado + las fuentes citadas. El PASO 2
(cruce contra ventas propias + prosa) lo hacen `helpers.cruzar_marcas_vs_ventas`
y `services/referencia_ia.analizar_gap_marcas`.

On-demand y baja frecuencia; web search es caro (~$10/1000 búsquedas) → el caller
(routes/informes.py) cachea el resultado por nombre de lab. Modelo Sonnet 4.6.
"""
import json

MODEL = 'claude-sonnet-4-6'

SYSTEM_RECOPILAR = """Sos un analista de mercado farmacéutico argentino. Te paso el nombre de UN laboratorio. Buscá en fuentes confiables (IQVIA, CILFA, Kantar, prensa especializada del sector, sitios del propio laboratorio) cuáles son sus MARCAS ESTRELLA en Argentina: las de mayor venta y recordación, idealmente las que están entre las más vendidas del país.

Para cada marca identificá: nombre comercial, molécula (principio activo), indicación breve, y si está entre las ~10 marcas/medicamentos más vendidos del país (top10_nacional).

Después de buscar, devolvé EXCLUSIVAMENTE un bloque JSON (sin texto antes ni después) con esta forma EXACTA:

```json
{
  "marcas": [
    {"marca": "Nombre", "molecula": "principio activo", "indicacion": "uso breve", "top10_nacional": true, "match_pattern": "NOMBRE"}
  ],
  "fuentes": [
    {"titulo": "Título de la fuente", "url": "https://..."}
  ]
}
```

Reglas:
- `match_pattern`: el nombre de la marca en MAYÚSCULAS y SIN ACENTOS, el token más distintivo (se usa para buscar el producto en una base de datos por coincidencia de texto). Ej: marca "Lotrial" -> "LOTRIAL".
- Incluí entre 8 y 15 marcas, las más relevantes del laboratorio.
- `fuentes`: solo las páginas que realmente consultaste, con su URL real.
- No inventes marcas: si no encontrás datos, devolvé "marcas": [] con las fuentes que miraste.
- NADA fuera del bloque JSON."""


def _extraer_json(texto):
    """Extrae el primer objeto JSON {...} balanceado del texto, ignorando los
    fences ```json. Robusto a texto antes/después; devuelve None si está
    truncado (llaves sin cerrar) o no parsea."""
    i = texto.find('{')
    if i == -1:
        return None
    depth = 0
    for k in range(i, len(texto)):
        ch = texto[k]
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(texto[i:k + 1])
                except (ValueError, TypeError):
                    return None
    return None  # llaves sin balancear → JSON truncado


def _fuentes_de_response(resp):
    """Junta fuentes (titulo, url) de los bloques web_search_tool_result y de las
    citations de los text blocks. Dedup por url, preservando orden."""
    vistos, fuentes = set(), []

    def _add(url, titulo):
        if url and url not in vistos:
            vistos.add(url)
            fuentes.append({'titulo': titulo or url, 'url': url})

    for b in resp.content:
        btype = getattr(b, 'type', '')
        if btype == 'web_search_tool_result':
            for r in (getattr(b, 'content', None) or []):
                _add(getattr(r, 'url', None), getattr(r, 'title', None))
        elif btype == 'text':
            for c in (getattr(b, 'citations', None) or []):
                _add(getattr(c, 'url', None), getattr(c, 'title', None))
    return fuentes


def recopilar_marcas_estrella(nombre_lab, api_key, model=MODEL, max_uses=5):
    """PASO 1 — busca en web las marcas estrella del lab.

    Devuelve (marcas, fuentes, usage):
      marcas: lista de dicts {marca, molecula, indicacion, top10_nacional, match_pattern}.
      fuentes: lista de dicts {titulo, url}.

    Lanza ImportError si falta anthropic, ValueError si no parsea el JSON, y
    propaga las excepciones de la API para que la ruta las mapee a un mensaje.
    """
    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=model,
        # alto: el web search mete los resultados en el contexto y el JSON de
        # marcas + fuentes es largo → con poco margen el JSON sale truncado.
        max_tokens=6000,
        system=[{'type': 'text', 'text': SYSTEM_RECOPILAR,
                 'cache_control': {'type': 'ephemeral'}}],
        tools=[{'type': 'web_search_20250305', 'name': 'web_search',
                'max_uses': max_uses}],
        messages=[{'role': 'user',
                   'content': (f'Laboratorio: {nombre_lab} (Argentina). '
                               f'Buscá sus marcas estrella y devolvé el JSON.')}],
    )
    texto = ''.join(b.text for b in resp.content
                    if getattr(b, 'type', '') == 'text').strip()
    data = _extraer_json(texto)
    if not data or 'marcas' not in data:
        extra = (' (respuesta cortada por límite de tokens)'
                 if getattr(resp, 'stop_reason', '') == 'max_tokens' else '')
        raise ValueError(f'La búsqueda no devolvió un JSON de marcas válido{extra}.')
    marcas = data.get('marcas') or []

    # Fuentes: las que cita el modelo en el JSON (curadas) primero + las reales
    # del response (dedup por url). Cap a 12 para no inundar el modal/PDF.
    fuentes = [f for f in (data.get('fuentes') or []) if f.get('url')]
    urls = {f['url'] for f in fuentes}
    for f in _fuentes_de_response(resp):
        if f['url'] not in urls:
            fuentes.append(f)
            urls.add(f['url'])
    return marcas, fuentes[:12], resp.usage
