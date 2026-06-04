"""Definición del flujo conversacional (Fase 0).

Estructura de datos editable a mano por ahora; en la Fase 1 esto se mueve a la
DB + una UI de edición. Cada nodo tiene un `tipo` que el cerebro sabe procesar:

  - 'menu'        → muestra `mensaje` + `opciones` (botones). Cada opción
                    lleva a otro nodo (`va_a`).
  - 'texto'       → muestra `mensaje` y vuelve al inicio (hoja informativa).
  - 'pedir_input' → muestra `mensaje` y espera el próximo mensaje del usuario,
                    que se pasa a la `accion` indicada.

Replica el menú de Central Oeste, acotado para el MVP.
"""

NODO_INICIO = 'inicio'

FLUJO = {
    'inicio': {
        'tipo': 'menu',
        'mensaje': (
            '¡Hola! 👋 Soy el asistente de Farmacia Badia (Rosario).\n'
            '¿En qué te puedo ayudar?'
        ),
        'opciones': [
            {'label': '🔎 Consultar un producto', 'va_a': 'consultar_producto'},
            {'label': '🛒 Encargar un producto', 'va_a': 'encargar'},
            {'label': '💬 Hacé tu consulta (IA)', 'va_a': 'consulta_ia'},
            {'label': '📸 Enviar una receta', 'va_a': 'receta'},
            {'label': '🕐 Horarios y dirección', 'va_a': 'horarios'},
            {'label': '🙋 Hablar con una persona', 'va_a': 'derivar'},
        ],
    },
    'encargar': {
        'tipo': 'pedir_input',
        'mensaje': '¿Qué querés encargar? Decime el producto y la cantidad 👇\n(ej: "2 cajas de amoxidal 500")',
        'accion': 'encargar',
    },
    'consultar_producto': {
        'tipo': 'pedir_input',
        'mensaje': 'Decime el nombre del producto que buscás 👇\n(ej: "ibuprofeno 600", "amoxidal")',
        'accion': 'consultar_producto',
    },
    'consulta_ia': {
        'tipo': 'pedir_input',
        'mensaje': 'Contame qué necesitás y te ayudo 🙂\n(ej: "algo para la tos", "tenés protector solar?")',
        'accion': 'consulta_ia',
    },
    'receta': {
        'tipo': 'texto',
        'mensaje': (
            '📸 Sacale una foto clara a tu receta y mandámela por acá.\n'
            'Te digo enseguida qué tenemos en stock 🙂\n'
            '(Para retirar igual hay que traer la receta original en papel.)'
        ),
    },
    'horarios': {
        'tipo': 'texto',
        'mensaje': (
            '🕐 Horarios\n'
            'Lunes a Sábado de 8 a 22 hs\n'
            'Domingos de 9 a 13 hs\n\n'
            '📍 Donado y Córdoba, Rosario'
        ),
    },
    'derivar': {
        'tipo': 'texto',
        'mensaje': (
            '🙋 Te derivo con una persona del equipo.\n'
            'En un ratito te responden por acá. ¡Gracias!'
        ),
    },
}
