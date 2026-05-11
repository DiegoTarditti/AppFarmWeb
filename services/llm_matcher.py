"""LLM matcher para items en queue de pendientes de revisión.

Llama a Claude Haiku 4.5 con un prompt estructurado y devuelve sugerencias
de match para items que el matcher Python no resolvió.

Setup:
- Variable de entorno ANTHROPIC_API_KEY (en Render: Environment → Add).
- Spending limit USD 5/mes recomendado en console.anthropic.com.

Costos (Haiku 4.5: $1 input / $5 output por 1M tokens):
- ~$0.0015 por item (~700 tok input + ~150 tok output).
- ~$0.35 por batch de 230 items.

Caché: el system prompt está marcado con cache_control, pero el mínimo de
caché en Haiku 4.5 es 4096 tokens. Si el prompt queda por debajo, el caché
no se activa silenciosamente (cache_creation_input_tokens=0). Es seguro
dejar el marcador igual.
"""
from __future__ import annotations

import json
import logging
import os

import anthropic

log = logging.getLogger(__name__)

MODEL = 'claude-haiku-4-5'

# Precios Haiku 4.5 (USD por 1M tokens) — ref 2026-05-11
PRICE_INPUT_PER_MTOK = 1.00
PRICE_OUTPUT_PER_MTOK = 5.00
PRICE_CACHE_READ_PER_MTOK = 0.10   # ~10% del input
PRICE_CACHE_WRITE_PER_MTOK = 1.25  # ~125% del input (TTL 5min)


SYSTEM_PROMPT = """\
Sos un experto en matching de productos farmacéuticos argentinos del catálogo
de una farmacia. Tu trabajo es analizar una descripción cruda de un proveedor
y elegir el candidato correcto del catálogo local, o decidir que ninguno aplica.

# Reglas críticas

## Forma farmacéutica
NUNCA confundir formas distintas. Sinónimos válidos:
- cáps / caps / cap / cápsulas → CÁPSULAS
- com / comp / cpr / comprimidos / tab / tabletas → COMPRIMIDOS
- jbe / jarabe → JARABE
- cr / cre / crema → CREMA
- emu / emulsión → EMULSIÓN (≠ crema)
- liq / líquido / sol / solución → LÍQUIDO/SOLUCIÓN
- amp / ampollas / inyectable → AMPOLLAS
- ung / ungüento → UNGÜENTO
- gts / gotas → GOTAS
- spray / aerosol → SPRAY
- sup / supositorio → SUPOSITORIO
- óvulo → ÓVULO

EJEMPLO CRÍTICO:
- "DEXALERGIN C 10 mg cáps. x 10" SÍ matchea "DEXALERGIN C 10 mg CAP x 10" (cáps=CAP).
- "DEXALERGIN C 10 mg cáps. x 10" NO matchea "DEXALERGIN C 10 mg COM x 10" (cáps≠COM).

## Concentración y cantidad
Concentraciones (mg/g/ml/UI) y cantidad por envase (x N) deben coincidir o ser
compatibles:
- "DERMAGLOS cr 100g" matchea "DERMAGLOS CRE x 100 GRS".
- "DERMAGLOS cr 100g" NO matchea "DERMAGLOS CRE x 60 GRS" (cantidad distinta).

## Marca / nombre comercial
- "DEXALERGIN" y "DEXALERGIN C" son la misma línea de marca; preferí el match
  más específico si está disponible.
- "ENSURE PLUS Frutilla LIQ" matchea "ENSURE PLUS FRUTILLA x 220 ml".
- "MAXIMA MD" NO es lo mismo que "MAXIMA" (MD es variante específica).
- "MAXIMA MD" SÍ matchea "MAXIMA MD (21+7 PLAC)" (placebos no cambian la identidad).

## Detección de ruido
Si la descripción es claramente un header/label de Excel y no un producto real
(ej. "PRODUCTOS", "Producto", "EAN", "Descripción", "Items", "Total", "---"),
devolvé action='descartar' con confidence alta.

# Output

SIEMPRE devolvé JSON válido con esta estructura exacta:
{
  "pick_idx": <int 1-based o null>,
  "confidence": <float 0.0-1.0>,
  "reasoning": "<breve justificación, máx 250 chars>",
  "action": "vincular" | "crear_nuevo" | "descartar" | "ambiguo"
}

# Mapeo confidence → action

- confidence >= 0.85 + matchea bien → action='vincular' con pick_idx.
- 0.5 <= confidence < 0.85 → action='ambiguo' con pick_idx (operador revisa).
- confidence < 0.5 pero parece producto válido → action='crear_nuevo', pick_idx=null.
- es ruido/header/label → action='descartar', pick_idx=null.

# Ejemplos

INPUT:
Source: "DERMAGLOS cr x 100"
Lab: ANDROMACO

Candidatos:
1. DERMAGLOS CRE x 100 GRS (EAN: 7790010000123)
2. DERMAGLOS EMU x 100 ML (EAN: 7790010000124)
3. DERMAGLOS GEL x 60 GRS (EAN: 7790010000125)

OUTPUT:
{"pick_idx": 1, "confidence": 0.97, "reasoning": "cr=CREMA matchea CRE; cantidad 100g coincide; lab Andromaco correcto. EMU es emulsión (distinto), GEL distinto.", "action": "vincular"}

---

INPUT:
Source: "PRODUCTOS"
Lab: BAGÓ

Candidatos:
1. RIVOTRIL 0,5 mg COM x 30 (EAN: 7791234567890)

OUTPUT:
{"pick_idx": null, "confidence": 0.99, "reasoning": "header de Excel, no es un producto real", "action": "descartar"}

---

INPUT:
Source: "VITAMINA NUEVA SUPER X 60"
Lab: LABORATORIO XYZ

Candidatos:
(ninguno)

OUTPUT:
{"pick_idx": null, "confidence": 0.85, "reasoning": "parece producto válido pero no hay candidatos en catálogo; corresponde crear nuevo", "action": "crear_nuevo"}
"""


_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    """Lazy-init del cliente. Lee ANTHROPIC_API_KEY del entorno."""
    global _client
    if _client is not None:
        return _client
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        raise RuntimeError(
            'ANTHROPIC_API_KEY no está seteada. '
            'Setear en Render → Environment → Add var, '
            'o exportar localmente para pruebas.'
        )
    _client = anthropic.Anthropic(api_key=api_key)
    return _client


def _build_user_message(descripcion_supplier: str,
                        supplier_nombre: str | None,
                        candidatos: list[dict]) -> str:
    if not candidatos:
        cands_str = "(ninguno)"
    else:
        lines = []
        for i, c in enumerate(candidatos[:10], 1):
            ean = (c.get('codigo_barra') or '').strip() or '—'
            desc = (c.get('descripcion') or '').strip()
            lines.append(f"{i}. {desc} (EAN: {ean})")
        cands_str = "\n".join(lines)
    return (
        f"Source: \"{descripcion_supplier}\"\n"
        f"Lab: {supplier_nombre or 'desconocido'}\n\n"
        f"Candidatos:\n{cands_str}\n"
    )


def _parse_json_response(text: str) -> dict | None:
    """Extrae el primer JSON object del texto. Tolera fences markdown."""
    if not text:
        return None
    text = text.strip()
    if text.startswith('```'):
        lines = text.split('\n')
        if lines[0].startswith('```'):
            lines = lines[1:]
        if lines and lines[-1].strip() == '```':
            lines = lines[:-1]
        text = '\n'.join(lines).strip()
    start = text.find('{')
    end = text.rfind('}')
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


_VALID_ACTIONS = {'vincular', 'crear_nuevo', 'descartar', 'ambiguo'}


def _validate_result(parsed: dict) -> tuple[bool, str | None]:
    """Valida shape del JSON. Devuelve (ok, error_msg)."""
    if not isinstance(parsed, dict):
        return False, 'no es un object'
    action = parsed.get('action')
    if action not in _VALID_ACTIONS:
        return False, f'action inválido: {action!r}'
    conf = parsed.get('confidence')
    if not isinstance(conf, (int, float)) or not (0.0 <= conf <= 1.0):
        return False, f'confidence fuera de rango: {conf!r}'
    pick_idx = parsed.get('pick_idx')
    if pick_idx is not None and not isinstance(pick_idx, int):
        return False, f'pick_idx debe ser int o null, no {type(pick_idx).__name__}'
    return True, None


def _usage_dict(usage) -> dict:
    return {
        'input_tokens': getattr(usage, 'input_tokens', 0) or 0,
        'output_tokens': getattr(usage, 'output_tokens', 0) or 0,
        'cache_read': getattr(usage, 'cache_read_input_tokens', 0) or 0,
        'cache_write': getattr(usage, 'cache_creation_input_tokens', 0) or 0,
    }


def estimar_costo_usd(input_tokens: int, output_tokens: int,
                      cache_read: int = 0, cache_write: int = 0) -> float:
    """Costo en USD para una llamada Haiku 4.5 dado el usage."""
    return (
        (input_tokens / 1_000_000) * PRICE_INPUT_PER_MTOK +
        (output_tokens / 1_000_000) * PRICE_OUTPUT_PER_MTOK +
        (cache_read / 1_000_000) * PRICE_CACHE_READ_PER_MTOK +
        (cache_write / 1_000_000) * PRICE_CACHE_WRITE_PER_MTOK
    )


def estimar_costo_batch(n_items: int, system_prompt_tokens: int = 1200,
                        avg_user_msg_tokens: int = 250,
                        avg_output_tokens: int = 150) -> dict:
    """Estimación pre-batch (sin llamar a la API).

    Si el system prompt está bajo el mínimo de caché de Haiku 4.5 (4096 toks)
    no asume hit de caché y todos los items pagan input completo.
    """
    if n_items <= 0:
        return {'total_usd': 0.0, 'items': 0, 'modelo': MODEL}
    cache_activa = system_prompt_tokens >= 4096
    user_per = (avg_user_msg_tokens / 1_000_000) * PRICE_INPUT_PER_MTOK
    out_per = (avg_output_tokens / 1_000_000) * PRICE_OUTPUT_PER_MTOK
    if cache_activa:
        write_first = (system_prompt_tokens / 1_000_000) * PRICE_CACHE_WRITE_PER_MTOK
        sys_per_subseq = (system_prompt_tokens / 1_000_000) * PRICE_CACHE_READ_PER_MTOK
        total = write_first + (user_per + out_per) * n_items + sys_per_subseq * (n_items - 1)
    else:
        sys_per_item = (system_prompt_tokens / 1_000_000) * PRICE_INPUT_PER_MTOK
        total = (sys_per_item + user_per + out_per) * n_items
    return {
        'total_usd': round(total, 4),
        'items': n_items,
        'modelo': MODEL,
        'cache_aprovechado': cache_activa,
        'system_prompt_tokens': system_prompt_tokens,
    }


def analizar_pendiente(descripcion_supplier: str,
                       supplier_nombre: str | None,
                       candidatos: list[dict],
                       *,
                       model: str = MODEL,
                       max_tokens: int = 400) -> dict:
    """Llama al LLM y devuelve la sugerencia + metadata.

    Args:
        descripcion_supplier: descripción cruda del archivo del proveedor.
        supplier_nombre: nombre del lab/proveedor (para contexto).
        candidatos: hasta 10 dicts con keys 'descripcion' (req),
            'codigo_barra' (opc), 'producto_id' (opc), 'observer_id' (opc).
        model: ID del modelo (default Haiku 4.5).
        max_tokens: ceiling de output (default 400, suficiente para JSON).

    Returns:
        Dict con keys:
        - ok (bool)
        - Si ok=True: pick_idx, confidence, reasoning, action, usage, costo_usd, modelo
        - Si ok=False: error (str), opcionalmente raw_response y usage
    """
    client = _get_client()
    user_msg = _build_user_message(descripcion_supplier, supplier_nombre, candidatos)

    try:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_msg}],
        )
    except anthropic.AuthenticationError as e:
        return {'ok': False, 'error': f'auth: API key inválida ({e.message})'}
    except anthropic.RateLimitError as e:
        return {'ok': False, 'error': f'rate limit: {e.message}'}
    except anthropic.BadRequestError as e:
        return {'ok': False, 'error': f'bad request: {e.message}'}
    except anthropic.APIStatusError as e:
        return {'ok': False, 'error': f'API {e.status_code}: {e.message}'}
    except anthropic.APIConnectionError as e:
        return {'ok': False, 'error': f'connection: {e}'}
    except Exception as e:  # noqa: BLE001
        log.exception('analizar_pendiente: error inesperado')
        return {'ok': False, 'error': f'unexpected: {e}'}

    text = ''
    for block in resp.content:
        if getattr(block, 'type', '') == 'text':
            text = block.text
            break

    parsed = _parse_json_response(text)
    usage = _usage_dict(resp.usage)
    if parsed is None:
        return {
            'ok': False,
            'error': f'no JSON válido en respuesta: {text[:200]!r}',
            'usage': usage,
        }
    valid, err = _validate_result(parsed)
    if not valid:
        return {
            'ok': False,
            'error': f'JSON con shape inválido: {err}',
            'raw_response': parsed,
            'usage': usage,
        }

    costo = estimar_costo_usd(
        usage['input_tokens'], usage['output_tokens'],
        usage['cache_read'], usage['cache_write'],
    )
    return {
        'ok': True,
        'pick_idx': parsed.get('pick_idx'),
        'confidence': float(parsed['confidence']),
        'reasoning': str(parsed.get('reasoning', ''))[:300],
        'action': parsed['action'],
        'usage': usage,
        'costo_usd': costo,
        'modelo': model,
    }
