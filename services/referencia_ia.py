"""Análisis IA de los informes de referencia de mercado (portfolio líder vs
ventas propias de la farmacia).

Familia de informes que cruzan datasets IQVIA/IMS (referencia_mercado.py) contra
las ventas reales: gap de captura por marca, ranking vs nacional, cobertura de
moléculas. Cada uno tiene su serializador + system prompt; comparten el modelo y
el patrón de llamada. Texto plano para mostrar en un modal.

On-demand, baja frecuencia → Opus 4.7 (máxima calidad de análisis).
Usado por routes/informes.py.
"""

MODEL = 'claude-opus-4-7'

SYSTEM_GAP = """Sos un analista de compras de una farmacia argentina. Te paso, para UN laboratorio, sus marcas líderes a nivel nacional (ranking IQVIA) cruzadas con lo que la farmacia REALMENTE vendió en los últimos 12 meses. Las marcas con ⭐ están entre las 10 más vendidas del país.

Tu trabajo es detectar GAPS DE CAPTURA: marcas líderes (sobre todo ⭐) que la farmacia vende poco o NADA y que debería incorporar/empujar, porque hay demanda nacional comprobada. No inventes números: usá solo los que te doy.

Devolvé un análisis BREVE y ACCIONABLE en español rioplatense, texto plano con viñetas (sin encabezados markdown pesados, usá guiones):
1. Oportunidades urgentes: marcas ⭐ que NO vende (o vende muy poco) → las que más conviene incorporar ya.
2. Dónde ya está bien parada (marcas que vende fuerte).
3. Recomendación concreta: qué pedir/negociar primero y por qué (priorizá por demanda nacional y por gap propio).

Citá nombres de marcas y los números que te paso. No más de ~250 palabras. No repitas la tabla entera."""


def _serializar_gap(data):
    """Texto compacto del gap de captura para mandarle a Claude."""
    lab = data.get('nombre_lab', '?')
    cab = (f"Laboratorio: {lab}. Portfolio de marcas LÍDERES NACIONALES (IQVIA) vs tus ventas 12m. "
           f"Total propio del lab: {data.get('total_u12m', 0):,} u / "
           f"${data.get('total_monto', 0):,.0f} en 12 meses.\n"
           f"Marcas (⭐ = top 10 país):\n")
    lineas = []
    for m in data.get('marcas', []):
        estrella = '⭐' if m.get('top10_nacional') else '  '
        if m.get('vende'):
            estado = (f"vendés {m.get('u12m', 0):,}u/12m "
                      f"({m.get('u_mensual', 0)}/mes, ${m.get('monto', 0):,.0f})")
        else:
            estado = "NO la vendés (0 ventas 12m)"
        lineas.append(
            f"{estrella} {m.get('marca', '')} [{m.get('molecula', '')} · {m.get('indicacion', '')}] "
            f"— {m.get('n_productos', 0)} prod. en catálogo — {estado}"
        )
    return cab + '\n'.join(lineas)


def _llamar(api_key, system, contenido, model, max_tokens):
    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=[{'type': 'text', 'text': system, 'cache_control': {'type': 'ephemeral'}}],
        messages=[{'role': 'user', 'content': contenido}],
    )
    texto = ''.join(b.text for b in resp.content if getattr(b, 'type', '') == 'text').strip()
    if not texto:
        raise ValueError('Claude no devolvió texto.')
    return texto, resp.usage


def analizar_gap_marcas(data, api_key, model=MODEL, max_tokens=1500):
    """Análisis del gap de captura por marca estrella. Devuelve (texto, usage).

    Lanza ImportError si falta anthropic, ValueError si no hay datos, y propaga
    las excepciones de la API para que la ruta las mapee a un mensaje amigable.
    """
    if not data or not data.get('marcas'):
        raise ValueError('No hay marcas en el dataset para analizar.')
    return _llamar(api_key, SYSTEM_GAP, _serializar_gap(data), model, max_tokens)


SYSTEM_COBERTURA = """Sos un analista de compras de una farmacia argentina. Te paso, para UN laboratorio, las MOLÉCULAS líderes del ranking nacional y, por cada una: cuánto vende la farmacia en total (12m), cuánto aporta la marca del lab de referencia, cuánto la competencia/genérico, y el share que captura la marca del lab.

Tu trabajo: detectar dónde la farmacia vende la molécula pero MAYORMENTE con competencia/genérico (share bajo de la marca del lab) → oportunidad de migrar demanda a la marca líder; y moléculas con demanda que la farmacia no cubre. No inventes números: usá solo los que te doy.

Devolvé un análisis BREVE y ACCIONABLE en español rioplatense, texto plano con viñetas (guiones, sin encabezados markdown pesados):
1. Migración de share: moléculas que vende fuerte pero con share bajo de la marca del lab → dónde empujar la marca líder.
2. Buena cobertura: moléculas donde la marca del lab ya domina.
3. Recomendación concreta: qué moléculas priorizar para ganar share y por qué (volumen total + gap de share).

Citá nombres de moléculas/marcas y los números. No más de ~250 palabras. No repitas la tabla entera."""


def _serializar_cobertura(data):
    """Texto compacto de la cobertura de moléculas para mandarle a Claude."""
    lab = data.get('nombre_lab', '?')
    cab = (f"Laboratorio: {lab}. Cobertura de MOLÉCULAS líderes nacionales: por cada una, "
           f"cuánto vende la farmacia y qué share captura la marca del lab vs competencia.\n"
           f"Moléculas (ranking nacional):\n")
    lineas = []
    for m in data.get('moleculas', []):
        marca = m.get('marca_roemmers')
        ranking = m.get('ranking')
        rank_str = f"#{ranking}" if ranking else "s/rank"
        marca_lbl = marca if marca else "(el lab NO tiene marca propia para esta molécula)"
        if not m.get('vende'):
            estado = "NO se vende esa molécula en la farmacia (0 ventas 12m)"
        elif marca:
            estado = (f"total {m.get('u12m_total', 0):,}u/12m | marca {marca} {m.get('u12m_lab', 0):,}u "
                      f"(share {m.get('share_lab_pct', 0)}%) | competencia {m.get('u12m_competencia', 0):,}u "
                      f"| prod del lab {m.get('n_productos_lab', 0)}/{m.get('n_productos_total', 0)}")
        else:
            estado = (f"total {m.get('u12m_total', 0):,}u/12m, TODO competencia/genérico "
                      f"(el lab no tiene marca para captar acá)")
        lineas.append(
            f"{rank_str} {m.get('molecula', '')} "
            f"(marca lab: {marca_lbl}; líder mercado: {m.get('lider_mercado', '?')}) — {estado}"
        )
    return cab + '\n'.join(lineas)


def analizar_cobertura_moleculas(data, api_key, model=MODEL, max_tokens=1500):
    """Análisis de cobertura de moléculas líderes. Devuelve (texto, usage)."""
    if not data or not data.get('moleculas'):
        raise ValueError('No hay moléculas en el dataset para analizar.')
    return _llamar(api_key, SYSTEM_COBERTURA, _serializar_cobertura(data), model, max_tokens)


SYSTEM_RANKING = """Sos un analista de compras de una farmacia argentina. Te paso los productos MÁS VENDIDOS de UN laboratorio en tu farmacia (top por unidades 12m). Los marcados con ⭐ son marcas estrella: están entre las 10 más vendidas del país.

Tu trabajo es evaluar si el mix de ventas propio de ese lab SIGUE al mercado nacional o tiene un perfil distinto. No inventes números: usá solo los que te doy.

Devolvé un análisis BREVE y ACCIONABLE en español rioplatense, texto plano con viñetas (guiones, sin encabezados markdown pesados):
1. ¿Tu mix sigue al mercado? Cuántas marcas estrella aparecen arriba en tu ranking y cuáles del lab faltan o están bajas (las vendés poco vs su peso nacional → oportunidad de empuje).
2. Fortalezas locales: productos propios fuertes que NO son estrella nacional (nicho/fidelidad que conviene cuidar).
3. Recomendación concreta: qué empujar para alinearte con la demanda nacional sin perder tus fortalezas.

Citá nombres de productos/marcas y los números. No más de ~250 palabras. No repitas toda la lista."""


def _serializar_ranking(data):
    """Texto compacto del ranking propio vs estrellas nacionales."""
    lab = data.get('nombre_lab', '?')
    n_est = data.get('n_estrella_en_top', 0)
    n_tot = data.get('n_estrella_total', 0)
    cab = (f"Laboratorio: {lab}. Tus productos más vendidos de este lab (top por unidades 12m). "
           f"En tu top hay {n_est} productos de marcas estrella nacionales "
           f"(el lab tiene {n_tot} marcas estrella en total).\n"
           f"Ranking propio (⭐ = marca estrella nacional):\n")
    lineas = []
    for i, p in enumerate(data.get('productos', []), 1):
        marca = p.get('marca_estrella')
        estrella = f" ⭐{marca}" if marca else ""
        lineas.append(
            f"{i}. {p.get('descripcion', '')} — {p.get('u12m', 0):,}u/12m "
            f"({p.get('u_mensual', 0)}/mes, ${p.get('monto', 0):,.0f}){estrella}"
        )
    return cab + '\n'.join(lineas)


def analizar_ranking_vs_nacional(data, api_key, model=MODEL, max_tokens=1500):
    """Análisis del ranking propio del lab vs marcas estrella. Devuelve (texto, usage)."""
    if not data or not data.get('productos'):
        raise ValueError('No hay productos en el dataset para analizar.')
    return _llamar(api_key, SYSTEM_RANKING, _serializar_ranking(data), model, max_tokens)
