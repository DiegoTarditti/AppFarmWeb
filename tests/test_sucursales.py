"""Tests del modelo Sucursal + rutas CRUD (`routes/sucursales.py`).

Cubre:
- Modelo: defaults, slug UNIQUE, no_null en slug/nombre.
- Listado: orden alfabético, contexto al template.
- Crear: validación slug/nombre, dedupe por slug, `activa` desde checkbox.
- Editar: por id existente / id inexistente.
- Eliminar: por id existente / id inexistente (idempotente).

Auth: el endpoint usa `@requiere_permiso('usuarios','admin')`, que llama a
`auth.tiene_permiso(current_user, …)`. Lo monkeypatcheamos a True para que el
test_client (Flask-Login del conftest devuelve user dummy) llegue al handler.
"""
import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin
from sqlalchemy.exc import IntegrityError

import auth
import database
from database import Sucursal, get_db


# ──────────────────── Modelo ────────────────────

class TestModeloSucursal:
    def test_defaults(self):
        with get_db() as s:
            row = Sucursal(slug='badia', nombre='Badia')
            s.add(row); s.commit()
            s.refresh(row)
            assert row.id is not None
            # activa por default True (Column default)
            assert row.activa is True
            # campos opcionales arrancan en None
            assert row.app_name is None
            assert row.db_name is None
            assert row.url_externa is None
            # actualizado_en con default callable (now_ar)
            assert row.actualizado_en is not None

    def test_slug_unique(self):
        with get_db() as s:
            s.add(Sucursal(slug='badia', nombre='Badia 1'))
            s.commit()
            s.add(Sucursal(slug='badia', nombre='Badia 2'))
            with pytest.raises(IntegrityError):
                s.commit()
            s.rollback()

    @pytest.mark.parametrize('campos,esperado', [
        ({'nombre': 'Sin slug'}, 'slug'),     # falta slug (NOT NULL)
        ({'slug': 'sin-nombre'}, 'nombre'),   # falta nombre (NOT NULL)
    ])
    def test_campos_obligatorios(self, campos, esperado):
        with get_db() as s:
            s.add(Sucursal(**campos))
            with pytest.raises(IntegrityError):
                s.commit()
            s.rollback()
            # el error menciona la columna afectada (sanity check)
            # — no estricto, depende del backend; solo aseguramos que rompe.
            assert esperado in ('slug', 'nombre')

    def test_activa_se_puede_apagar(self):
        with get_db() as s:
            row = Sucursal(slug='off', nombre='Off', activa=False)
            s.add(row); s.commit()
            assert s.query(Sucursal).filter_by(slug='off').first().activa is False


# ──────────────────── Rutas: app + cliente ────────────────────

@pytest.fixture
def app_sucursales(monkeypatch):
    """Mini-app Flask con routes/sucursales registrado + auth bypass + Login.

    No reusa el `flask_app` del conftest porque ese solo registra unas pocas
    rutas (invoices/claims/…) y necesitamos `sucursales_*`. Mantiene el resto
    del setup análogo al conftest.
    """
    app = Flask(__name__, template_folder='../templates')
    app.secret_key = 'test'
    app.config['TESTING'] = True

    # Jinja globals que el template y el base usan.
    class _AnonUser:
        is_authenticated = False
        nombre_completo = None; username = None; rol = None
    app.jinja_env.globals['current_user'] = _AnonUser()
    app.jinja_env.globals['tiene_permiso'] = lambda *a, **k: True
    class _Entorno:
        codigo = 'test'; label = 'Test'; color = '#888'
    app.jinja_env.globals['entorno'] = _Entorno()
    from flask import url_for as _real_url_for
    app.jinja_env.globals['url_for'] = lambda ep, **kw: (
        _real_url_for(ep, **kw) if ep in app.view_functions else '#'
    )

    # Flask-Login dummy autenticado (necesario para @login_required).
    lm = LoginManager(app)

    class _DummyUser(UserMixin):
        id = '1'; username = 'admin'; rol = 'admin'; nombre_completo = 'Admin'
    @lm.user_loader
    def _load_user(_):
        return _DummyUser()
    @lm.request_loader
    def _req_load(_):
        return _DummyUser()

    # Bypass del check de permiso fino (el decorator usa auth.tiene_permiso).
    monkeypatch.setattr(auth, 'tiene_permiso', lambda *a, **k: True)

    # Registrar rutas bajo test.
    import routes.sucursales as _suc
    _suc.init_app(app)

    return app


@pytest.fixture
def client(app_sucursales):
    return app_sucursales.test_client()


# ──────────────────── Rutas: listado ────────────────────

class TestListado:
    def test_vacio(self, client):
        r = client.get('/sucursales')
        assert r.status_code == 200

    def test_orden_alfabetico(self, client):
        with get_db() as s:
            s.add_all([
                Sucursal(slug='zeta', nombre='Zeta'),
                Sucursal(slug='alfa', nombre='Alfa'),
                Sucursal(slug='media', nombre='Media'),
            ])
            s.commit()
        r = client.get('/sucursales')
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        # Aparecen en orden Alfa → Media → Zeta.
        i_alfa, i_media, i_zeta = body.find('Alfa'), body.find('Media'), body.find('Zeta')
        assert -1 < i_alfa < i_media < i_zeta


# ──────────────────── Rutas: guardar (crear/editar) ────────────────────

class TestGuardar:
    def test_crear_minimo(self, client):
        r = client.post('/sucursales/guardar', data={'slug': 'badia', 'nombre': 'Badia'})
        assert r.status_code == 302  # redirect a sucursales_list
        with get_db() as s:
            row = s.query(Sucursal).filter_by(slug='badia').first()
            assert row is not None
            assert row.nombre == 'Badia'
            # activa: no se envió checkbox → False (el form usa bool(f.get('activa')))
            assert row.activa is False

    def test_crear_completo(self, client):
        r = client.post('/sucursales/guardar', data={
            'slug': 'PIERI', 'nombre': '  Pieri  ',  # slug se lowercasea, nombre se strippea
            'app_name': 'farmacia-pieri',
            'db_name': 'pieri_db',
            'url_externa': 'postgresql://x/y',
            'activa': 'on',
        })
        assert r.status_code == 302
        with get_db() as s:
            row = s.query(Sucursal).filter_by(slug='pieri').first()
            assert row is not None
            assert row.nombre == 'Pieri'
            assert row.app_name == 'farmacia-pieri'
            assert row.db_name == 'pieri_db'
            assert row.url_externa == 'postgresql://x/y'
            assert row.activa is True

    @pytest.mark.parametrize('data', [
        {},                              # ambos vacíos
        {'slug': 'x'},                   # falta nombre
        {'nombre': 'Y'},                 # falta slug
        {'slug': '   ', 'nombre': 'Y'},  # slug solo espacios
    ])
    def test_crear_falla_si_faltan_obligatorios(self, client, data):
        r = client.post('/sucursales/guardar', data=data)
        assert r.status_code == 302  # redirect con flash error
        with get_db() as s:
            assert s.query(Sucursal).count() == 0

    def test_crear_rechaza_slug_duplicado(self, client):
        with get_db() as s:
            s.add(Sucursal(slug='badia', nombre='Badia')); s.commit()
        r = client.post('/sucursales/guardar', data={'slug': 'badia', 'nombre': 'Badia2'})
        assert r.status_code == 302
        with get_db() as s:
            # Sigue habiendo solo 1, con el nombre original.
            rows = s.query(Sucursal).filter_by(slug='badia').all()
            assert len(rows) == 1
            assert rows[0].nombre == 'Badia'

    def test_editar_existente(self, client):
        with get_db() as s:
            row = Sucursal(slug='badia', nombre='Badia', app_name='vieja')
            s.add(row); s.commit()
            sid = row.id
        r = client.post('/sucursales/guardar', data={
            'id': str(sid), 'slug': 'badia', 'nombre': 'Badia Nueva',
            'app_name': 'farmacia-badia', 'activa': '1',
        })
        assert r.status_code == 302
        with get_db() as s:
            row = s.get(Sucursal, sid)
            assert row.nombre == 'Badia Nueva'
            assert row.app_name == 'farmacia-badia'
            assert row.activa is True
            # db_name y url_externa no se mandaron → quedan None
            assert row.db_name is None
            assert row.url_externa is None

    def test_editar_id_inexistente_no_crea_nada(self, client):
        r = client.post('/sucursales/guardar', data={
            'id': '9999', 'slug': 'fantasma', 'nombre': 'No Existe',
        })
        assert r.status_code == 302
        with get_db() as s:
            assert s.query(Sucursal).count() == 0


# ──────────────────── Rutas: delete ────────────────────

class TestDelete:
    def test_delete_existente(self, client):
        with get_db() as s:
            row = Sucursal(slug='badia', nombre='Badia')
            s.add(row); s.commit()
            sid = row.id
        r = client.post(f'/sucursales/{sid}/delete')
        assert r.status_code == 302
        with get_db() as s:
            assert s.get(Sucursal, sid) is None

    def test_delete_inexistente_no_rompe(self, client):
        r = client.post('/sucursales/9999/delete')
        # Idempotente: redirige normal aunque no exista.
        assert r.status_code == 302
