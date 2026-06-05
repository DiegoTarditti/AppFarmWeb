"""Cerebro del bot: procesa un mensaje genérico y devuelve una respuesta
genérica. NO sabe nada de Telegram ni WhatsApp — eso lo traduce el adaptador.

El estado de conversación se persiste en la DB (bot.store), lo que habilita el
handoff (panel de operadores): si la conversación la tomó un operador
(estado='humano'), el bot NO responde.

  procesar(canal, canal_user_id, texto, imagen_b64=None, ...) → {texto, opciones} | None
"""
import os

from bot import store
from bot.acciones import ACCIONES
from bot.flujo import FLUJO, NODO_INICIO
from bot.ia import leer_receta

# Palabras que siempre vuelven al menú principal.
_GLOBALES = {'menu', 'menú', 'inicio', 'hola', 'buenas', 'start', '/start'}

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


def _resolver(nodo_actual, esperando, texto, imagen_b64, media_type):
    """Lógica PURA del flujo (sin DB). Devuelve
    (resp{texto,opciones}, nuevo_nodo, nueva_esperando, derivar)."""
    # 0) Foto (receta): la procesa la IA visión, sin importar el nodo.
    if imagen_b64:
        return ({'texto': leer_receta(imagen_b64, media_type),
                 'opciones': _opciones_de(FLUJO[NODO_INICIO])}, NODO_INICIO, None, False)

    # 1) Comandos globales → menú principal.
    if texto.lower() in _GLOBALES:
        return (_render(FLUJO[NODO_INICIO]), NODO_INICIO, None, False)

    # 2) Esperando input para una acción (ej. nombre de producto).
    if esperando:
        accion = ACCIONES.get(esperando)
        if accion:
            out = accion(texto)
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
            return ({'texto': ia(texto), 'opciones': []}, nodo_actual, None, False)
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


def procesar(canal, canal_user_id, texto, imagen_b64=None,
             media_type='image/jpeg', nombre=None, linea=None):
    texto = (texto or '').strip()
    conv = store.get_conversacion(canal, canal_user_id, nombre, linea)
    cid = conv['id']

    # Auto-retorno: si estaba derivada/atendida pero sin actividad por mucho
    # tiempo, la devolvemos al bot (así ningún cliente queda huérfano).
    if conv['estado_atencion'] in ('cola', 'humano') and \
            store.revisar_inactividad(cid, AUTO_BOT_MINUTOS):
        conv['estado_atencion'] = 'bot'

    # Guardar el mensaje entrante.
    store.guardar_mensaje(cid, 'cliente',
                          texto or ('[imagen recibida]' if imagen_b64 else ''),
                          tiene_imagen=bool(imagen_b64))

    # Si ya está derivada (en cola) o la tomó un operador (humano), el bot NO
    # responde: el cliente espera a la persona, no queremos meter el menú en el
    # medio. Los mensajes igual se guardan arriba para que el operador los vea.
    if conv['estado_atencion'] in ('cola', 'humano'):
        return None

    resp, nuevo_nodo, nueva_esperando, derivar = _resolver(
        conv['nodo'], conv['esperando'], texto, imagen_b64, media_type)

    store.set_estado_flujo(cid, nuevo_nodo, nueva_esperando)
    if derivar:
        store.set_atencion(cid, 'cola')
    if resp and resp.get('texto'):
        store.guardar_mensaje(cid, 'bot', resp['texto'])
    return resp
