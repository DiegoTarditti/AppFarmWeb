"""Definición del flujo conversacional.

Cada nodo tiene un `tipo` que el cerebro sabe procesar:

  - 'menu'        → muestra `mensaje` + `opciones` (botones). Cada opción
                    lleva a otro nodo (`va_a`).
  - 'texto'       → muestra `mensaje` y vuelve al inicio (hoja informativa).
  - 'pedir_input' → muestra `mensaje` y espera el próximo mensaje del usuario,
                    que se pasa a la `accion` indicada.

Los flujos guiados de **Compra Farmacia** y **Fórmulas Magistrales** NO viven acá:
son máquinas de estado en `bot/cerebro.py` (`_flujo_compra`), igual que el envío.
El menú solo dispara la entrada (`va_a` = 'compra_inicio' / 'magistral_inicio'),
que el cerebro intercepta.
"""

NODO_INICIO = 'inicio'

_BASE_FLUJO = {
    'inicio': {
        'tipo': 'menu',
        'mensaje': (
            '¡Hola! 👋 Soy el asistente de Farmacia Badia (Rosario).\n'
            '¿En qué te puedo ayudar?'
        ),
        'opciones': [
            {'label': '🔎 Consultar Precio / Stock', 'va_a': 'consultar_producto'},
            {'label': '🛒 Compra Farmacia', 'va_a': 'compra_inicio'},
            {'label': '🧪 Fórmulas Magistrales', 'va_a': 'magistral_inicio'},
            {'label': '🙋 Hablar con un operador', 'va_a': 'derivar'},
            {'label': '📦 Consultar estado de pedido', 'va_a': 'estado_pedido'},
            {'label': '💉 Vacunatorio', 'va_a': 'vacunatorio'},
        ],
    },
    # Consultar Precio/Stock: UN solo paso — pide el medicamento, devuelve precio+stock.
    'consultar_producto': {
        'tipo': 'pedir_input',
        'mensaje': '🔎 Decime el medicamento que querés consultar 👇\n(ej: "ibuprofeno 600", "amoxidal")',
        'accion': 'consultar_producto',
    },

    # === Identificación post-selección de producto =============================
    # Se entra ACÁ desde `seleccionar_producto` cuando matchea un producto.
    'id_confirmar_dni': {
        'tipo': 'menu',
        'mensaje': 'Para agilizarte, ¿podés confirmarme tu identidad?',
        'opciones': [
            {'label': '✅ Sí, soy yo', 'va_a': 'inicio'},   # placeholder; cerebro lo intercepta
            {'label': '❌ No',         'va_a': 'id_pedir_dni'},
        ],
    },
    'id_pedir_dni': {
        'tipo': 'pedir_input',
        'mensaje': (
            '👤 Para agilizarte, ¿me das tu DNI?\n'
            '(Solo número, sin puntos. Ej: 30123456.)\n\n'
            'Si preferís, tocá *No, paso al equipo* y te derivo directo.'
        ),
        'accion': 'identificar_por_dni',
        'opciones': [
            {'label': '❌ No, paso al equipo', 'va_a': 'encargar_post_id'},
        ],
    },
    'id_ofrecer_nombre': {
        'tipo': 'menu',
        'mensaje': (
            '🔎 No te encuentro con ese DNI.\n'
            '¿Querés dejar tu nombre y apellido así el operador te ubica más rápido? '
            'Es opcional.'
        ),
        'opciones': [
            {'label': '✅ Sí, dejo mi nombre',  'va_a': 'id_pedir_nombre'},
            {'label': '❌ No, pasame al equipo', 'va_a': 'encargar_post_id'},
        ],
    },
    'id_pedir_nombre': {
        'tipo': 'pedir_input',
        'mensaje': '👤 Decime tu nombre y apellido 👇',
        'accion': 'guardar_nombre_y_encargar',
    },
    'encargar_post_id': {
        'tipo': 'texto',
        'mensaje': 'Listo, te paso al equipo 🙂',
    },

    # === Estado de pedido: placeholder ===
    'estado_pedido': {
        'tipo': 'texto',
        'mensaje': (
            '📦 Para consultar tu pedido, mandame por acá:\n'
            '• Tu nombre o teléfono, o\n'
            '• El número de pedido.\n\n'
            'En un ratito te paso el estado 🙂'
        ),
    },
    # === Vacunatorio: placeholder ===
    'vacunatorio': {
        'tipo': 'texto',
        'mensaje': (
            '💉 Vacunatorio — Farmacia Badia.\n'
            'Decime qué vacuna necesitás y cuándo te queda bien y te ayudamos a coordinar el turno.'
        ),
    },
    'encargar': {
        'tipo': 'pedir_input',
        'mensaje': '¿Qué querés encargar? Decime el producto y la cantidad 👇\n(ej: "2 cajas de amoxidal 500")',
        'accion': 'encargar',
    },
    'consulta_ia': {
        'tipo': 'pedir_input',
        'mensaje': 'Contame qué necesitás y te ayudo 🙂\n(ej: "algo para la tos", "tenés protector solar?")',
        'accion': 'consulta_ia',
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
    # Re-enganche proactivo (lo dispara el bot tras inactividad a mitad de flujo).
    'reenganche': {
        'tipo': 'menu',
        'mensaje': '¿Seguís ahí? ¿Querés avanzar con tu consulta?',
        'opciones': [
            {'label': 'Sí', 'va_a': 'inicio'},
            {'label': 'No', 'va_a': 'despedida'},
        ],
    },
    'despedida': {
        'tipo': 'texto',
        'mensaje': '¡Dale! Cuando quieras seguir, escribime y te ayudo 🙂',
    },
}

FLUJO = _BASE_FLUJO

SECCIONES = {
    'BIENVENIDA': 'inicio',
    'CONSULTAR_PRODUCTO': 'consultar_producto',
    'ENCARGAR': 'encargar',
    'ID_CONFIRMAR_DNI': 'id_confirmar_dni',
    'ID_PEDIR_DNI': 'id_pedir_dni',
    'ID_OFRECER_NOMBRE': 'id_ofrecer_nombre',
    'ID_PEDIR_NOMBRE': 'id_pedir_nombre',
    'ENCARGAR_POST_ID': 'encargar_post_id',
    'CONSULTA_IA': 'consulta_ia',           # preservado, sin link en el menú actual
    'HORARIOS': 'horarios',                 # preservado, sin link en el menú actual
    'DERIVAR': 'derivar',
    'ESTADO_PEDIDO': 'estado_pedido',
    'VACUNATORIO': 'vacunatorio',
    'REENGANCHE': 'reenganche',
    'DESPEDIDA': 'despedida',
}


def get_flujo():
    """Devuelve el flujo actual, aplicando overrides de flujo_data.json si existe."""
    import json as _json
    import os as _os
    base = dict(_BASE_FLUJO)
    path = _os.path.join(_os.path.dirname(__file__), 'flujo_data.json')
    try:
        if _os.path.exists(path):
            overrides = _json.loads(open(path, encoding='utf-8').read())
            for nodo_id, mensaje in overrides.items():
                if nodo_id in base:
                    base[nodo_id] = dict(base[nodo_id])
                    base[nodo_id]['mensaje'] = mensaje
    except Exception:
        pass
    return base
