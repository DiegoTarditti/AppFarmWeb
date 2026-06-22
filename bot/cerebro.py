"""Cerebro del bot: procesa un mensaje genérico y devuelve una respuesta
genérica. NO sabe nada de Telegram ni WhatsApp — eso lo traduce el adaptador.

El estado de conversación se persiste en la DB (bot.store), lo que habilita el
handoff (panel de operadores): si la conversación la tomó un operador
(estado='humano'), el bot NO responde.

  procesar(canal, canal_user_id, texto, imagen_b64=None, ...) → {texto, opciones} | None
"""
import os

from bot import envio, store
from bot.acciones import ACCIONES, PREFIJO_ELEGIR, seleccionar_producto
from bot.data import buscar_productos
from bot.flujo import NODO_INICIO, get_flujo
from bot.ia import leer_receta

# Palabras que siempre vuelven al menú principal.
_GLOBALES = {'menu', 'menú', 'inicio', 'hola', 'buenas', 'start', '/start'}

# Frases que indican que el cliente quiere hablar con una PERSONA (en texto libre,
# no solo con el botón). Si aparecen, derivamos de verdad (no que la IA lo diga).
_DERIVAR_FRASES = (
    'operador', 'operadora', 'pasame con', 'pásame con', 'paseme con',
    'comunicame con', 'comunicar con', 'hablar con una persona', 'hablar con alguien',
    'hablar con un humano', 'con una persona', 'con un humano', 'con alguien',
    'atencion humana', 'atención humana', 'una persona real', 'quiero un humano',
    'derivame', 'derivar', 'derivá', 'no me atiende', 'me atiende nadie',
    'atienda alguien', 'atienda una persona', 'atienda una persona',
)


def _quiere_humano(texto):
    t = ' ' + (texto or '').lower().strip() + ' '
    return any(f in t for f in _DERIVAR_FRASES)

# Auto-retorno al bot: si una conversación derivada/atendida queda sin actividad
# por más de estos minutos, el próximo mensaje del cliente la devuelve al bot.
# Default 180 (3 hs); bajalo con ATENCION_AUTO_BOT_MINUTOS (ej. 1 para probar).
AUTO_BOT_MINUTOS = float(os.environ.get('ATENCION_AUTO_BOT_MINUTOS', '180'))


def esta_con_humano(canal, canal_user_id):
    """True si la conversación está derivada o atendida por un operador (sin crearla)."""
    return store.estado_atencion_de(canal, canal_user_id) in ('cola', 'humano')


def preparar_reenganche(conv_id):
    """Arma el mensaje de re-enganche (¿Seguís ahí? Sí/No), deja la conversación
    en el nodo 'reenganche' (esperando=None → no vuelve a dispararse) y lo guarda.
    Devuelve {texto, opciones} para que el adaptador lo envíe."""
    flujo = get_flujo()
    nodo = flujo['reenganche']
    resp = {'texto': nodo['mensaje'], 'opciones': _opciones_de(nodo)}
    store.set_estado_flujo(conv_id, 'reenganche', None)
    store.guardar_mensaje(conv_id, 'bot', resp['texto'])
    return resp


def _opciones_de(nodo):
    return [o['label'] for o in nodo.get('opciones', [])]


def _render(nodo):
    return {'texto': nodo['mensaje'], 'opciones': _opciones_de(nodo)}


def _match_opcion(nodo, texto):
    """Qué opción eligió el usuario: por número (1/2/3) o por texto del botón."""
    ops = nodo.get('opciones', [])
    t = texto.strip()
    if t.isdigit():
        i = int(t) - 1
        if 0 <= i < len(ops):
            return ops[i]
    tl = t.lower()
    for op in ops:
        lab = op['label'].lower()
        if tl == lab or (len(tl) >= 3 and (tl in lab or lab in tl)):
            return op
    return None


# ── Flujo de identificación post-selección de producto ─────────────────────
# Pasos: id_confirmar_dni (si vinculado por tel) → id_pedir_dni → id_ofrecer_nombre
# → id_pedir_nombre → derivar al operador con encargar(producto_pendiente).

def _ident_iniciar(conv, producto_texto):
    """El cliente eligió un producto. Decide si pedirle confirmación del DNI
    (vínculo previo por teléfono + DNI en ficha), si pedirle el DNI desde cero,
    o si encargar directo (vinculado sin DNI conocido).

    Devuelve (resp, nuevo_nodo, nueva_esperando, derivar)."""
    cid = conv['id']
    store.set_producto_pendiente(cid, producto_texto)
    # Caso 1: vinculado y con DNI conocido → confirmar últimos 3 dígitos.
    if conv.get('cliente_id') or conv.get('cliente_observer_id'):
        info = store.dni_de_cliente_vinculado(
            cliente_id=conv.get('cliente_id'),
            cliente_observer_id=conv.get('cliente_observer_id'))
        if info:
            _dni_full, ult3, nombre = info
            texto = (f'¡Hola, {nombre}! 🙂\n'
                     f'Te tengo registrado/a con DNI terminado en ***{ult3}.\n'
                     '¿Confirmás que sos vos?')
            return ({'texto': texto,
                     'opciones': ['✅ Sí, soy yo', '❌ No, ese no soy yo'],
                     'meta': {'camino': 'identificar', 'resuelto': True,
                              'paso': 'confirmar_dni'}},
                    'id_confirmar_dni', None, False)
        # Caso 2: vinculado pero sin DNI conocido → encargar directo (ya tenemos ficha).
        return _ident_encargar(conv, motivo='vinculado_sin_dni')
    # Caso 3: no vinculado → pedir DNI desde cero.
    flujo = get_flujo()
    return ({'texto': flujo['id_pedir_dni']['mensaje'],
             'opciones': [o['label'] for o in flujo['id_pedir_dni'].get('opciones', [])],
             'meta': {'camino': 'identificar', 'resuelto': True, 'paso': 'pedir_dni'}},
            'id_pedir_dni', 'identificar_por_dni', False)


def _ident_encargar(conv, motivo='', observer_id_match=None, nombre_lead=None):
    """Cierra el flujo de identificación: vincula si corresponde, crea lead local
    si llegó un nombre, dispara `encargar(producto_pendiente)` y deriva al operador.
    Limpia `producto_pendiente`. Devuelve la tupla estándar."""
    cid = conv['id']
    producto = store.get_producto_pendiente(cid) or 'producto'
    # Vinculación por DNI (match único).
    if observer_id_match:
        try:
            store.vincular_cliente(cid, observer_id_match)
        except Exception:  # noqa: BLE001
            pass
    # Lead local opcional (nombre+apellido tipeados).
    elif nombre_lead:
        try:
            partes = nombre_lead.strip().split()
            datos = {'nombre': partes[0] if partes else nombre_lead,
                     'apellido': ' '.join(partes[1:]) if len(partes) > 1 else None,
                     'whatsapp': conv.get('canal_user_id')}
            store.crear_cliente_local(cid, datos, creado_por='bot')
        except Exception:  # noqa: BLE001
            pass
    # Disparar la acción encargar (la traemos del registro ACCIONES).
    out = ACCIONES.get('encargar', lambda t: {'texto': '📝 ¡Anotado!'})(producto)
    store.set_producto_pendiente(cid, None)
    texto = out.get('texto', '📝 ¡Anotado! Lo paso al equipo.')
    return ({'texto': texto, 'opciones': [],
             'meta': {'camino': 'encargar', 'resuelto': False,
                      'motivo': motivo or 'derivado_post_id'}},
            NODO_INICIO, None, True)


# Trigger words para que el cliente "salga" del flujo de identificación si quiere.
_SKIP_ID = {'no', 'no quiero', 'paso', 'pasa', 'cancelar', 'salir'}


def _resolver(nodo_actual, esperando, texto, imagen_b64, media_type, historial=None, conv=None):
    """Lógica PURA del flujo (sin DB). Devuelve
    (resp{texto,opciones}, nuevo_nodo, nueva_esperando, derivar).

    `historial` (turnos previos en formato Anthropic) se le pasa al nodo de IA
    para que recuerde el hilo; el resto de las acciones lo ignora."""
    flujo = get_flujo()
    # 0) Foto: análisis de receta BLOQUEADO por ahora (pedido del cliente).
    # Solo acusamos recibo y MANTENEMOS el estado actual → si estaba en un nodo
    # *_receta_pedir esperando 'consultar_producto', después tipea el producto y
    # se ejecuta la acción normal. Volver a leer_receta se reactiva sacando el
    # comentario y restaurando el return original.
    if imagen_b64:
        return ({'texto': '📸 Recibí la foto, gracias — por ahora no analizamos imágenes. Seguí contándome 👇',
                 'opciones': [],
                 'meta': {'camino': 'receta_bloqueada', 'resuelto': True}},
                nodo_actual, esperando, False)

    # 1) Comandos globales → menú principal.
    if texto.lower() in _GLOBALES:
        resp = _render(flujo[NODO_INICIO])
        resp['meta'] = {'camino': 'menu', 'resuelto': True}
        return (resp, NODO_INICIO, None, False)

    # 1.5) Pedido explícito de un humano, en cualquier estado → handoff REAL.
    # (Antes esto iba a la IA, que "decía" que derivaba sin hacerlo.)
    if _quiere_humano(texto):
        return ({'texto': flujo['derivar']['mensaje'], 'opciones': [],
                 'meta': {'camino': 'derivar', 'resuelto': False, 'motivo': 'derivado'}},
                NODO_INICIO, None, True)

    # 2) Esperando input para una acción (ej. nombre de producto).
    # 2a) Estado especial: el cliente está eligiendo de una lista numerada.
    if esperando and esperando.startswith(PREFIJO_ELEGIR):
        query = esperando[len(PREFIJO_ELEGIR):]
        out = seleccionar_producto(texto, query)
        if isinstance(out, dict):
            # Si seleccionar_producto matcheó un producto → NO derivar directo.
            # Pasar por el flujo de identificación (DNI/nombre) primero.
            meta = out.get('meta') or {}
            if meta.get('proximo_paso') == 'iniciar_id' and conv is not None:
                return _ident_iniciar(conv, meta.get('producto') or 'producto')
            nueva_esp = out.get('esperando', esperando)
            resp = {'texto': out.get('texto', ''),
                    'opciones': out.get('opciones', []),
                    'meta': {'camino': 'precio', 'resuelto': True, **meta}}
            return (resp, nodo_actual, nueva_esp, out.get('derivar', False))

    # 2b) Menú de confirmación de DNI vinculado por teléfono — interceptamos
    # porque las opciones del menú son dinámicas (no usan `va_a` normal).
    if nodo_actual == 'id_confirmar_dni' and conv is not None:
        clean = (texto or '').strip().lower()
        es_si = (clean in ('si', 'sí', 's', 'sí soy yo', 'soy yo', '1')
                 or clean.startswith('✅')
                 or 'soy yo' in clean)
        es_no = (clean in ('no', 'n', '2')
                 or clean.startswith('❌')
                 or 'no soy' in clean
                 or 'ese no' in clean)
        if es_si:
            return _ident_encargar(conv, motivo='confirmado_por_telefono')
        if es_no:
            # Desvincular y pedir DNI desde cero. El producto_pendiente queda.
            try:
                store.desvincular_cliente(conv['id'])
            except Exception:  # noqa: BLE001
                pass
            return ({'texto': flujo['id_pedir_dni']['mensaje'],
                     'opciones': [o['label'] for o in flujo['id_pedir_dni'].get('opciones', [])],
                     'meta': {'camino': 'identificar', 'resuelto': True,
                              'paso': 'pedir_dni_post_no'}},
                    'id_pedir_dni', 'identificar_por_dni', False)
        # No entendí → repreguntar.
        return ({'texto': 'Decime "sí" o "no" 🙂',
                 'opciones': ['✅ Sí, soy yo', '❌ No, ese no soy yo'],
                 'meta': {'camino': 'identificar', 'resuelto': True, 'paso': 'reintentar'}},
                'id_confirmar_dni', None, False)

    if esperando:
        accion = ACCIONES.get(esperando)
        if accion:
            out = accion(texto, historial) if esperando == 'consulta_ia' else accion(texto)
            # camino por defecto según la acción; el dict de la acción puede
            # refinar la meta (motivo sin_stock/derivado, producto).
            camino_def = {'consultar_producto': 'precio', 'encargar': 'encargar',
                          'consulta_ia': 'consulta_ia',
                          'identificar_por_dni': 'identificar',
                          'guardar_nombre_y_encargar': 'identificar'}.get(esperando, 'otro')
            # Interceptar el flujo de identificación: las acciones devuelven
            # `meta.proximo_paso` y cerebro decide el ramificado.
            if isinstance(out, dict) and (out.get('meta') or {}).get('camino') == 'identificar' and conv is not None:
                meta_id = out['meta']
                paso = meta_id.get('proximo_paso')
                if paso == 'vincular_y_encargar':
                    return _ident_encargar(conv, motivo='dni_match',
                                            observer_id_match=meta_id.get('observer_id'))
                if paso == 'derivar_sin_id':
                    return _ident_encargar(conv, motivo='sin_id')
                if paso == 'crear_local_y_encargar':
                    return _ident_encargar(conv, motivo='lead_local',
                                            nombre_lead=meta_id.get('nombre_input'))
                if paso == 'ofrecer_nombre':
                    nodo_off = flujo['id_ofrecer_nombre']
                    return ({'texto': nodo_off['mensaje'],
                             'opciones': [o['label'] for o in nodo_off.get('opciones', [])],
                             'meta': {'camino': 'identificar', 'resuelto': True,
                                      'paso': 'ofrecer_nombre'}},
                            'id_ofrecer_nombre', None, False)
                if paso == 'reintentar':
                    return ({'texto': out['texto'], 'opciones': out.get('opciones', []),
                             'meta': meta_id},
                            nodo_actual, esperando, False)
            # Una acción puede devolver un string (sigue en el mismo loop) o un
            # dict {texto, esperando, derivar, meta} para cortar el loop o derivar.
            if isinstance(out, dict):
                # esperando: solo se cambia si la acción lo dice (sin la key → loop sigue).
                nueva_esp = out['esperando'] if 'esperando' in out else esperando
                resp = {'texto': out.get('texto', ''),
                        'opciones': out.get('opciones', []),
                        'meta': {'camino': camino_def, 'resuelto': True, **(out.get('meta') or {})},
                        'ofertas_meta': out.get('ofertas_meta', [])}
                return (resp, nodo_actual, nueva_esp, out.get('derivar', False))
            return ({'texto': out, 'opciones': [],
                     'meta': {'camino': camino_def, 'resuelto': True}},
                    nodo_actual, esperando, False)

    # 3) Selección dentro del menú actual.
    nodo = flujo.get(nodo_actual, flujo[NODO_INICIO])
    op = _match_opcion(nodo, texto)
    if not op:
        if len(texto) >= 3:
            # Híbrido: primero búsqueda estructurada (con botones). Si encuentra
            # productos, devuelve la lista numerada. Si no, cae a la IA (preguntas
            # conceptuales: "algo para la tos", síntomas, consultas generales).
            busqueda = ACCIONES['consultar_producto'](texto)
            if busqueda.get('opciones'):
                return ({'texto': busqueda['texto'], 'opciones': busqueda['opciones'],
                         'meta': busqueda.get('meta', {'camino': 'precio', 'resuelto': True}),
                         'ofertas_meta': busqueda.get('ofertas_meta', [])},
                        nodo_actual, busqueda.get('esperando'), False)
            ia = ACCIONES.get('consulta_ia')
            if ia:
                out = ia(texto, historial)
                # La IA puede pedir derivar (tool derivar_a_humano) → dict con flag.
                if isinstance(out, dict):
                    return ({'texto': out.get('texto', ''), 'opciones': [],
                             'meta': out.get('meta', {'camino': 'consulta_ia', 'resuelto': True})},
                            nodo_actual, None, out.get('derivar', False))
                return ({'texto': out, 'opciones': [],
                         'meta': {'camino': 'consulta_ia', 'resuelto': True}},
                        nodo_actual, None, False)
        return ({'texto': 'No te entendí 🤔 Escribí "menú" para ver las opciones.',
                 'opciones': [], 'meta': {'camino': 'otro', 'resuelto': False,
                                          'motivo': 'no_entendido'}},
                nodo_actual, None, False)

    destino_key = op['va_a']
    # Entradas a los flujos guiados (los continúa _flujo_compra).
    if destino_key == 'compra_inicio':
        return ({'texto': '🛒 Dale. ¿Qué producto querés comprar? 👇',
                 'opciones': [], 'meta': {'camino': 'compra', 'resuelto': True}},
                'compra_producto', None, False)
    if destino_key == 'magistral_inicio':
        return ({'texto': '🧪 Fórmulas magistrales.\n¿Tenés la receta médica?',
                 'opciones': ['✅ Sí, ya la tengo', '❌ Todavía no'],
                 'meta': {'camino': 'magistral', 'resuelto': True}},
                'magistral_receta', None, False)
    destino = flujo.get(destino_key)
    if not destino:
        resp = _render(flujo[NODO_INICIO])
        resp['meta'] = {'camino': 'menu', 'resuelto': True}
        return (resp, NODO_INICIO, None, False)

    # Cualquier menu que apunte a `encargar_post_id` dispara el cierre del flujo
    # de identificación (encargar con lo que haya + derivar al operador).
    if destino_key == 'encargar_post_id' and conv is not None:
        return _ident_encargar(conv, motivo='salio_de_id')

    tipo = destino['tipo']
    if tipo == 'menu':
        resp = _render(destino)
        resp['meta'] = {'camino': 'menu', 'resuelto': True}
        return (resp, destino_key, None, False)
    if tipo == 'pedir_input':
        # Navegó a un nodo que pide input; el resultado real llega en el próximo mensaje.
        return ({'texto': destino['mensaje'], 'opciones': [],
                 'meta': {'camino': 'menu', 'resuelto': True}},
                destino_key, destino['accion'], False)
    # 'texto' (hoja) → mostrar y volver al menú. Si es "derivar", marca handoff.
    # Al derivar NO mostramos el menú: el cliente queda esperando al operador.
    derivar = (destino_key == 'derivar')
    opciones = [] if derivar else _opciones_de(flujo[NODO_INICIO])
    meta = ({'camino': 'derivar', 'resuelto': False, 'motivo': 'derivado'} if derivar
            else {'camino': destino_key if destino_key in ('horarios', 'receta') else 'menu',
                  'resuelto': True})
    return ({'texto': destino['mensaje'], 'opciones': opciones, 'meta': meta},
            NODO_INICIO, None, derivar)


def _fmt_monto(m):
    return f'${m:,.0f}'.replace(',', '.')


# ── Flujo de envío + libreta de domicilios ───────────────────────────────────
_EMOJI_ET = {'casa': '🏠', 'trabajo': '🏢', 'otro': '🏡'}
_ET_OPCIONES = ['🏠 Casa', '🏢 Trabajo', '🏡 Otro', 'No guardar']


def _es_trigger_envio(texto):
    t = (texto or '').strip().lower()
    return 'costo de env' in t or t in ('envio', 'envío', 'envios', 'envíos')


def _es_otra_direccion(texto):
    t = (texto or '').strip().lower()
    return 'otra direcc' in t or t == 'otra' or '➕' in (texto or '')


def _es_opcion_menu(texto):
    """True si el texto es exactamente una opción del menú principal (tocaron un
    botón), para no tomarlo como dirección dentro del flujo de envío."""
    flujo = get_flujo()
    t = (texto or '').strip().lower()
    return any(t == o['label'].lower() for o in flujo[NODO_INICIO]['opciones'])


def _label_domicilio(d):
    et = d.get('etiqueta') or 'Otro'
    emoji = _EMOJI_ET.get(et.lower(), '📍')
    return f"{emoji} {et} — {d.get('direccion') or 'ubicación 📍'}"[:60]


def _match_domicilio(doms, texto):
    t = (texto or '').strip().lower()
    if t.isdigit():
        i = int(t) - 1
        return doms[i] if 0 <= i < len(doms) else None
    for d in doms:                                   # por dirección (más específico)
        dir_ = (d.get('direccion') or '').lower()
        if dir_ and dir_[:18] in t:
            return d
    for d in doms:                                   # por etiqueta
        et = (d.get('etiqueta') or '').lower()
        if et and et in t:
            return d
    return None


def _cotizar_dom(d):
    if d.get('lat') is not None and d.get('lng') is not None:
        return envio.cotizar_por_coords(d['lat'], d['lng'])
    if d.get('direccion'):
        return envio.cotizar_por_direccion(d['direccion'], localidad=d.get('localidad'))
    return {'monto': None, 'detalle': 'sin datos'}


def _texto_cotizacion(r, destino=''):
    a = f"a {destino} " if destino else ""
    if r.get('monto') is not None:
        det = f" ({r['detalle']})" if r.get('detalle') else ''
        return f"🛵 El envío {a}sale {_fmt_monto(r['monto'])}{det}.\nPara coordinarlo escribí \"operador\" 🙂"
    return (f"No pude calcular el envío {a}automáticamente 🙈\n"
            "Escribí \"operador\" y el equipo te pasa el costo.")


def _ubicacion_guardada_txt(d):
    """Cómo mostrarle al cliente la ubicación guardada: la dirección si la
    escribió, o un link a Google Maps si fue un pin."""
    if d.get('direccion'):
        return d['direccion']
    if d.get('lat') is not None and d.get('lng') is not None:
        return f"https://maps.google.com/?q={d['lat']},{d['lng']}"
    return ''


_CONF_OPCIONES = ['✅ Sí, es esa', '📍 No, es otra']


def _resp_proponer_dom(d):
    """Propone un domicilio guardado: muestra costo + ubicación y pide que el
    cliente CONFIRME que el envío va ahí."""
    r = _cotizar_dom(d)
    if r.get('monto') is not None:
        det = f" ({r['detalle']})" if r.get('detalle') else ''
        txt = f"🛵 El envío a {d['etiqueta']} sale {_fmt_monto(r['monto'])}{det}."
    else:
        txt = f"📦 Envío a {d['etiqueta']}."
    loc = _ubicacion_guardada_txt(d)
    if loc:
        txt += f"\n📍 Ubicación guardada: {loc}"
    txt += "\n\n¿Confirmás que el envío es a esta ubicación?"
    return {'texto': txt, 'opciones': _CONF_OPCIONES}


def _es_confirmacion(texto):
    t = (texto or '').strip().lower()
    return ('✅' in (texto or '') or t.startswith('si') or t.startswith('sí')
            or 'es esa' in t or 'confirm' in t)


def _envio_confirmar(cid, esperando, texto):
    """Paso final: el cliente acepta (o rechaza) la ubicación propuesta."""
    if _es_confirmacion(texto):
        dom_id = None
        if esperando and esperando.startswith('dom:'):
            try:
                dom_id = int(esperando.split(':', 1)[1])
            except ValueError:
                dom_id = None
        d = store.get_domicilio(dom_id) if dom_id else None
        if dom_id:
            store.marcar_uso_domicilio(dom_id)
        et = d['etiqueta'] if d else 'tu domicilio'
        store.set_estado_flujo(cid, NODO_INICIO, None)
        return {'texto': (f'¡Perfecto! 🛵 Coordinamos el envío a {et}.\n'
                          'Escribí "operador" y el equipo lo finaliza 🙂'),
                'opciones': _menu_ops()}
    return _envio_preguntar_destino(cid)        # "No, es otra" → volver a ofrecer


def _menu_ops():
    flujo = get_flujo()
    return _opciones_de(flujo[NODO_INICIO])


def _envio_preguntar_destino(cid):
    doms = store.listar_domicilios(cid)
    if doms:
        store.set_estado_flujo(cid, 'envio_elegir', None)
        return {'texto': '🛵 ¿A dónde te lo llevamos?',
                'opciones': [_label_domicilio(d) for d in doms] + ['➕ Otra dirección']}
    store.set_estado_flujo(cid, 'envio_pedir', None)
    return {'texto': ('🛵 Calculemos tu envío.\n'
                      'Podés escribirme el domicilio (calle y número) '
                      'o mandarme tu ubicación 📍 desde el clip de adjuntos.'),
            'opciones': []}


def _envio_capturar(cid, ubicacion=None, direccion=None):
    """Cotiza lo que llegó (pin o dirección), guarda el domicilio (sin etiqueta
    aún) y pide la etiqueta."""
    if ubicacion:
        r = envio.cotizar_por_coords(ubicacion.get('lat'), ubicacion.get('lng'))
        dom = store.guardar_domicilio(cid, lat=ubicacion.get('lat'),
                                      lng=ubicacion.get('lng'), origen='pin')
    else:
        coords = envio.geocodificar(direccion)
        if not coords:                     # no geocodificó → no guardar basura, re-pedir
            store.set_estado_flujo(cid, 'envio_pedir', None)
            return {'texto': ('No pude ubicar esa dirección 🙈\n'
                              'Probá escribiéndola como "Pellegrini 1234" o mandame tu ubicación 📍.'),
                    'opciones': []}
        r = envio.cotizar_por_coords(*coords)
        dom = store.guardar_domicilio(cid, direccion=direccion, lat=coords[0],
                                      lng=coords[1], origen='direccion')
    store.set_estado_flujo(cid, 'envio_guardar', f"dom:{dom['id']}")
    cab = (f"🛵 El envío sale {_fmt_monto(r['monto'])}"
           f"{(' (' + r['detalle'] + ')') if r.get('detalle') else ''}.\n\n"
           if r.get('monto') is not None else "Recibí tu ubicación 📍.\n\n")
    return {'texto': cab + '¿Lo guardo para la próxima? Elegí:', 'opciones': _ET_OPCIONES}


def _envio_guardar_etiqueta(cid, esperando, texto):
    dom_id = None
    if esperando and esperando.startswith('dom:'):
        try:
            dom_id = int(esperando.split(':', 1)[1])
        except ValueError:
            dom_id = None
    t = (texto or '').strip().lower()
    store.set_estado_flujo(cid, NODO_INICIO, None)
    if dom_id and t.startswith('no'):
        store.eliminar_domicilio(dom_id)
        return {'texto': 'Listo, no lo guardé 👍', 'opciones': _menu_ops()}
    etiqueta = 'Casa' if 'casa' in t else 'Trabajo' if 'trabajo' in t else 'Otro'
    if dom_id:
        store.set_etiqueta_domicilio(dom_id, etiqueta)
    return {'texto': f'¡Guardado como {etiqueta}! 🎉 La próxima te lo ofrezco directo.',
            'opciones': _menu_ops()}


def _flujo_envio(cid, nodo, esperando, texto, ubicacion):
    """Maneja todo el sub-flujo de envío. Devuelve la respuesta o None si el
    mensaje no es parte del flujo (para que siga el flujo normal)."""
    # Comandos para escapar siempre ganan (no quedar atrapado en el flujo).
    if texto.lower() in _GLOBALES or _quiere_humano(texto):
        return None

    en_flujo = nodo.startswith('envio_')

    # Tocar "Costo de envío" en cualquier momento (re)inicia el flujo.
    if _es_trigger_envio(texto) and not ubicacion:
        return _envio_preguntar_destino(cid)

    # Tocar OTRA opción del menú estando en el flujo → salir (la maneja el flujo
    # normal). Evita que un botón del menú se tome como "dirección".
    if en_flujo and not ubicacion and _es_opcion_menu(texto):
        store.set_estado_flujo(cid, NODO_INICIO, None)
        return None

    # Elegir entre domicilios guardados → proponer ubicación + pedir confirmación.
    if nodo == 'envio_elegir' and not ubicacion:
        doms = store.listar_domicilios(cid)
        sel = _match_domicilio(doms, texto)
        if sel:
            store.set_estado_flujo(cid, 'envio_confirmar', f"dom:{sel['id']}")
            return _resp_proponer_dom(sel)
        if _es_otra_direccion(texto):
            store.set_estado_flujo(cid, 'envio_pedir', None)
            return {'texto': 'Dale 👇 Escribime el domicilio (calle y número) o mandame tu ubicación 📍.',
                    'opciones': []}
        return _envio_preguntar_destino(cid)        # no entendí → re-preguntar

    # Confirmar la ubicación propuesta (Sí, es esa / No, es otra).
    if nodo == 'envio_confirmar' and not ubicacion:
        return _envio_confirmar(cid, esperando, texto)

    # Capturar ubicación/dirección nueva.
    if (nodo in ('envio_pedir', 'envio_elegir') and ubicacion) or \
            (nodo == 'envio_pedir' and texto):
        return _envio_capturar(cid, ubicacion=ubicacion,
                               direccion=None if ubicacion else texto)

    # Elegir etiqueta de lo recién capturado.
    if nodo == 'envio_guardar' and not ubicacion:
        return _envio_guardar_etiqueta(cid, esperando, texto)

    # Pin fuera del flujo (Fase 2): cotiza y ofrece guardarlo.
    if ubicacion and ubicacion.get('lat') is not None:
        return _envio_capturar(cid, ubicacion=ubicacion)

    return None


# ── Flujo guiado de Compra Farmacia + Fórmulas Magistrales ───────────────────
# Eje: el PRODUCTO. Buscamos stock → si hay seguimos, si no ofrecemos encargo.
# Después juntamos obra social (opcional) y receta (opcional) y derivamos al
# operador con el resumen (notas de sistema). Magistral es un flujo aparte.

def _dijo_si(texto):
    t = (texto or '').strip().lower()
    return ('✅' in (texto or '') or t in ('si', 'sí', 's', '1', 'sip', 'dale', 'ok')
            or t.startswith(('si ', 'sí ')) or 'tengo' in t or 'quiero' in t or 'encarg' in t)


def _dijo_no(texto):
    t = (texto or '').strip().lower()
    return ('❌' in (texto or '') or t in ('no', 'n', '2', 'nop', 'paso')
            or t.startswith('no ') or 'todavia no' in t or 'todavía no' in t or 'gracias' in t)


def _es_omitir(texto):
    t = (texto or '').strip().lower()
    return ('omitir' in t or 'saltar' in t or 'seguir' in t or 'sin foto' in t
            or t in ('-', 'skip', 'no se', 'no sé'))


def _precio_txt(p):
    return f'${p:,.0f}'.replace(',', '.') if p else 's/precio'


def _flujo_compra(cid, nodo, esperando, texto, imagen_b64):
    """Máquina de estados de Compra Farmacia / Magistral. Devuelve la respuesta
    o None si el mensaje no es parte del flujo (sigue el flujo normal)."""
    if (texto or '').lower() in _GLOBALES or _quiere_humano(texto):
        return None
    if not (nodo.startswith('compra_') or nodo.startswith('magistral_')):
        return None
    # Tocó otra opción del menú estando en el flujo → salir.
    if _es_opcion_menu(texto):
        store.set_estado_flujo(cid, NODO_INICIO, None)
        return None

    # ── Compra ──
    if nodo == 'compra_producto':
        return _compra_buscar(cid, texto)
    if nodo == 'compra_encargo':
        if _dijo_si(texto):
            store.nota_sistema(cid, '🛒 COMPRA — pidió ENCARGAR el producto.')
            return _compra_preguntar_os(cid)
        store.set_estado_flujo(cid, NODO_INICIO, None)
        return {'texto': 'Dale, no hay problema 🙂 ¿Algo más? Escribí "menú".', 'opciones': _menu_ops()}
    if nodo == 'compra_os':
        if _dijo_si(texto):
            store.set_estado_flujo(cid, 'compra_os_cual', None)
            return {'texto': '🏥 ¿Cuál es tu obra social? (podés tocar "Omitir")', 'opciones': ['Omitir']}
        store.nota_sistema(cid, '🏥 Obra social: NO usa.')
        return _compra_preguntar_receta(cid)
    if nodo == 'compra_os_cual':
        if not _es_omitir(texto):
            store.nota_sistema(cid, f'🏥 Obra social: {texto.strip()[:60]}')
        return _compra_preguntar_receta(cid)
    if nodo == 'compra_receta':
        if _dijo_si(texto):
            store.set_estado_flujo(cid, 'compra_receta_foto', None)
            return {'texto': '📸 Mandá la foto de la receta (o tocá "Seguir sin foto").',
                    'opciones': ['Seguir sin foto']}
        store.nota_sistema(cid, '📋 Receta: NO la tiene.')
        return _compra_cierre(cid, sin_receta=True)
    if nodo == 'compra_receta_foto':
        if imagen_b64:
            store.nota_sistema(cid, '📋 Receta: 📸 envió foto.')
        elif _es_omitir(texto):
            store.nota_sistema(cid, '📋 Receta: la tiene, sigue sin foto.')
        else:
            store.nota_sistema(cid, f'📋 Receta: {texto.strip()[:60]}')
        return _compra_cierre(cid)

    # ── Magistral ──
    if nodo == 'magistral_receta':
        if _dijo_si(texto):
            store.set_estado_flujo(cid, 'magistral_foto', None)
            return {'texto': '📸 Mandá la foto de la receta (o tocá "Seguir sin foto").',
                    'opciones': ['Seguir sin foto']}
        store.nota_sistema(cid, '🧪 MAGISTRAL — sin receta.')
        store.set_estado_flujo(cid, 'magistral_preparar', None)
        return {'texto': '⚠️ Para prepararla vas a tener que traer la receta original.\n\n'
                         '¿Qué necesitás preparar? 👇', 'opciones': []}
    if nodo == 'magistral_foto':
        if imagen_b64:
            store.nota_sistema(cid, '🧪 MAGISTRAL — 📸 envió receta.')
        store.set_estado_flujo(cid, 'magistral_preparar', None)
        return {'texto': '¿Qué necesitás preparar? 👇', 'opciones': []}
    if nodo == 'magistral_preparar':
        store.nota_sistema(cid, f'🧪 MAGISTRAL — preparar: {(texto or "").strip()[:120]}')
        store.set_estado_flujo(cid, NODO_INICIO, None)
        store.set_atencion(cid, 'cola')
        return {'texto': '¡Listo! 🧪 Te paso con el equipo para coordinar la preparación 🙂', 'opciones': []}
    return None


def _compra_buscar(cid, texto):
    if len((texto or '').strip()) < 3:
        return {'texto': 'Escribime el nombre del producto (al menos 3 letras) 🙂', 'opciones': []}
    rows = buscar_productos(texto, limite=4)
    if not rows:
        return {'texto': f'No encontré "{texto.strip()}" 🤔 Probá con otro nombre, o escribí "menú".',
                'opciones': []}
    store.set_producto_pendiente(cid, texto.strip())
    con_stock = next((r for r in rows if r['stock'] > 0), None)
    if con_stock:
        store.nota_sistema(cid, f'🛒 COMPRA — quiere: {texto.strip()} | EN STOCK: '
                                f'{con_stock["producto"]} ({con_stock["stock"]}u, {_precio_txt(con_stock["precio"])})')
        return _compra_preguntar_os(
            cid, intro=f'✅ Tengo {con_stock["producto"]} — {_precio_txt(con_stock["precio"])} '
                       f'({con_stock["stock"]}u).\n\n')
    store.nota_sistema(cid, f'🛒 COMPRA — quiere: {texto.strip()} | SIN STOCK')
    store.set_estado_flujo(cid, 'compra_encargo', None)
    return {'texto': f'❌ No tengo stock de "{texto.strip()}" ahora.\n¿Querés que te lo encarguemos?',
            'opciones': ['✅ Sí, encargámelo', '❌ No, gracias']}


def _compra_preguntar_os(cid, intro=''):
    store.set_estado_flujo(cid, 'compra_os', None)
    return {'texto': intro + '¿Vas a usar obra social? 🏥', 'opciones': ['✅ Sí', '❌ No']}


def _compra_preguntar_receta(cid):
    store.set_estado_flujo(cid, 'compra_receta', None)
    return {'texto': '¿Tenés la receta médica? 📋', 'opciones': ['✅ Sí', '❌ No']}


def _compra_cierre(cid, sin_receta=False):
    store.set_estado_flujo(cid, NODO_INICIO, None)
    store.set_atencion(cid, 'cola')
    extra = '\n(Acordate de traer la receta original para retirar.)' if sin_receta else ''
    return {'texto': '¡Genial! 🛒 Te paso con el equipo para concretar la compra '
                     '(precio final con cobertura, pago y entrega).' + extra, 'opciones': []}


def procesar(canal, canal_user_id, texto, imagen_b64=None,
             media_type='image/jpeg', nombre=None, linea=None, ubicacion=None):
    texto = (texto or '').strip()
    conv = store.get_conversacion(canal, canal_user_id, nombre, linea)
    cid = conv['id']

    # Auto-retorno: si estaba derivada/atendida pero sin actividad por mucho
    # tiempo, la devolvemos al bot (así ningún cliente queda huérfano).
    if conv['estado_atencion'] in ('cola', 'humano') and \
            store.revisar_inactividad(cid, AUTO_BOT_MINUTOS):
        conv['estado_atencion'] = 'bot'

    # Guardar el mensaje entrante. Si es una ubicación, persistimos las coords
    # en formato parseable "📍 lat,lng" para que el frontend de /atencion las
    # detecte y rendee una burbuja especial con link al mapa (Diego 2026-06-21).
    if texto:
        entrante = texto
    elif imagen_b64:
        entrante = '[imagen recibida]'
    elif ubicacion and ubicacion.get('lat') is not None:
        entrante = f'📍 {ubicacion["lat"]:.6f},{ubicacion["lng"]:.6f}'
    else:
        entrante = ''
    store.guardar_mensaje(cid, 'cliente', entrante, tiene_imagen=bool(imagen_b64))

    # Si ya está derivada (en cola) o la tomó un operador (humano), el bot NO
    # responde: el cliente espera a la persona, no queremos meter el menú en el
    # medio. Los mensajes igual se guardan arriba para que el operador los vea.
    if conv['estado_atencion'] in ('cola', 'humano'):
        return None

    # Flujo de envío (libreta de domicilios + cotización). Maneja el pin de
    # ubicación, la opción "Costo de envío" y sus pasos. Si no aplica, sigue.
    resp_envio = _flujo_envio(cid, conv['nodo'] or NODO_INICIO,
                              conv['esperando'], texto, ubicacion)
    if resp_envio is not None:
        if resp_envio.get('texto'):
            store.guardar_mensaje(cid, 'bot', resp_envio['texto'])
        return resp_envio

    # Flujo guiado de Compra Farmacia / Magistral (máquina de estados compra_*).
    resp_compra = _flujo_compra(cid, conv['nodo'] or NODO_INICIO,
                                conv['esperando'], texto, imagen_b64)
    if resp_compra is not None:
        if resp_compra.get('texto'):
            store.guardar_mensaje(cid, 'bot', resp_compra['texto'])
        return resp_compra

    # Historial (incluye el mensaje recién guardado) para que el nodo de IA
    # recuerde el hilo de la conversación.
    historial = store.get_historial_ia(cid)
    resp, nuevo_nodo, nueva_esperando, derivar = _resolver(
        conv['nodo'], conv['esperando'], texto, imagen_b64, media_type, historial, conv=conv)

    store.set_estado_flujo(cid, nuevo_nodo, nueva_esperando)
    if derivar:
        store.set_atencion(cid, 'cola')
    # Guardar mensajes del sistema con ofertas detectadas por el bot
    ofertas = (resp or {}).get('ofertas_meta') or []
    for o in ofertas:
        import json
        store.nota_sistema(cid, 'OFERTA|' + json.dumps({'id': o['id'], 'observer_id': o['observer_id'],
                                'descripcion': o['descripcion'], 'tipo': o['tipo'],
                                'valor': o['valor']}))
    if resp and resp.get('texto'):
        store.guardar_mensaje(cid, 'bot', resp['texto'])

    # Analítica: registrar la interacción con su camino/motivo (lo que el bot
    # resolvió o no). Solo acá — el bot efectivamente procesó; nunca en el
    # return None de cola/humano (ahí responde una persona, no el bot).
    meta = (resp or {}).get('meta') or {}
    store.registrar_interaccion(
        conv_id=cid, canal=canal, linea=linea, texto=texto,
        camino=meta.get('camino', 'otro'), resuelto=meta.get('resuelto', True),
        motivo=meta.get('motivo'), tema=meta.get('tema'), producto=meta.get('producto'))
    return resp
