"""Tests para GET /api/sync-status."""

from datetime import datetime, timedelta

import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin

import database
from database import (
    ObsCliente, ObsLaboratorio, ObsProducto, ObsStock,
    ObsSyncLog, ObsVentaMensual,
)


# ---------------------------------------------------------------------------
# Fixture: app con routes.observer registrado
# ---------------------------------------------------------------------------

@pytest.fixture(scope='module')
def app(init_test_db):
    _app = Flask(__name__, template_folder='../templates')
    _app.secret_key = 'test-sync-status'
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

    import routes.observer as _obs
    _obs.init_app(_app)
    return _app


@pytest.fixture
def client(app):
    with app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _log(session, entidad, hace_horas):
    """Crea un ObsSyncLog con timestamp = ahora - hace_horas."""
    ts = datetime.utcnow() - timedelta(hours=hace_horas)
    session.add(ObsSyncLog(entidad=entidad, ejecutado_en=ts, filas_upsert=10))
    session.commit()


def _fila_ventas(session):
    """Inserta una fila mínima en obs_ventas_mensuales (requiere obs_productos)."""
    if not session.query(ObsProducto).first():
        session.add(ObsProducto(observer_id=1, descripcion='Prod test'))
        session.commit()
    session.add(ObsVentaMensual(
        id_farmacia=1, producto_observer=1, anio=2026, mes=1,
        unidades=10, monto=1000, transacciones=5,
    ))
    session.commit()


def _fila_stock(session):
    if not session.query(ObsProducto).first():
        session.add(ObsProducto(observer_id=1, descripcion='Prod test'))
        session.commit()
    session.add(ObsStock(id_farmacia=1, producto_observer=1, stock_actual=5))
    session.commit()


def _fila_producto(session):
    session.add(ObsProducto(observer_id=99, descripcion='Prod extra'))
    session.commit()


def _fila_laboratorio(session):
    session.add(ObsLaboratorio(observer_id=1, descripcion='Lab test'))
    session.commit()


def _fila_cliente(session):
    session.add(ObsCliente(observer_id=1, apellido_nombre='Cliente test'))
    session.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_estructura_respuesta(client):
    r = client.get('/api/sync-status')
    assert r.status_code == 200
    data = r.get_json()
    assert 'entidades' in data
    assert 'peor_estado' in data
    assert 'cualquier_atrasado' in data
    assert isinstance(data['entidades'], dict)


def test_todo_nunca_sin_datos(client):
    """Sin filas en ninguna tabla → todas en estado 'nunca'."""
    r = client.get('/api/sync-status')
    data = r.get_json()
    for entidad_data in data['entidades'].values():
        assert entidad_data['estado'] == 'nunca'
    assert data['peor_estado'] == 'nunca'
    assert data['cualquier_atrasado'] is True


def test_estado_ok(client):
    s = database.SessionLocal()
    try:
        _fila_ventas(s)
        _log(s, 'ventas_mensuales', hace_horas=1)  # hace 1h → bien dentro del warn de 24h
    finally:
        s.close()

    r = client.get('/api/sync-status')
    data = r.get_json()
    assert data['entidades']['ventas_mensuales']['estado'] == 'ok'


def test_estado_warning(client):
    s = database.SessionLocal()
    try:
        _fila_ventas(s)
        _log(s, 'ventas_mensuales', hace_horas=30)  # >24h warn, <72h err
    finally:
        s.close()

    r = client.get('/api/sync-status')
    data = r.get_json()
    assert data['entidades']['ventas_mensuales']['estado'] == 'warning'
    assert data['cualquier_atrasado'] is True


def test_estado_error(client):
    s = database.SessionLocal()
    try:
        _fila_ventas(s)
        _log(s, 'ventas_mensuales', hace_horas=80)  # >72h err
    finally:
        s.close()

    r = client.get('/api/sync-status')
    data = r.get_json()
    assert data['entidades']['ventas_mensuales']['estado'] == 'error'
    assert data['peor_estado'] == 'error'


def test_estado_externo_sin_log(client):
    """Hay filas pero no hay ObsSyncLog → estado 'externo'."""
    s = database.SessionLocal()
    try:
        _fila_ventas(s)
        # NO creamos log → externo
    finally:
        s.close()

    r = client.get('/api/sync-status')
    data = r.get_json()
    assert data['entidades']['ventas_mensuales']['estado'] == 'externo'


def test_peor_estado_prioridad(client):
    """Si hay una en error y otra en ok, peor_estado = error."""
    s = database.SessionLocal()
    try:
        _fila_ventas(s)
        _log(s, 'ventas_mensuales', hace_horas=80)   # error
        _fila_laboratorio(s)
        _log(s, 'laboratorios', hace_horas=1)         # ok
    finally:
        s.close()

    r = client.get('/api/sync-status')
    data = r.get_json()
    assert data['peor_estado'] == 'error'


def test_cualquier_atrasado_false_cuando_todo_ok(client):
    s = database.SessionLocal()
    try:
        _fila_ventas(s)
        _log(s, 'ventas_mensuales', hace_horas=1)
        _fila_stock(s)
        _log(s, 'stock', hace_horas=1)
        _fila_producto(s)
        _log(s, 'productos', hace_horas=1)
        _fila_laboratorio(s)
        _log(s, 'laboratorios', hace_horas=1)
        s2 = database.SessionLocal()
        s2.add(ObsCliente(observer_id=2, apellido_nombre='Cliente 2', id_farmacia=1))
        s2.commit()
        _log(s2, 'clientes', hace_horas=1)
        s2.close()
    finally:
        s.close()

    r = client.get('/api/sync-status')
    data = r.get_json()
    assert data['cualquier_atrasado'] is False
    assert data['peor_estado'] in ('ok', 'externo')


def test_campos_por_entidad(client):
    """Cada entidad devuelve los campos esperados."""
    s = database.SessionLocal()
    try:
        _fila_ventas(s)
        _log(s, 'ventas_mensuales', hace_horas=5)
    finally:
        s.close()

    r = client.get('/api/sync-status')
    data = r.get_json()
    e = data['entidades']['ventas_mensuales']
    assert 'estado' in e
    assert 'horas' in e
    assert 'ultimo_sync' in e
    assert 'filas' in e
    assert 'mensaje' in e
    assert e['filas'] >= 1
    assert e['horas'] is not None
