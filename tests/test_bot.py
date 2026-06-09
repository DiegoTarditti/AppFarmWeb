"""Tests del bot asistente: lógica pura del flujo + persistencia + handoff.

No tocan la IA (Claude) ni `product_analytics` (Postgres): los caminos cubiertos
son los deterministas. El único punto que consulta stock (encargar) se mockea.
"""
import bot.acciones
from bot import audio, caja, cerebro, store
from bot.cerebro import _match_opcion, _resolver, procesar
from bot.flujo import FLUJO, NODO_INICIO

IMG = 'image/jpeg'


# ── Selección de opciones ────────────────────────────────────────────────────

def test_match_opcion_por_numero():
    # Opción 1 del menú principal: "Consultar Precio / Stock" → submenú de modalidades.
    assert _match_opcion(FLUJO[NODO_INICIO], '1')['va_a'] == 'consultar_precio_menu'


def test_match_opcion_por_label_parcial():
    assert _match_opcion(FLUJO[NODO_INICIO], 'vacunatorio')['va_a'] == 'vacunatorio'


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


def test_pide_humano_en_texto_libre_deriva():
    # "Pásame con operador" (texto libre, no botón) debe derivar de verdad.
    resp, nodo, esp, deriv = _resolver(NODO_INICIO, None, 'Pásame con operador', None, IMG)
    assert deriv is True and resp['opciones'] == []
    # incluso a mitad de un flujo (esperando) el pedido de humano gana.
    resp2, _, _, deriv2 = _resolver('encargar', 'encargar', 'no me atiende nadie', None, IMG)
    assert deriv2 is True


def test_quiere_humano_sin_falsos_positivos():
    from bot.cerebro import _quiere_humano
    assert _quiere_humano('pasame con operador')
    assert _quiere_humano('quiero hablar con una persona')
    assert not _quiere_humano('necesito ibuprofeno')
    assert not _quiere_humano('algo para una persona con diabetes')


def test_vacunatorio_es_hoja_y_vuelve_al_inicio():
    # "Vacunatorio" reemplazó a "Horarios y dirección" como hoja texto accesible
    # desde el menú principal (los horarios siguen vivos como nodo huérfano, sin link directo).
    resp, nodo, esp, deriv = _resolver(NODO_INICIO, None, 'Vacunatorio', None, IMG)
    assert nodo == NODO_INICIO and not deriv
    assert 'Vacunatorio' in resp['texto'] or 'vacuna' in resp['texto'].lower()


def test_consultar_modalidad_entra_en_pedir_input():
    # Antes había "Encargar un producto" como entrada directa a un pedir_input desde inicio;
    # ahora el patrón equivalente es: submenú Consultar Precio/Stock → elegir modalidad
    # entra en un pedir_input con acción consultar_producto.
    resp, nodo, esp, deriv = _resolver('consultar_precio_menu', None, 'Particular', None, IMG)
    assert nodo == 'consultar_particular' and esp == 'consultar_producto' and not deriv


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


def test_bandeja_incluye_todas_las_conversaciones():
    # Ahora la bandeja muestra TODO (incluido lo que maneja solo el bot), para
    # poder supervisar/intervenir. El panel filtra por pestaña.
    solo_bot = store.get_conversacion('telegram', 'b1', nombre='Bot')
    en_cola = store.get_conversacion('telegram', 'b2', nombre='Cola')
    store.set_atencion(en_cola['id'], 'cola')
    ids = [c['id'] for c in store.listar_conversaciones()]
    assert en_cola['id'] in ids and solo_bot['id'] in ids


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


def test_cliente_unificado_id_y_ticket_hereda():
    """Tabla única: vincular dos veces al mismo observer NO duplica la fila
    clientes (get-or-create), y el ticket de caja hereda el cliente_id del chat."""
    import database
    with database.get_db() as s:
        s.add(database.ObsCliente(observer_id=5002, apellido_nombre='Pinto, Eva',
                                  id_farmacia=1))
        s.commit()
    conv = store.get_conversacion('telegram', 'unif1', nombre='Eva')
    assert store.vincular_cliente(conv['id'], 5002)['ok']
    assert store.vincular_cliente(conv['id'], 5002)['ok']   # idempotente
    with database.get_db() as s:
        assert s.query(database.Cliente).filter_by(observer_id=5002).count() == 1
        cid = s.get(database.BotConversacion, conv['id']).cliente_id
        assert cid is not None
    r = caja.crear_ticket(conv['id'], [{'nombre': 'X', 'precio': 10, 'cantidad': 1}],
                          operador_id=None, cliente_nombre='Eva')
    assert r['ok']
    with database.get_db() as s:
        assert s.get(database.TicketCaja, r['id']).cliente_id == cid


def test_domicilio_por_cliente_unificado():
    """El domicilio (pin) guardado para un cliente vinculado se lee por su
    cliente_id, venga la consulta por observer_id o por cliente_id."""
    import database
    with database.get_db() as s:
        s.add(database.ObsCliente(observer_id=5003, apellido_nombre='Ruiz, Leo',
                                  id_farmacia=1))
        s.commit()
    conv = store.get_conversacion('telegram', 'dom1', nombre='Leo')
    store.vincular_cliente(conv['id'], 5003)
    assert store.guardar_domicilio(conv['id'], etiqueta='Casa',
                                   direccion='Mitre 100', lat=-32.9, lng=-60.6)['ok']
    doms = store.listar_domicilios_de_cliente(observer_id=5003)
    assert len(doms) == 1 and doms[0]['direccion'] == 'Mitre 100'
    assert doms[0]['lat'] == -32.9


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


def test_caja_ticket_flujo():
    conv = store.get_conversacion('telegram', 'cajatest', nombre='C')
    r = caja.crear_ticket(conv['id'], [
        {'nombre': 'Ibupirac', 'precio': 100, 'cantidad': 2},
        {'nombre': 'Notos', 'precio': 50, 'cantidad': 1},
    ], operador_id=None, cliente_nombre='C')
    assert r['ok'] and r['total'] == 250
    tid = r['id']
    assert any(t['id'] == tid for t in caja.listar_tickets())
    assert caja.cobrar_ticket(tid, 'Efectivo', None)['ok']
    t = next(x for x in caja.listar_tickets() if x['id'] == tid)
    assert t['estado'] == 'cobrado' and t['forma_pago'] == 'Efectivo'
    caja.entregar_ticket(tid)
    assert not any(t['id'] == tid for t in caja.listar_tickets())   # sale de la cola


def test_caja_formas_pago_seed():
    nombres = [f['nombre'] for f in caja.listar_formas_pago()]
    assert 'Efectivo' in nombres and len(nombres) >= 3


def test_historial_ia_alterna_roles_y_colapsa_consecutivos():
    conv = store.get_conversacion('telegram', 'h1', nombre='Hist')
    cid = conv['id']
    # Dos del cliente seguidos (p.ej. tras un handoff) → se colapsan en un 'user'.
    store.guardar_mensaje(cid, 'cliente', 'hola')
    store.guardar_mensaje(cid, 'cliente', 'tenés ibuprofeno?')
    store.guardar_mensaje(cid, 'bot', 'sí, tengo')
    store.guardar_mensaje(cid, 'operador', 'no debe aparecer')
    h = store.get_historial_ia(cid)
    assert [m['role'] for m in h] == ['user', 'assistant']   # alterna, sin operador
    assert h[0]['content'] == 'hola\ntenés ibuprofeno?'        # colapsado
    assert h[0]['role'] == 'user'                             # arranca en user


def test_historial_ia_descarta_assistant_inicial():
    conv = store.get_conversacion('telegram', 'h2', nombre='Hist2')
    cid = conv['id']
    store.guardar_mensaje(cid, 'bot', 'menú de bienvenida')   # arranca con bot
    store.guardar_mensaje(cid, 'cliente', 'algo para la tos')
    h = store.get_historial_ia(cid)
    assert h and h[0]['role'] == 'user'   # se descarta el assistant del arranque


def test_procesar_guarda_mensaje_entrante_aunque_este_en_humano():
    conv = store.get_conversacion('telegram', '444', nombre='C')
    store.set_atencion(conv['id'], 'humano')
    procesar('telegram', '444', 'una pregunta para el humano')
    textos = [m['texto'] for m in store.get_mensajes(conv['id'])]
    assert 'una pregunta para el humano' in textos


# ── Analítica: memoria de no-resueltos (bot_interacciones) ───────────────────

def test_resolver_marca_sin_stock_y_preserva_loop(monkeypatch):
    # Producto no encontrado → meta sin_stock, y NO corta el loop de búsqueda.
    monkeypatch.setattr(bot.acciones, 'buscar_productos', lambda *a, **k: [])
    resp, nodo, esp, deriv = _resolver('consultar_producto', 'consultar_producto',
                                       'xyzzy', None, IMG)
    assert resp['meta']['motivo'] == 'sin_stock'
    assert resp['meta']['producto'] == 'xyzzy' and resp['meta']['resuelto'] is False
    assert esp == 'consultar_producto' and not deriv   # loop preservado


def test_resolver_no_entendido_y_derivado_meta():
    r1, *_ = _resolver(NODO_INICIO, None, 'aa', None, IMG)        # texto corto, sin match
    assert r1['meta']['motivo'] == 'no_entendido' and r1['meta']['resuelto'] is False
    r2, _, _, deriv = _resolver(NODO_INICIO, None, 'Hablar con una persona', None, IMG)
    assert deriv and r2['meta']['motivo'] == 'derivado'


def test_procesar_registra_interaccion():
    procesar('telegram', 'int1', 'hola', nombre='X', linea='Telegram')
    filas = store.listar_interacciones()
    assert any(f['camino'] == 'menu' and f['linea'] == 'Telegram' for f in filas)


def test_procesar_en_humano_no_registra_interaccion():
    conv = store.get_conversacion('telegram', 'h9', nombre='C')
    store.set_atencion(conv['id'], 'humano')
    procesar('telegram', 'h9', 'hola igual')
    filas = store.listar_interacciones()
    assert all(f['conversacion_id'] != conv['id'] for f in filas)


def test_listar_interacciones_y_resumen_del_dia():
    conv = store.get_conversacion('telegram', 'r1', nombre='C', linea='Telegram')
    cid = conv['id']
    store.registrar_interaccion(cid, 'telegram', 'Telegram', 'ibuprofeno',
                                'precio', False, 'sin_stock', None, 'ibuprofeno')
    store.registrar_interaccion(cid, 'telegram', 'Telegram', 'gracias', 'menu', True)
    assert len(store.listar_interacciones()) == 2
    solo_no = store.listar_interacciones(solo_no_resueltas=True)
    assert len(solo_no) == 1 and solo_no[0]['motivo'] == 'sin_stock'
    r = store.resumen_del_dia()
    assert r['total'] == 2 and r['no_resueltas'] == 1
    assert r['por_motivo'].get('sin_stock') == 1
    assert r['productos_sin_stock'][0]['producto'] == 'ibuprofeno'


def test_panel_no_resueltos_renderiza(client):
    r = client.get('/bot/no-resueltos')
    assert r.status_code == 200
    assert b'Memoria de no-resueltos' in r.data


def test_api_resumen_y_lista_json(client):
    rr = client.get('/bot/no-resueltos/api/resumen')
    assert rr.status_code == 200
    d = rr.get_json()
    assert 'resumen' in d and 'texto' in d
    rl = client.get('/bot/no-resueltos/api/lista?solo_no_resueltas=1')
    assert rl.status_code == 200
    assert 'interacciones' in rl.get_json()
