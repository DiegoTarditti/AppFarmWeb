"""Análisis IA del resumen final de un pedido (Claude Haiku 4.5).

Recibe el dataset del Stage 3 (1 fila por EAN con stock + rotación + sugerido +
total pedido + cobertura post + dto%) más unos agregados del pedido entero, y le
pide a Claude observaciones accionables: cobertura desbalanceada, capital
atado, ofertas con poco ROI, productos olvidados, etc.

On-demand, una llamada por click del operador. Cache opcional por pedido_id.
"""

MODEL = 'claude-haiku-4-5'

SYSTEM_PROMPT = """Sos un analista de compras de una farmacia argentina. El operador armó un pedido y antes de confirmarlo te lo pasa para que le marques cosas que el algoritmo determinístico no detecta: trade-offs, riesgos y oportunidades.

Tu trabajo NO es recalcular cantidades — eso ya lo hizo el motor. Tu trabajo es leer el resumen y devolver observaciones BREVES y ACCIONABLES en español rioplatense.

Métricas por línea:
- Stock: unidades en stock hoy (ObServer). null = sin dato confirmado, asumir 0.
- Avg/mes: ventas promedio mensuales últimos 12m.
- Sugerido: cantidad que el motor calculó para cubrir el objetivo del pedido (cuántos días, ver "objetivo_d").
- Total: cantidad que el operador realmente va a pedir (puede diferir del sugerido por ofertas o ajustes manuales).
- Cob.actual: días que dura el stock hoy sin pedir nada.
- Cob.post: días que dura el stock DESPUÉS de aplicar el Total. Es el dato clave.
- Gap_vs_obj: cob_post − objetivo_d. Negativo = quedaste corto. Positivo grande = sobrestockeás.
- Importe: total × precio (plata atada por esta línea).
- En oferta: si está participando de una oferta con mínimo.
- Dto%: descuento PSL si aplica.

Estructura tu respuesta en secciones cortas, texto plano con guiones (sin markdown pesado):

1. Resumen económico (1-2 líneas): plata total, ahorro estimado, concentración por lab si aplica.
2. Alertas — productos que merecen revisión ANTES de confirmar (máx 5):
   - Sobrestock fuerte (gap >+60d) — sobre todo si es importe alto.
   - Cobertura corta (gap <-7d) — pueden ir a stock-out.
   - Ofertas tomadas con cobertura desproporcionada al ahorro real.
3. Oportunidades / sugerencias concretas (máx 3): si ves un patrón que merece acción del operador.

Sé concreto: citá nombres de productos y números reales (no inventes). Si todo se ve OK, decilo y no inventes alertas. Máx 250 palabras."""


def _trim_nombre(s, n=50):
    s = (s or '').strip()
    return s if len(s) <= n else s[:n - 1] + '…'


def _serializar(lab_nombre, n_days, rows, agregados):
    """Arma el texto compacto para Claude. rows = lista del exportData del frontend."""
    cab = (f"Lab: {lab_nombre or '—'} · objetivo del pedido: {n_days}d · "
           f"{len(rows)} productos en el resumen.\n\n"
           f"Agregados del pedido:\n")
    for k, v in (agregados or {}).items():
        cab += f"- {k}: {v}\n"
    cab += "\nLíneas (1 por EAN). Solo incluyo las que tienen Total>0 o gap notable:\n"

    lineas = []
    for r in rows:
        total = int(r.get('total') or 0)
        gap = r.get('gap_vs_objetivo_d')
        avg = r.get('avg_monthly') or 0
        # Filtrá ruido: solo líneas accionables.
        if total == 0 and (gap is None or abs(gap or 0) < 30) and avg < 1:
            continue
        nombre = _trim_nombre(r.get('nombre'))
        ean = r.get('ean', '')
        stock = r.get('stock_obs')
        stock_str = str(stock) if stock is not None else 'null'
        sugerido = r.get('cant_pedida') or 0
        cob_act = r.get('cobertura_actual_d')
        cob_post = r.get('cobertura_post_d')
        precio = r.get('precio_pvp') or 0
        importe = round(total * precio)
        in_off = total > 0 and (r.get('cant_oferta_min') or 0) > 0
        lineas.append(
            f"- {nombre} [{ean}] stock={stock_str} avg={avg:.1f}/m "
            f"sug={sugerido} tot={total} cob_act={cob_act}d cob_post={cob_post}d "
            f"gap={gap}d imp=${importe:,}"
            + (" [en_oferta]" if in_off else "")
        )
    return cab + '\n'.join(lineas[:200])   # cap defensivo


def analizar_pedido(lab_nombre, n_days, rows, agregados,
                    api_key, model=MODEL, max_tokens=1200):
    """Llama a Claude con el resumen del pedido y devuelve (texto, usage).

    Lanza ImportError si falta anthropic, ValueError si no hay filas, y propaga
    las excepciones de la API para que la ruta las mapee a mensajes amigables.
    """
    if not rows:
        raise ValueError('No hay líneas en el resumen del pedido.')
    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)
    contenido = _serializar(lab_nombre, n_days, rows, agregados)
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=[{'type': 'text', 'text': SYSTEM_PROMPT,
                 'cache_control': {'type': 'ephemeral'}}],
        messages=[{'role': 'user', 'content': contenido}],
    )
    texto = ''.join(b.text for b in resp.content
                    if getattr(b, 'type', '') == 'text').strip()
    if not texto:
        raise ValueError('Claude no devolvió texto.')
    return texto, resp.usage
