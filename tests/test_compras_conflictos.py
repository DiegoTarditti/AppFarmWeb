"""Tests para /api/compras/conflictos — validación de input.

Solo cubre la validación de payload (body inválido, items grandes). No testea
la lógica de detección de conflictos (queries complejas a obs_productos +
laboratorios) — eso lo cubre un smoke test futuro con datos sembrados.
"""
import pytest

# Dependencias transitivas (pandas, openpyxl) son necesarias porque
# routes/__init__.py importa todos los módulos eagerly.
pytest.importorskip('pandas')
pytest.importorskip('openpyxl')


@pytest.fixture(scope='module')
def app_con_compras(init_test_db, tmp_path_factory):
    from flask import Flask
    from flask_login import LoginManager, UserMixin

    app = Flask(__name__, template_folder='../templates')
    app.secret_key = 'test-conflictos'
    app.config['TESTING'] = True
    app.config['UPLOAD_FOLDER'] = str(tmp_path_factory.mktemp('uploads'))

    lm = LoginManager(app)

    class _U(UserMixin):
        id = '1'
        username = 'test'
        rol = 'admin'

    @lm.user_loader
    def _load(uid):
        return _U()

    @lm.request_loader
    def _req(_r):
        return _U()

    import routes.compras_rapido as cr
    cr.init_app(app)
    return app


@pytest.fixture
def client(app_con_compras):
    with app_con_compras.test_client() as c:
        yield c


def test_items_no_es_lista(client):
    r = client.post('/api/compras/conflictos', json={'items': 'no soy lista'})
    assert r.status_code == 400
    assert 'lista' in r.get_json()['error']


def test_items_vacio(client):
    r = client.post('/api/compras/conflictos', json={'items': []})
    assert r.status_code == 400
    assert 'vacío' in r.get_json()['error']


def test_body_sin_items(client):
    r = client.post('/api/compras/conflictos', json={})
    assert r.status_code == 400


def test_body_no_json(client):
    r = client.post('/api/compras/conflictos', data='no json')
    assert r.status_code == 400


def test_items_supera_limite(client):
    """Anti-DoS: 5001 items debe rechazarse con 413."""
    items = [{'ean': str(i), 'drogueria_actual_id': 1} for i in range(5001)]
    r = client.post('/api/compras/conflictos', json={'items': items})
    assert r.status_code == 413
    assert '5000' in r.get_json()['error']


def test_items_valido_dentro_de_limite(client):
    """5000 items pasa la validación (otra cosa es si encuentra conflictos)."""
    items = [{'ean': '7791234567890', 'drogueria_actual_id': 1}]
    r = client.post('/api/compras/conflictos', json={'items': items})
    # 200 OK con conflictos vacíos (no hay datos en DB).
    assert r.status_code == 200
    body = r.get_json()
    assert body['ok'] is True
    assert 'conflictos' in body
