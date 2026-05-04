"""Tests para el flujo de pedidos emitidos: recepción, operadores."""

import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin

import database
from database import PedidoEmitido, PedidoEmitidoItem, Provider, UsuarioPedido


# ---------------------------------------------------------------------------
# App fixture con routes.compras_dia registrado
# ---------------------------------------------------------------------------

@pytest.fixture(scope='module')
def app(init_test_db, tmp_path_factory):
    _app = Flask(__name__, template_folder='../templates')
    _app.secret_key = 'test-pedido-emitido'
    _app.config['TESTING'] = True
    _app.config['UPLOAD_FOLDER'] = str(tmp_path_factory.mktemp('uploads'))

    class _Anon:
        is_authenticated = False
        nombre_completo = None
        username = None
        rol = None

    _app.jinja_env.globals.update({
        'current_user': _Anon(),
        'tiene_permiso': lambda *a, **k: False,
        'url_for': lambda e, **kw: '#',
        'entorno': type('E', (), {'codigo': 'test', 'label': 'Test', 'color': '#888'})(),
    })

    lm = LoginManager(_app)

    class _User(UserMixin):
        id = '1'
        username = 'test'
        rol = 'dev'
        nombre_completo = 'Test'
        debe_cambiar_password = False

    @lm.user_loader
    def _load(uid):
        return _User()

    @lm.request_loader
    def _req_load(req):
        return _User()

    import routes.compras_dia as _cd
    _cd.init_app(_app)

    return _app


@pytest.fixture
def client(app):
    with app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _crear_drogueria(session):
    prov = Provider(razon_social='Droguería Test', match_strategy='barcode')
    session.add(prov)
    session.flush()
    return prov


def _crear_pedido_emitido(session, n_items=3):
    prov = _crear_drogueria(session)
    p = PedidoEmitido(
        drogueria_id=prov.id,
        usuario='test',
        emitido_por='Test',
        total_items=n_items,
        total_unidades=n_items * 2,
        estado='ABIERTO',
    )
    session.add(p)
    session.flush()
    for i in range(n_items):
        session.add(PedidoEmitidoItem(
            pedido_id=p.id,
            descripcion=f'Producto {i}',
            lab_nombre='Lab Test',
            cantidad_pedida=5,
        ))
    session.commit()
    return p


# ---------------------------------------------------------------------------
# Tests — operadores (UsuarioPedido)
# ---------------------------------------------------------------------------

class TestUsuariosPedidos:

    def test_get_lista_vacia(self, client):
        r = client.get('/api/usuarios-pedidos')
        assert r.status_code == 200
        data = r.get_json()
        assert data['ok'] is True
        assert data['users'] == []

    def test_post_crear_operador(self, client):
        r = client.post('/api/usuarios-pedidos',
                        json={'nombre': 'Diego'},
                        content_type='application/json')
        assert r.status_code == 200
        data = r.get_json()
        assert data['ok'] is True
        assert data['nombre'] == 'Diego'

    def test_get_lista_con_operador(self, client):
        s = database.SessionLocal()
        try:
            s.add(UsuarioPedido(nombre='Lisandro'))
            s.commit()
        finally:
            s.close()

        r = client.get('/api/usuarios-pedidos')
        data = r.get_json()
        assert any(u['nombre'] == 'Lisandro' for u in data['users'])

    def test_post_nombre_duplicado_activo(self, client):
        client.post('/api/usuarios-pedidos', json={'nombre': 'Ana'})
        r = client.post('/api/usuarios-pedidos', json={'nombre': 'Ana'})
        assert r.status_code == 400
        assert 'Ya existe' in r.get_json()['error']

    def test_post_reactivar_soft_deleted(self, client):
        s = database.SessionLocal()
        try:
            s.add(UsuarioPedido(nombre='Carlos', activo=False))
            s.commit()
        finally:
            s.close()

        # Re-agregar Carlos (estaba soft-deleted) debe reactivarlo
        r = client.post('/api/usuarios-pedidos', json={'nombre': 'Carlos'})
        assert r.status_code == 200
        assert r.get_json()['ok'] is True

        # Debe aparecer en la lista
        r2 = client.get('/api/usuarios-pedidos')
        assert any(u['nombre'] == 'Carlos' for u in r2.get_json()['users'])

    def test_delete_soft(self, client):
        s = database.SessionLocal()
        try:
            u = UsuarioPedido(nombre='Maria')
            s.add(u)
            s.commit()
            uid = u.id
        finally:
            s.close()

        r = client.delete(f'/api/usuarios-pedidos/{uid}')
        assert r.status_code == 200

        # Ya no debe aparecer en la lista
        r2 = client.get('/api/usuarios-pedidos')
        assert not any(u['nombre'] == 'Maria' for u in r2.get_json()['users'])


# ---------------------------------------------------------------------------
# Tests — recepción de pedido
# ---------------------------------------------------------------------------

class TestRecepcionPedido:

    def test_recepcion_404_pedido_inexistente(self, client):
        r = client.post('/api/pedido-emitido/9999/recepcion',
                        json={'items': [], 'recibido_por': 'Diego'})
        assert r.status_code == 404

    def test_recepcion_guarda_cantidades(self, client):
        s = database.SessionLocal()
        try:
            p = _crear_pedido_emitido(s, n_items=2)
            pid = p.id
            item_ids = [i.id for i in p.items]
        finally:
            s.close()

        items_payload = [{'id': item_ids[0], 'revisada': 3},
                         {'id': item_ids[1], 'revisada': 0}]
        r = client.post(f'/api/pedido-emitido/{pid}/recepcion',
                        json={'items': items_payload, 'recibido_por': 'Diego'})
        assert r.status_code == 200
        assert r.get_json()['ok'] is True

        # Verificar que se guardaron las cantidades
        s2 = database.SessionLocal()
        try:
            it0 = s2.get(PedidoEmitidoItem, item_ids[0])
            it1 = s2.get(PedidoEmitidoItem, item_ids[1])
            assert it0.cantidad_revisada_op == 3
            assert it1.cantidad_revisada_op == 0
            assert it1.estado == 'NO_VINO'
        finally:
            s2.close()

    def test_recepcion_guarda_operador(self, client):
        s = database.SessionLocal()
        try:
            p = _crear_pedido_emitido(s, n_items=1)
            pid = p.id
            item_id = p.items[0].id
        finally:
            s.close()

        client.post(f'/api/pedido-emitido/{pid}/recepcion',
                    json={'items': [{'id': item_id, 'revisada': 5}],
                          'recibido_por': 'Lisandro'})

        s2 = database.SessionLocal()
        try:
            ped = s2.get(PedidoEmitido, pid)
            assert ped.recibido_por == 'Lisandro'
        finally:
            s2.close()

    def test_recepcion_no_pisa_operador_existente(self, client):
        s = database.SessionLocal()
        try:
            p = _crear_pedido_emitido(s, n_items=1)
            p.recibido_por = 'Primero'
            s.commit()
            pid = p.id
            item_id = p.items[0].id
        finally:
            s.close()

        # Intentar cambiar el operador no debe pisar al primero
        client.post(f'/api/pedido-emitido/{pid}/recepcion',
                    json={'items': [{'id': item_id, 'revisada': 3}],
                          'recibido_por': 'Segundo'})

        s2 = database.SessionLocal()
        try:
            ped = s2.get(PedidoEmitido, pid)
            assert ped.recibido_por == 'Primero'
        finally:
            s2.close()

    def test_recepcion_item_completo_cambia_estado(self, client):
        s = database.SessionLocal()
        try:
            p = _crear_pedido_emitido(s, n_items=1)
            pid = p.id
            item_id = p.items[0].id
        finally:
            s.close()

        # cantidad_pedida = 5, revisada = 5 → RECIBIDO
        client.post(f'/api/pedido-emitido/{pid}/recepcion',
                    json={'items': [{'id': item_id, 'revisada': 5}],
                          'recibido_por': 'Test'})

        s2 = database.SessionLocal()
        try:
            it = s2.get(PedidoEmitidoItem, item_id)
            assert it.estado == 'RECIBIDO'
            assert it.cantidad_recibida == 5
        finally:
            s2.close()
