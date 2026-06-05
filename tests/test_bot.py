"""Tests del bot asistente: lógica pura del flujo + persistencia + handoff.

No tocan la IA (Claude) ni `product_analytics` (Postgres): los caminos cubiertos
son los deterministas. El único punto que consulta stock (encargar) se mockea.
"""
import bot.acciones
from bot import audio, cerebro, store
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


def test_audio_sin_motor_no_transcribe(monkeypatch):
    # Sin Whisper local (forzado) y sin OPENAI_API_KEY → degrada a None.
    # (No cargamos el modelo real para no bajar ~150 MB en los tests.)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.setattr(audio, '_modelo_local', lambda: None)
    assert audio.transcribir(b'fake-bytes') is None


def test_cliente_vincular_buscar_y_ficha():
    import database
    with database.get_db() as s:
        s.add(database.ObsCliente(observer_id=5001, apellido_nombre='Gomez, Ana',
                                  documento_numero=27123456, telefono='3415551234',
                                  id_farmacia=1))
        s.commit()
    assert any(c['observer_id'] == 5001 for c in store.buscar_clientes('gomez'))
    assert store.buscar_clientes('27123456')[0]['observer_id'] == 5001

    conv = store.get_conversacion('telegram', 'clitest', nombre='Ana')
    assert store.vincular_cliente(conv['id'], 5001)['ok']
    full = store.get_conversacion_full(conv['id'])
    assert full['cliente_observer_id'] == 5001

    f = store.get_ficha_cliente(5001)
    assert f['nombre'] == 'Gomez, Ana' and '27123456' in f['documento']
    assert store.guardar_ficha_local(5001, notas='alérgico a penicilina', tags='vip')['ok']
    assert store.get_ficha_cliente(5001)['notas'] == 'alérgico a penicilina'

    assert store.desvincular_cliente(conv['id'])['ok']
    assert store.get_conversacion_full(conv['id'])['cliente_observer_id'] is None


def test_alta_cliente_local():
    conv = store.get_conversacion('telegram', 'altatest', nombre='Nuevo')
    assert store.crear_cliente_local(conv['id'], {
        'nombre': 'Marcos', 'apellido': 'Gimenez',
        'dni': '35888999', 'domicilio': 'San Martín 456'})['ok']
    f = store.get_ficha_de_conversacion(conv['id'])
    assert f['fuente'] == 'local'
    assert f['nombre'] == 'Gimenez, Marcos' and f['documento'] == '35888999'
    assert store.guardar_notas_conversacion(conv['id'], 'lead del bot')['ok']
    assert store.get_ficha_de_conversacion(conv['id'])['notas'] == 'lead del bot'
    assert store.desvincular_cliente(conv['id'])['ok']
    assert store.get_ficha_de_conversacion(conv['id']) is None


def test_reenganche_a_mitad_de_flujo():
    import datetime as _dt

    import database
    conv = store.get_conversacion('telegram', 'reengtest', nombre='T')
    cid = conv['id']
    store.set_estado_flujo(cid, 'encargar', 'encargar')   # a mitad de un flujo
    with database.get_db() as s:
        c = s.get(database.BotConversacion, cid)
        c.ultimo_en = database.now_ar() - _dt.timedelta(minutes=5)
        s.commit()
    assert any(p['id'] == cid for p in store.conversaciones_para_reenganche(1))
    resp = cerebro.preparar_reenganche(cid)
    assert resp['opciones'] == ['Sí', 'No']
    # tras re-enganchar, esperando=None → no vuelve a dispararse
    assert not any(p['id'] == cid for p in store.conversaciones_para_reenganche(1))


def test_procesar_guarda_mensaje_entrante_aunque_este_en_humano():
    conv = store.get_conversacion('telegram', '444', nombre='C')
    store.set_atencion(conv['id'], 'humano')
    procesar('telegram', '444', 'una pregunta para el humano')
    textos = [m['texto'] for m in store.get_mensajes(conv['id'])]
    assert 'una pregunta para el humano' in textos
