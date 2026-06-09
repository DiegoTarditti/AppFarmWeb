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

_BASE_FLUJO = {
    'inicio': {
        'tipo': 'menu',
        'mensaje': (
            '¡Hola! 👋 Soy el asistente de Farmacia Badia (Rosario).\n'
            '¿En qué te puedo ayudar?'
        ),
        'opciones': [
            {'label': '🔎 Consultar Precio / Stock', 'va_a': 'consultar_precio_menu'},
            {'label': '🛒 Compra Farmacia', 'va_a': 'compra_farmacia'},
            {'label': '🙋 Hablar con un operador', 'va_a': 'derivar'},
            {'label': '📦 Consultar estado de pedido', 'va_a': 'estado_pedido'},
            {'label': '💉 Vacunatorio', 'va_a': 'vacunatorio'},
        ],
    },
    # === Submenú de "Consultar Precio / Stock" — 4 modalidades. Todas usan la
    #     acción `consultar_producto` (existente). Cuando definamos comportamientos
    #     distintos por modalidad, cambiamos `accion` a una nueva (o agregamos contexto). ===
    'consultar_precio_menu': {
        'tipo': 'menu',
        'mensaje': '🔎 ¿Bajo qué modalidad?',
        'opciones': [
            {'label': '👤 Particular',           'va_a': 'consultar_particular'},
            {'label': '🏥 Obra Social',          'va_a': 'consultar_os'},
            {'label': '🧪 Fórmulas Magistrales', 'va_a': 'consultar_magistral'},
            {'label': '⚡ Retira Express',       'va_a': 'consultar_express'},
        ],
    },
    'consultar_particular': {
        'tipo': 'pedir_input',
        'mensaje': '👤 Particular — decime el nombre del producto 👇\n(ej: "ibuprofeno 600", "amoxidal")',
        'accion': 'consultar_producto',
    },
    # Obra Social y Fórmulas Magistrales: primero preguntamos por la receta.
    # Si la tiene → la mandamos como foto (la guardamos sin analizar) y seguimos.
    # Si no la tiene → le avisamos que tiene que presentarla y seguimos igual.
    'consultar_os': {
        'tipo': 'menu',
        'mensaje': '🏥 Obra Social — ¿Tenés la receta médica?',
        'opciones': [
            {'label': '✅ Sí, ya la tengo',  'va_a': 'os_receta_pedir'},
            {'label': '❌ Todavía no',       'va_a': 'os_sin_receta'},
        ],
    },
    'os_receta_pedir': {
        'tipo': 'pedir_input',
        'mensaje': (
            '📸 Mandame la foto de la receta. La guardamos sin analizarla por ahora.\n'
            'Después (o en el mismo mensaje) decime el nombre del producto 👇'
        ),
        'accion': 'consultar_producto',
    },
    'os_sin_receta': {
        'tipo': 'pedir_input',
        'mensaje': (
            '⚠️ Para retirar con cobertura de tu obra social vas a tener que **presentar la receta original** en el local.\n'
            'Mientras tanto te paso el precio/stock — decime el nombre del producto 👇'
        ),
        'accion': 'consultar_producto',
    },
    'consultar_magistral': {
        'tipo': 'menu',
        'mensaje': '🧪 Fórmulas Magistrales — ¿Tenés la receta médica?',
        'opciones': [
            {'label': '✅ Sí, ya la tengo',  'va_a': 'magistral_receta_pedir'},
            {'label': '❌ Todavía no',       'va_a': 'magistral_sin_receta'},
        ],
    },
    'magistral_receta_pedir': {
        'tipo': 'pedir_input',
        'mensaje': (
            '📸 Mandame la foto de la receta. La guardamos sin analizarla por ahora.\n'
            'Después (o en el mismo mensaje) contame qué necesitás preparar 👇'
        ),
        'accion': 'consultar_producto',
    },
    'magistral_sin_receta': {
        'tipo': 'pedir_input',
        'mensaje': (
            '⚠️ Para preparar una fórmula magistral vas a tener que **presentar la receta original** en el local.\n'
            'Mientras tanto contame qué necesitás preparar 👇'
        ),
        'accion': 'consultar_producto',
    },
    'consultar_express': {
        'tipo': 'pedir_input',
        'mensaje': '⚡ Retira Express — decime el producto que querés retirar rápido.',
        'accion': 'consultar_producto',
    },

    # === Compra Farmacia: submenú PROVISIONAL (a confirmar con Diego) ===
    # Por ahora junta los flujos viejos de compra (encargar/receta/envio). Esperando
    # el submenú definitivo del cliente.
    # "Encargar" se sacó como entrada separada — hoy el flujo natural es
    # consultar precio/stock → elegir → derivar (mismo destino). Compra Farmacia
    # queda para receta y envío. Más adelante puede convertirse en el "checkout"
    # post-selección de producto.
    'compra_farmacia': {
        'tipo': 'menu',
        'mensaje': '🛒 ¿Qué necesitás?',
        'opciones': [
            {'label': '📸 Enviar una receta', 'va_a': 'receta'},
            {'label': '🛵 Costo de envío', 'va_a': 'envio'},
        ],
    },
    # === Identificación post-selección de producto =============================
    # Se entra ACÁ desde `seleccionar_producto` cuando matchea un producto.
    # Si la conv ya está vinculada y el cliente tiene DNI → `id_confirmar_dni`
    # (el mensaje y opciones los arma `seleccionar_producto` al vuelo con el nombre
    # y los últimos 3 dígitos; el procesamiento del Sí/No lo maneja cerebro).
    # Si NO está vinculado → `id_pedir_dni`.
    'id_confirmar_dni': {
        'tipo': 'menu',
        # mensaje + opciones reales se inyectan dinámicamente desde la acción —
        # estos son placeholder/fallback por si se entra al nodo sin contexto.
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
        # Botones rápidos en WhatsApp; en Telegram funciona el texto también.
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
    # Nodo "puente" para derivar al operador con lo que haya
    # (lo dispara cualquier "No, paso al equipo"). El cerebro intercepta el
    # nodo y dispara `encargar(producto_pendiente)` con los datos disponibles.
    'encargar_post_id': {
        'tipo': 'texto',
        'mensaje': 'Listo, te paso al equipo 🙂',
    },

    # === Estado de pedido: placeholder. Cuando esté la action, pasar a pedir_input. ===
    'estado_pedido': {
        'tipo': 'texto',
        'mensaje': (
            '📦 Para consultar tu pedido, mandame por acá:\n'
            '• Tu nombre o teléfono, o\n'
            '• El número de pedido.\n\n'
            'En un ratito te paso el estado 🙂'
        ),
    },
    # === Vacunatorio: placeholder. Esperando contenido / submenú definitivo. ===
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
    # Re-enganche proactivo: el bot lo manda solo cuando el cliente queda a
    # mitad de un flujo (ver bot/cerebro.preparar_reenganche).
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
    'COMPRA_FARMACIA': 'compra_farmacia',
    'ENCARGAR': 'encargar',
    'CONSULTAR_PRECIO_MENU':  'consultar_precio_menu',
    'CONSULTAR_PARTICULAR':   'consultar_particular',
    'CONSULTAR_OS':           'consultar_os',
    'OS_RECETA_PEDIR':        'os_receta_pedir',
    'OS_SIN_RECETA':          'os_sin_receta',
    'CONSULTAR_MAGISTRAL':    'consultar_magistral',
    'MAGISTRAL_RECETA_PEDIR': 'magistral_receta_pedir',
    'MAGISTRAL_SIN_RECETA':   'magistral_sin_receta',
    'ID_CONFIRMAR_DNI':       'id_confirmar_dni',
    'ID_PEDIR_DNI':           'id_pedir_dni',
    'ID_OFRECER_NOMBRE':      'id_ofrecer_nombre',
    'ID_PEDIR_NOMBRE':        'id_pedir_nombre',
    'ENCARGAR_POST_ID':       'encargar_post_id',
    'CONSULTAR_EXPRESS':      'consultar_express',
    'CONSULTAR_PRODUCTO': 'consultar_producto',
    'CONSULTA_IA': 'consulta_ia',           # preservado, sin link en el menú actual
    'RECETA': 'receta',
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
