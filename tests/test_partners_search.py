"""Tests del endpoint /api/partners/search, en especial la materialización
on-the-fly de labs de ObsLaboratorio al master `laboratorios`.

Contexto: el master `laboratorios` se popla on-demand (al materializar productos),
así que labs que existen en ObServer pero a los que nunca se les materializó un
producto NO aparecen en el autocomplete. El fix es buscar también en
ObsLaboratorio y materializar el faltante en el momento (vía
get_or_create_laboratorio, que es idempotente por normalización de nombre).
"""
import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin

import database
from database import Laboratorio, ObsLaboratorio, Provider, get_db


@pytest.fixture
def app_partners():
    """App mínima con routes/partners registrado + LoginManager dummy."""
    app = Flask(__name__)
    app.secret_key = 'test'
    app.config['TESTING'] = True
    lm = LoginManager(app)

    class _User(UserMixin):
        id = '1'

    @lm.user_loader
    def _load(_):
        return _User()

    @lm.request_loader
    def _req(_):
        return _User()

    import routes.partners as _p
    _p.init_app(app)
    return app


@pytest.fixture
def client(app_partners):
    return app_partners.test_client()


# ───────────── Setup helpers ─────────────

def _add_obs_lab(s, observer_id, descripcion, fecha_baja=None):
    s.add(ObsLaboratorio(observer_id=observer_id, descripcion=descripcion,
                         fecha_baja=fecha_baja))


# ───────────── Búsqueda vanilla en master ─────────────

class TestSearchVanilla:
    def test_search_master_existente(self, client):
        with get_db() as s:
            s.add(Laboratorio(nombre='Roemmers', activo=True))
            s.commit()
        r = client.get('/api/partners/search?tipo=laboratorio&q=roem')
        assert r.status_code == 200
        body = r.get_json()
        assert body['tipo'] == 'laboratorio'
        assert any(p['nombre'] == 'Roemmers' for p in body['data'])

    def test_tipo_invalido(self, client):
        r = client.get('/api/partners/search?tipo=xxx&q=roem')
        assert r.status_code == 400


# ───────────── Materialización on-the-fly (el fix) ─────────────

class TestMaterializaDesdeObs:
    def test_materializa_lab_de_obs_si_falta_en_master(self, client):
        with get_db() as s:
            _add_obs_lab(s, observer_id=152, descripcion='Roemmers')
            s.commit()
            assert s.query(Laboratorio).count() == 0  # master vacío
        r = client.get('/api/partners/search?tipo=laboratorio&q=roem')
        body = r.get_json()
        # Devuelve el lab, con id real del master.
        assert any(p['nombre'] == 'Roemmers' for p in body['data'])
        # Y queda persistido vinculado al observer_id.
        with get_db() as s:
            row = s.query(Laboratorio).filter_by(observer_id=152).first()
            assert row is not None
            assert row.nombre == 'Roemmers'

    def test_no_duplica_si_ya_existe_en_master(self, client):
        with get_db() as s:
            s.add(Laboratorio(nombre='Roemmers', observer_id=152, activo=True))
            _add_obs_lab(s, observer_id=152, descripcion='Roemmers')
            s.commit()
        r = client.get('/api/partners/search?tipo=laboratorio&q=roem')
        body = r.get_json()
        # Un solo Roemmers en la respuesta y en master.
        nombres = [p['nombre'] for p in body['data']]
        assert nombres.count('Roemmers') == 1
        with get_db() as s:
            assert s.query(Laboratorio).filter_by(nombre='Roemmers').count() == 1

    def test_dedupe_por_normalizacion_de_nombre(self, client):
        """Si el master tiene 'ROEMMERS' y Obs trae 'Roemmers', no crea otro."""
        with get_db() as s:
            s.add(Laboratorio(nombre='ROEMMERS', activo=True))
            _add_obs_lab(s, observer_id=152, descripcion='Roemmers')
            s.commit()
        client.get('/api/partners/search?tipo=laboratorio&q=roem')
        with get_db() as s:
            # Sigue habiendo 1, y al existente le pegó el observer_id.
            rows = s.query(Laboratorio).all()
            assert len(rows) == 1
            assert rows[0].observer_id == 152

    def test_ignora_obs_con_fecha_baja(self, client):
        from datetime import datetime
        with get_db() as s:
            _add_obs_lab(s, 999, 'LabBajado', fecha_baja=datetime(2024, 1, 1))
            s.commit()
        r = client.get('/api/partners/search?tipo=laboratorio&q=labbaj')
        body = r.get_json()
        assert body['data'] == []
        with get_db() as s:
            assert s.query(Laboratorio).count() == 0  # no se materializó


# ───────────── Guards: no materializa en casos que no corresponde ─────────────

class TestGuards:
    def test_query_corta_no_toca_obs(self, client):
        """q de 1 char no debe disparar la materialización (UX: keystroke ruido)."""
        with get_db() as s:
            _add_obs_lab(s, 152, 'Roemmers')
            s.commit()
        r = client.get('/api/partners/search?tipo=laboratorio&q=r')
        assert r.status_code == 200
        # Master no recibió el INSERT.
        with get_db() as s:
            assert s.query(Laboratorio).count() == 0

    def test_drogueria_no_busca_en_obs_labs(self, client):
        """Para tipo=drogueria/proveedor, no se mira ObsLaboratorio."""
        with get_db() as s:
            _add_obs_lab(s, 152, 'Roemmers')
            s.commit()
        r = client.get('/api/partners/search?tipo=drogueria&q=roem')
        assert r.status_code == 200
        assert r.get_json()['data'] == []
        with get_db() as s:
            # No se materializó nada en laboratorios.
            assert s.query(Laboratorio).count() == 0

    def test_sin_query_no_materializa(self, client):
        """Sin q (search sin filtro) no debería hacer side-effect."""
        with get_db() as s:
            _add_obs_lab(s, 152, 'Roemmers')
            s.commit()
        r = client.get('/api/partners/search?tipo=laboratorio')
        assert r.status_code == 200
        with get_db() as s:
            assert s.query(Laboratorio).count() == 0
