"""Tests para GET /api/pedido/<id>/indicadores."""

import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin

import database
from database import Pedido, PedidoItem


# ---------------------------------------------------------------------------
# Fixture: app con routes.purchase registrado
# ---------------------------------------------------------------------------

@pytest.fixture(scope='module')
def app(init_test_db):
    _app = Flask(__name__, template_folder='../templates')
    _app.secret_key = 'test-indicadores'
    _app.config['TESTING'] = True
    _app.config['UPLOAD_FOLDER'] = '/tmp'

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

    @lm.user_loader
    def _load(uid):
        return _User()

    @lm.request_loader
    def _req_load(req):
        return _User()

    import routes.purchase as _pur
    _pur.init_app(_app)
    return _app


@pytest.fixture
def client(app):
    with app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _crear_pedido(session, laboratorio='Lab Test', n_items=3):
    pedido = Pedido(
        farmacia_id=1,
        laboratorio=laboratorio,
        farmacia='Farmacia Test',
        periodo='2026-05',
        n_days=30,
        estado='PENDIENTE',
    )
    session.add(pedido)
    session.flush()
    for i in range(n_items):
        session.add(PedidoItem(
            pedido_id=pedido.id,
            farmacia_id=1,
            codigo_barra=f'779000000{i:04d}',
            nombre=f'Producto Test {i}',
            cantidad=i + 1,
            precio_pvp=100.0,
            subtotal=100.0 * (i + 1),
        ))
    session.commit()
    return pedido


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_404_pedido_inexistente(client):
    r = client.get('/api/pedido/9999/indicadores')
    assert r.status_code == 404
    data = r.get_json()
    assert 'error' in data


def test_pedido_sin_items(client):
    s = database.SessionLocal()
    try:
        pedido = _crear_pedido(s, n_items=0)
        pid = pedido.id
    finally:
        s.close()

    r = client.get(f'/api/pedido/{pid}/indicadores')
    assert r.status_code == 200
    data = r.get_json()

    assert data['pedido']['id'] == pid
    assert data['pedido']['n_items'] == 0
    assert data['items'] == []
    assert data['riesgos'] == []
    assert data['top10'] == []
    assert 'estacionalidad' in data
    assert len(data['estacionalidad']['labels']) == 12
    assert len(data['estacionalidad']['unidades']) == 12


def test_estructura_respuesta_con_items(client):
    s = database.SessionLocal()
    try:
        pedido = _crear_pedido(s, n_items=3)
        pid = pedido.id
    finally:
        s.close()

    r = client.get(f'/api/pedido/{pid}/indicadores')
    assert r.status_code == 200
    data = r.get_json()

    assert data['pedido']['n_items'] == 3
    assert data['pedido']['unidades_pedidas'] == 1 + 2 + 3  # cantidades 1,2,3

    # Cada item tiene los campos esperados
    for it in data['items']:
        assert 'codigo_barra' in it
        assert 'nombre' in it
        assert 'cantidad_pedida' in it
        assert 'tiene_obs' in it
        assert 'stock' in it
        assert 'u3m' in it
        assert 'u12m' in it

    # Sin ObServer en SQLite: todos sin link
    assert all(not it['tiene_obs'] for it in data['items'])
    assert data['pedido']['n_con_obs'] == 0


def test_filtro_q(client):
    s = database.SessionLocal()
    try:
        pedido = Pedido(
            farmacia_id=1, laboratorio='Lab Q', farmacia='F', periodo='2026-05',
            n_days=30, estado='PENDIENTE',
        )
        s.add(pedido)
        s.flush()
        s.add(PedidoItem(pedido_id=pedido.id, farmacia_id=1,
                         codigo_barra='1111111111111', nombre='AMOXIDAL 500MG',
                         cantidad=5, precio_pvp=100.0, subtotal=500.0))
        s.add(PedidoItem(pedido_id=pedido.id, farmacia_id=1,
                         codigo_barra='2222222222222', nombre='IBUPROFENO 400MG',
                         cantidad=3, precio_pvp=80.0, subtotal=240.0))
        s.commit()
        pid = pedido.id
    finally:
        s.close()

    # Sin filtro: 2 items
    r = client.get(f'/api/pedido/{pid}/indicadores')
    assert r.get_json()['pedido']['n_items'] == 2

    # Filtro "amox" → 1 item
    r = client.get(f'/api/pedido/{pid}/indicadores?q=amox')
    data = r.get_json()
    assert data['pedido']['n_items'] == 1
    assert data['items'][0]['nombre'] == 'AMOXIDAL 500MG'
    assert data['filtro']['items_total'] == 2
    assert data['filtro']['items_filtrados'] == 1


def test_filtro_q_multitoken(client):
    s = database.SessionLocal()
    try:
        pedido = Pedido(
            farmacia_id=1, laboratorio='Lab MT', farmacia='F', periodo='2026-05',
            n_days=30, estado='PENDIENTE',
        )
        s.add(pedido)
        s.flush()
        s.add(PedidoItem(pedido_id=pedido.id, farmacia_id=1,
                         codigo_barra='3333333333333', nombre='ACTRON 400MG SUSPENSION',
                         cantidad=2, precio_pvp=150.0, subtotal=300.0))
        s.add(PedidoItem(pedido_id=pedido.id, farmacia_id=1,
                         codigo_barra='4444444444444', nombre='ACTRON 600MG COMP',
                         cantidad=1, precio_pvp=200.0, subtotal=200.0))
        s.commit()
        pid = pedido.id
    finally:
        s.close()

    # "actron susp" → solo el de suspensión
    r = client.get(f'/api/pedido/{pid}/indicadores?q=actron+susp')
    data = r.get_json()
    assert data['pedido']['n_items'] == 1
    assert 'SUSPENSION' in data['items'][0]['nombre']


def test_mix_monodroga_y_laboratorio_presentes(client):
    s = database.SessionLocal()
    try:
        pedido = _crear_pedido(s, n_items=5)
        pid = pedido.id
    finally:
        s.close()

    r = client.get(f'/api/pedido/{pid}/indicadores')
    data = r.get_json()

    # mix_monodroga y mix_laboratorio deben ser listas (pueden ser vacías si no hay ObServer)
    assert isinstance(data['mix_monodroga'], list)
    assert isinstance(data['mix_laboratorio'], list)


def test_estacionalidad_tiene_12_meses(client):
    s = database.SessionLocal()
    try:
        pedido = _crear_pedido(s, n_items=1)
        pid = pedido.id
    finally:
        s.close()

    r = client.get(f'/api/pedido/{pid}/indicadores')
    data = r.get_json()
    est = data['estacionalidad']
    assert len(est['labels']) == 12
    assert len(est['unidades']) == 12
    assert all(v == 0.0 for v in est['unidades'])  # sin ObServer → todo 0
