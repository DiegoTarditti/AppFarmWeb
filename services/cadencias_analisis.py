"""Análisis en prosa del snapshot de cadencias por laboratorio (Claude).

Toma el top de labs del snapshot ya materializado (CadenciaLabSnapshot) y le
pide a Claude un análisis accionable para el comprador de la farmacia: dónde se
concentra la facturación, capital inmovilizado (dormidos con stock), labs en
caída, oportunidades. Devuelve texto plano para mostrar en un modal.

Usado por routes/informes.py (`/informes/cadencias-resumen/analizar-ia`).
On-demand, baja frecuencia → modelo Opus 4.7 (máxima calidad de análisis).
"""

MODEL = 'claude-opus-4-7'

SYSTEM_PROMPT = """Sos un analista de compras de una farmacia argentina. Te paso un resumen de cadencias por laboratorio (datos ya calculados, no inventes números). Tu trabajo es leerlos y devolver un análisis BREVE y ACCIONABLE en español rioplatense, para que el dueño decida qué comprar/revisar.

Qué significa cada métrica (por laboratorio):
- $/mes: facturación mensual estimada del lab. Es el peso del lab en el negocio.
- RFM (sobre TODOS los productos del lab): core = vende seguido y reciente (la base); ocasional = esporádico pero reciente; caída = vendía seguido y hace rato no (RIESGO, revisar); dormido = sin ventas hace tiempo.
- Rotación (solo productos con ventas): alta / media-alta / media / baja / muy-baja.
- dormido c/stock: productos dormidos que TIENEN stock → capital inmovilizado. Se acompaña del $ inmovilizado y las unidades.
- con_ventas / sin_ventas: cuántos productos del lab tuvieron o no movimiento.

Estructura tu respuesta en secciones cortas con viñetas (sin markdown de encabezados pesados, usá texto plano con guiones):
1. Dónde se concentra la plata (2-3 labs top por $/mes y qué tan sano es su mix core/caída).
2. Alertas: labs con "caída" alta (riesgo de perder ventas) y capital inmovilizado relevante (dormido c/stock con $ alto).
3. Oportunidades / qué hacer (acciones concretas: negociar, desinvertir dormidos, vigilar caídas).

Sé concreto, citá nombres de labs y números. No más de ~250 palabras. No repitas la tabla entera."""


def _serializar(filas_top, meta, vista='cantidad'):
    """Arma el texto compacto que se le manda a Claude."""
    m = meta or {}
    hint = ('El usuario está mirando la vista por MONTO ($/mes): priorizá el análisis económico.'
            if vista == 'monto' else
            'El usuario está mirando la vista por CANTIDAD de productos.')
    cab = (f"Snapshot de cadencias — {m.get('n_labs', len(filas_top))} labs totales, "
           f"cobertura {m.get('cobertura', '?')}d, ventana avg {m.get('meses_rot', '?')} meses. "
           f"{hint} Te paso el top {len(filas_top)} por facturación mensual:\n")
    lineas = []
    for i, f in enumerate(filas_top, 1):
        lineas.append(
            f"{i}. {f.get('nombre', f.get('lab_id'))}: ${f.get('monto_mensual', 0):,.0f}/mes | "
            f"RFM core={f.get('core', 0)} ocasional={f.get('ocasional', 0)} "
            f"caida={f.get('caida', 0)} dormido={f.get('dormido', 0)} | "
            f"rotacion alta={f.get('alta', 0)} m-alta={f.get('media_alta', 0)} "
            f"media={f.get('media', 0)} baja={f.get('baja', 0)} m-baja={f.get('muy_baja', 0)} | "
            f"productos con_ventas={f.get('con_ventas', 0)}/sin_ventas={f.get('sin_ventas', 0)} | "
            f"dormido c/stock={f.get('dormido_con_stock', 0)} "
            f"(${f.get('dormido_valor', 0):,.0f}, {f.get('dormido_stock_u', 0)}u)"
        )
    return cab + '\n'.join(lineas)


def analizar_cadencias(filas_top, meta, api_key, model=MODEL, max_tokens=1500, vista='cantidad'):
    """Llama a Claude con el resumen de cadencias y devuelve (texto, usage).

    Lanza ImportError si falta anthropic, ValueError si no hay filas, y propaga
    las excepciones de la API (crédito, key, rate limit) para que la ruta las
    mapee a un mensaje amigable.
    """
    if not filas_top:
        raise ValueError('No hay laboratorios en el snapshot para analizar.')
    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)
    contenido = _serializar(filas_top, meta, vista)
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=[{'type': 'text', 'text': SYSTEM_PROMPT, 'cache_control': {'type': 'ephemeral'}}],
        messages=[{'role': 'user', 'content': contenido}],
    )
    texto = ''.join(b.text for b in resp.content if getattr(b, 'type', '') == 'text').strip()
    if not texto:
        raise ValueError('Claude no devolvió texto.')
    return texto, resp.usage
