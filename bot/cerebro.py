"""Cerebro del bot: procesa un mensaje genérico y devuelve una respuesta
genérica. NO sabe nada de Telegram ni WhatsApp — eso lo traduce el adaptador.

El estado de conversación se persiste en la DB (bot.store), lo que habilita el
handoff (panel de operadores): si la conversación la tomó un operador
(estado='humano'), el bot NO responde.

  procesar(canal, canal_user_id, texto, imagen_b64=None, ...) → {texto, opciones} | None
"""
import os

from bot import envio, store
from bot.acciones import ACCIONES
from bot.flujo import FLUJO, NODO_INICIO
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
    nodo = FLUJO['reenganche']
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


def _resolver(nodo_actual, esperando, texto, imagen_b64, media_type, historial=None):
    """Lógica PURA del flujo (sin DB). Devuelve
    (resp{texto,opciones}, nuevo_nodo, nueva_esperando, derivar).

    `historial` (turnos previos en formato Anthropic) se le pasa al nodo de IA
    para que recuerde el hilo; el resto de las acciones lo ignora."""
    # 0) Foto (receta): la procesa la IA visión, sin importar el nodo.
    if imagen_b64:
        return ({'texto': leer_receta(imagen_b64, media_type),
                 'opciones': _opciones_de(FLUJO[NODO_INICIO])}, NODO_INICIO, None, False)

    # 1) Comandos globales → menú principal.
    if texto.lower() in _GLOBALES:
        return (_render(FLUJO[NODO_INICIO]), NODO_INICIO, None, False)

    # 1.5) Pedido explícito de un humano, en cualquier estado → handoff REAL.
    # (Antes esto iba a la IA, que "decía" que derivaba sin hacerlo.)
    if _quiere_humano(texto):
        return ({'texto': FLUJO['derivar']['mensaje'], 'opciones': []},
                NODO_INICIO, None, True)

    # 2) Esperando input para una acción (ej. nombre de producto).
    if esperando:
        accion = ACCIONES.get(esperando)
        if accion:
            out = accion(texto, historial) if esperando == 'consulta_ia' else accion(texto)
            # Una acción puede devolver un string (sigue en el mismo loop) o un
            # dict {texto, esperando, derivar} para cortar el loop o derivar.
            if isinstance(out, dict):
                return ({'texto': out['texto'], 'opciones': out.get('opciones', [])},
                        nodo_actual, out.get('esperando'), out.get('derivar', False))
            return ({'texto': out, 'opciones': []}, nodo_actual, esperando, False)

    # 3) Selección dentro del menú actual.
    nodo = FLUJO.get(nodo_actual, FLUJO[NODO_INICIO])
    op = _match_opcion(nodo, texto)
    if not op:
        # Híbrido: texto libre → IA (en vez de "no te entendí"). NO re-pegamos el
        # menú: es una charla, los botones del saludo siguen visibles más arriba.
        ia = ACCIONES.get('consulta_ia')
        if ia and len(texto) >= 3:
            return ({'texto': ia(texto, historial), 'opciones': []}, nodo_actual, None, False)
        return ({'texto': 'No te entendí 🤔 Escribí "menú" para ver las opciones.',
                 'opciones': []}, nodo_actual, None, False)

    destino_key = op['va_a']
    destino = FLUJO.get(destino_key)
    if not destino:
        return (_render(FLUJO[NODO_INICIO]), NODO_INICIO, None, False)

    tipo = destino['tipo']
    if tipo == 'menu':
        return (_render(destino), destino_key, None, False)
    if tipo == 'pedir_input':
        return ({'texto': destino['mensaje'], 'opciones': []}, destino_key, destino['accion'], False)
    # 'texto' (hoja) → mostrar y volver al menú. Si es "derivar", marca handoff.
    # Al derivar NO mostramos el menú: el cliente queda esperando al operador.
    derivar = (destino_key == 'derivar')
    opciones = [] if derivar else _opciones_de(FLUJO[NODO_INICIO])
    return ({'texto': destino['mensaje'], 'opciones': opciones}, NODO_INICIO, None, derivar)


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
    t = (texto or '').strip().lower()
    return any(t == o['label'].lower() for o in FLUJO[NODO_INICIO]['opciones'])


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
    return _opciones_de(FLUJO[NODO_INICIO])


def _envio_preguntar_destino(cid):
    doms = store.listar_domicilios(cid)
    if doms:
        store.set_estado_flujo(cid, 'envio_elegir', None)
        return {'texto': '🛵 ¿A dónde te lo llevamos?',
                'opciones': [_label_domicilio(d) for d in doms] + ['➕ Otra dirección']}
    store.set_estado_flujo(cid, 'envio_pedir', None)
    return {'texto': ('🛵 Calculemos tu envío.\nCompartime tu ubicación 📍 '
                      '(adjuntar → Ubicación) o escribí la dirección (calle y número).'),
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
            return {'texto': ('No pude ubicar esa dirección 🙈. Probá con calle y '
                              'número, o compartime tu ubicación 📍.'), 'opciones': []}
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
            return {'texto': 'Dale 👇 Compartime tu ubicación 📍 o escribí la dirección.',
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

    # Guardar el mensaje entrante.
    entrante = texto or ('[imagen recibida]' if imagen_b64
                         else ('[ubicación recibida]' if ubicacion else ''))
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

    # Historial (incluye el mensaje recién guardado) para que el nodo de IA
    # recuerde el hilo de la conversación.
    historial = store.get_historial_ia(cid)
    resp, nuevo_nodo, nueva_esperando, derivar = _resolver(
        conv['nodo'], conv['esperando'], texto, imagen_b64, media_type, historial)

    store.set_estado_flujo(cid, nuevo_nodo, nueva_esperando)
    if derivar:
        store.set_atencion(cid, 'cola')
    if resp and resp.get('texto'):
        store.guardar_mensaje(cid, 'bot', resp['texto'])
    return resp
