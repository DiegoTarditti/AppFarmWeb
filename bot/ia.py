"""Nodo de IA libre: el cliente escribe en lenguaje natural y Claude responde,
usando una herramienta (tool use) para consultar el stock/precio real.

Es el diferencial sobre los bots genéricos: entiende "algo para la tos" y
cruza con la data de la farmacia. NO diagnostica; deriva al farmacéutico para
lo médico y aclara cuando algo necesita receta.
"""
import json
import os

from bot.data import buscar_productos
from bot.info import FICHA

MODEL = 'claude-sonnet-4-6'

SYSTEM = """Sos el asistente por chat de Farmacia Badia (Rosario). Atendés clientes de forma amable, breve y en español rioplatense (de vos).

REGLAS IMPORTANTES:
- Para precio o stock USÁ SIEMPRE la herramienta buscar_producto (datos reales). Nunca inventes precios ni disponibilidad.
- NO hagas diagnósticos ni indiques tratamientos. Si describen un síntoma, podés orientar sobre productos de venta libre habituales, pero sugerí consultar al farmacéutico para algo puntual.
- Si un producto requiere receta, aclaralo ("ese necesita receta, acercate con ella").
- Respuestas CORTAS. Si no encontrás lo que buscan o necesitan una persona, NO digas que ya avisaste ni que derivaste (no podés hacerlo vos): invitalos a escribir "operador" o tocar "Hablar con una persona" y el sistema los deriva.
- No des consejos peligrosos ni dosis.
- FORMATO CHAT (WhatsApp): NADA de tablas ni Markdown (asteriscos, pipes, #). Listá como máximo 5 productos, uno por línea con viñeta •, así: "• Ibuprofeno 600 x10 — $3.000 — en stock"."""

TOOLS = [{
    'name': 'buscar_producto',
    'description': 'Busca productos en el stock real de la farmacia por nombre comercial o por droga. Devuelve descripción, precio y stock.',
    'input_schema': {
        'type': 'object',
        'properties': {
            'query': {'type': 'string',
                      'description': 'nombre o droga a buscar, ej "ibuprofeno 600" o "amoxidal"'},
        },
        'required': ['query'],
    },
}]


SYSTEM_RECETA = """Sos el asistente de Farmacia Badia (Rosario). Te mandan la FOTO de una receta médica.

Tu tarea:
1. Identificá los medicamentos recetados (nombre comercial o droga + presentación/dosis si se ve).
2. Para CADA medicamento, usá la herramienta buscar_producto para ver si hay stock real.
3. Respondé en español rioplatense (de vos), formato CHAT: viñetas con •, SIN tablas ni Markdown, breve.
   - Qué medicamentos tenés en stock (con precio).
   - Cuáles no tenés, y ofrecé encargarlos.
   - SIEMPRE cerrá aclarando que para retirar hay que traer la RECETA ORIGINAL en papel.

REGLAS: No interpretes el diagnóstico ni des indicaciones médicas. Si la receta está ilegible o no es una receta, pedí amablemente que la manden más clara o que se acerquen a la farmacia."""


# Bloque común: la info real de la farmacia + la regla anti-invención. Se
# agrega a TODOS los system prompts para que el bot no alucine servicios.
_INFO = f"""

INFO DE LA FARMACIA — es lo ÚNICO que podés afirmar como cierto:
{FICHA}
Si te preguntan algo que NO está en esta info (servicios, obras sociales, formas de pago, delivery, horarios exactos, etc.), NO lo inventes ni supongas: decí que lo consultás con el equipo y ofrecé que hablen con una persona. Nunca afirmes que la farmacia hace algo si no está arriba."""

SYSTEM += _INFO
SYSTEM_RECETA += _INFO


def _conversar_con_tool(client, system, messages, max_vueltas=6, max_tokens=800):
    """Loop de tool use compartido: corre la conversación resolviendo las
    llamadas a buscar_producto hasta que el modelo responde texto."""
    for _ in range(max_vueltas):
        resp = client.messages.create(model=MODEL, max_tokens=max_tokens,
                                      system=system, tools=TOOLS, messages=messages)
        if resp.stop_reason != 'tool_use':
            txt = ''.join(b.text for b in resp.content
                          if getattr(b, 'type', '') == 'text').strip()
            # Defensa: limpiar Markdown de negrita (Telegram/WhatsApp lo muestran
            # literal sin parse_mode).
            return txt.replace('**', '').replace('__', '')
        messages.append({'role': 'assistant', 'content': resp.content})
        results = []
        for b in resp.content:
            if getattr(b, 'type', '') == 'tool_use' and b.name == 'buscar_producto':
                hallados = buscar_productos((b.input or {}).get('query', ''))
                results.append({'type': 'tool_result', 'tool_use_id': b.id,
                                'content': json.dumps(hallados, ensure_ascii=False)})
        messages.append({'role': 'user', 'content': results})
    return ''


def leer_receta(imagen_b64, media_type='image/jpeg'):
    """Lee una foto de receta (Claude visión), extrae los medicamentos y los
    cruza con el stock real. Devuelve el texto de respuesta para el cliente."""
    api_key = (os.environ.get('ANTHROPIC_API_KEY') or '').strip()
    if not api_key:
        return ('No puedo procesar la receta por acá ahora 🙈\n'
                'Acercate a la farmacia con la receta y te ayudamos.')
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
        messages = [{'role': 'user', 'content': [
            {'type': 'image', 'source': {'type': 'base64',
                                         'media_type': media_type, 'data': imagen_b64}},
            {'type': 'text', 'text': 'Acá va la foto de mi receta.'},
        ]}]
        texto = _conversar_con_tool(client, SYSTEM_RECETA, messages, max_tokens=800)
        return texto or ('Recibí la foto pero no pude leerla bien 😕\n'
                         'Probá con una más clara o acercate a la farmacia con la receta.')
    except Exception as e:  # noqa: BLE001
        print('leer_receta error:', e)
        return ('Tuve un problema leyendo la receta 🙏\n'
                'Probá de nuevo o acercate a la farmacia con ella.')


def consulta_ia(texto):
    api_key = (os.environ.get('ANTHROPIC_API_KEY') or '').strip()
    if not api_key:
        return ('Por ahora no puedo responder consultas libres 🙈\n'
                'Escribí "menú" y elegí una opción.')
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
        messages = [{'role': 'user', 'content': texto}]
        texto_resp = _conversar_con_tool(client, SYSTEM, messages, max_vueltas=4, max_tokens=600)
        return texto_resp or '¿Me lo reformulás? 🙂'
    except Exception as e:  # noqa: BLE001
        print('consulta_ia error:', e)
        return 'Uy, tuve un problema para responder. Probá de nuevo o escribí "menú" 🙏'
