"""Tests del bot asistente: lógica pura del flujo + persistencia + handoff.

No tocan la IA (Claude) ni `product_analytics` (Postgres): los caminos cubiertos
son los deterministas. El único punto que consulta stock (encargar) se mockea.
"""
import bot.acciones
from bot import store
from bot.cerebro import _match_opcion, _resolver, procesar
from bot.flujo import FLUJO, NODO_INICIO

IMG = 'image/jpeg'


# ── Selección de opciones ────────────────────────────────────────────────────

def test_match_opcion_por_numero():
    assert _match_opcion(FLUJO[NODO_INICIO], '1')['va_a'] == 'consultar_producto'


def test_match_opcion_por_label_parcial():
    assert _match_opcion(FLUJO[NODO_INICIO], 'horarios')['va_a'] == 'horarios'


def test_match_opcion_invalida_devuelve_none():
    assert _match_opcion(FLUJO[NODO_INICIO], '99') is None


# ── Flujo (lógica pura, sin DB ni IA) ────────────────────────────────────────

def test_saludo_muestra_menu_completo():
    resp, nodo, esp, deriv = _resolver(NODO_INICIO, None, 'hola', None, IMG)
    assert nodo == NODO_INICIO and not deriv
    assert len(resp['opciones']) == len(FLUJO[NODO_INICIO]['opciones'])


def test_derivar_marca_handoff_y_no_repega_menu():
    resp, nodo, esp, deriv = _resolver(NODO_INICIO, None, 'Hablar con una persona', None, IMG)
    assert deriv is True
    assert resp['opciones'] == []   # al derivar no volvemos a mostrar el menú


def test_horarios_es_hoja_y_vuelve_al_inicio():
    resp, nodo, esp, deriv = _resolver(NODO_INICIO, None, 'Horarios y dirección', None, IMG)
    assert nodo == NODO_INICIO and not deriv
    assert 'Donado' in resp['texto']


def test_encargar_entra_en_pedir_input():
    resp, nodo, esp, deriv = _resolver(NODO_INICIO, None, 'Encargar un producto', None, IMG)
    assert nodo == 'encargar' and esp == 'encargar' and not deriv


def test_encargar_captura_y_deriva(monkeypatch):
    monkeypatch.setattr(bot.acciones, 'buscar_productos', lambda *a, **k: [])
    resp, nodo, esp, deriv = _resolver('encargar', 'encargar', '2 cajas de amoxidal', None, IMG)
    assert deriv is True          # cae en la bandeja del operador
    assert esp is None            # corta el loop de captura
    assert 'Anotado' in resp['texto']


def test_encargar_texto_corto_pide_mas(monkeypatch):
    monkeypatch.setattr(bot.acciones, 'buscar_productos', lambda *a, **k: [])
    resp, nodo, esp, deriv = _resolver('encargar', 'encargar', 'x', None, IMG)
    assert not deriv and esp == 'encargar'   # sigue esperando, no deriva


# ── Persistencia (DB SQLite del conftest) ────────────────────────────────────

def test_store_crea_y_guarda_mensajes():
    conv = store.get_conversacion('telegram', '111', nombre='Ana', linea='Telegram')
    assert conv['estado_atencion'] == 'bot'
    store.guardar_mensaje(conv['id'], 'cliente', 'hola')
    store.guardar_mensaje(conv['id'], 'bot', 'buenas')
    assert [m['origen'] for m in store.get_mensajes(conv['id'])] == ['cliente', 'bot']


def test_get_conversacion_es_idempotente():
    a = store.get_conversacion('telegram', '222', nombre='Z')
    b = store.get_conversacion('telegram', '222')
    assert a['id'] == b['id']


def test_bandeja_solo_muestra_cola_y_humano():
    solo_bot = store.get_conversacion('telegram', 'b1', nombre='Bot')
    en_cola = store.get_conversacion('telegram', 'b2', nombre='Cola')
    store.set_atencion(en_cola['id'], 'cola')
    ids = [c['id'] for c in store.listar_conversaciones()]
    assert en_cola['id'] in ids and solo_bot['id'] not in ids


# ── Handoff end-to-end (procesar) ────────────────────────────────────────────

def test_procesar_deriva_y_silencia_al_bot():
    procesar('telegram', '333', 'hola', nombre='Cliente')
    procesar('telegram', '333', 'Hablar con una persona')
    conv = store.get_conversacion('telegram', '333')
    assert conv['estado_atencion'] == 'cola'

    # El operador la toma → el bot no responde más en ese chat.
    store.set_atencion(conv['id'], 'humano', operador_user_id=None)
    assert procesar('telegram', '333', 'sigo escribiendo') is None


def test_procesar_guarda_mensaje_entrante_aunque_este_en_humano():
    conv = store.get_conversacion('telegram', '444', nombre='C')
    store.set_atencion(conv['id'], 'humano')
    procesar('telegram', '444', 'una pregunta para el humano')
    textos = [m['texto'] for m in store.get_mensajes(conv['id'])]
    assert 'una pregunta para el humano' in textos
