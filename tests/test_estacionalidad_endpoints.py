"""Tests funcionales de los endpoints CRUD de escenarios de estacionalidad.

Usa el flask_app fixture de conftest pero registra el módulo de estacionalidad
manualmente (conftest solo registra invoices/claims/plantillas/inferencia).
"""
import json

import pytest

import database
import routes.estacionalidad as estac_routes
from database import EstacionalidadEscenario, ObsNombreDroga


@pytest.fixture(scope='module', autouse=True)
def _registrar_modulo_estacionalidad(flask_app):
    if 'informe_estacionalidad_drogas' in flask_app.view_functions:
        return
    estac_routes.init_app(flask_app)


@pytest.fixture
def droga_demo():
    """Crea una droga en obs_nombres_drogas para los tests."""
    s = database.SessionLocal()
    try:
        d = ObsNombreDroga(observer_id=9999, descripcion='Paracetamol DEMO')
        s.add(d)
        s.commit()
        yield 9999
    finally:
        s.close()


def _indices_validos():
    return [1.0, 0.8, 1.1, 2.5, 1.5, 0.7, 0.6, 0.6, 0.6, 1.0, 0.5, 0.6]


class TestListar:

    def test_lista_vacia_cuando_no_hay_escenarios(self, client, droga_demo):
        r = client.get(f'/api/estacionalidad/droga/{droga_demo}/escenarios')
        assert r.status_code == 200
        body = r.get_json()
        assert body['droga_id'] == droga_demo
        assert body['escenarios'] == []


class TestCrear:

    def test_crear_escenario_basico(self, client, droga_demo):
        payload = {'nombre': 'base', 'indices': _indices_validos()}
        r = client.post(f'/api/estacionalidad/droga/{droga_demo}/escenarios',
                        json=payload)
        assert r.status_code == 200
        body = r.get_json()
        assert body['nombre'] == 'base'
        assert body['indices'] == _indices_validos()
        assert body['lead_time_meses'] == 0
        assert body['cobertura_meses'] == 1.0
        assert body['es_default'] is False
        assert body['id'] is not None

    def test_crear_escenario_con_lead_y_cobertura(self, client, droga_demo):
        r = client.post(f'/api/estacionalidad/droga/{droga_demo}/escenarios', json={
            'nombre': 'agresivo',
            'indices': _indices_validos(),
            'lead_time_meses': 2,
            'cobertura_meses': 1.5,
            'es_default': True,
        })
        assert r.status_code == 200
        body = r.get_json()
        assert body['lead_time_meses'] == 2
        assert body['cobertura_meses'] == 1.5
        assert body['es_default'] is True

    def test_rechaza_indices_incompletos(self, client, droga_demo):
        r = client.post(f'/api/estacionalidad/droga/{droga_demo}/escenarios',
                        json={'nombre': 'x', 'indices': [1.0, 2.0]})
        assert r.status_code == 400
        assert 'indices' in r.get_json()['error'].lower()

    def test_rechaza_indices_no_numericos(self, client, droga_demo):
        bad = ['a'] * 12
        r = client.post(f'/api/estacionalidad/droga/{droga_demo}/escenarios',
                        json={'nombre': 'x', 'indices': bad})
        assert r.status_code == 400

    def test_rechaza_droga_inexistente(self, client):
        r = client.post('/api/estacionalidad/droga/77777/escenarios',
                        json={'nombre': 'x', 'indices': _indices_validos()})
        assert r.status_code == 404

    def test_upsert_actualiza_existente(self, client, droga_demo):
        # Crear
        client.post(f'/api/estacionalidad/droga/{droga_demo}/escenarios',
                    json={'nombre': 'base', 'indices': _indices_validos()})
        # Actualizar misma key (droga_id, nombre): debería upsertear, no crear duplicado.
        nuevos = [2.0] * 12
        r = client.post(f'/api/estacionalidad/droga/{droga_demo}/escenarios',
                        json={'nombre': 'base', 'indices': nuevos, 'lead_time_meses': 3})
        assert r.status_code == 200
        assert r.get_json()['indices'] == nuevos
        assert r.get_json()['lead_time_meses'] == 3

        # Listar: debe haber solo 1
        r2 = client.get(f'/api/estacionalidad/droga/{droga_demo}/escenarios')
        assert len(r2.get_json()['escenarios']) == 1

    def test_clipping_lead_y_cobertura(self, client, droga_demo):
        # lead_time fuera de rango se clipea a [0, 6]
        r = client.post(f'/api/estacionalidad/droga/{droga_demo}/escenarios', json={
            'nombre': 'clip',
            'indices': _indices_validos(),
            'lead_time_meses': 99,
            'cobertura_meses': 50,
        })
        body = r.get_json()
        assert body['lead_time_meses'] == 6
        assert body['cobertura_meses'] == 6.0


class TestDefault:

    def test_marcar_default_desmarca_otros(self, client, droga_demo):
        e1 = client.post(f'/api/estacionalidad/droga/{droga_demo}/escenarios',
                         json={'nombre': 'a', 'indices': _indices_validos(),
                               'es_default': True}).get_json()
        e2 = client.post(f'/api/estacionalidad/droga/{droga_demo}/escenarios',
                         json={'nombre': 'b', 'indices': _indices_validos()}).get_json()
        # Marcar e2 como default → e1 deja de serlo.
        r = client.post(
            f'/api/estacionalidad/droga/{droga_demo}/escenarios/{e2["id"]}/default')
        assert r.status_code == 200
        assert r.get_json()['es_default'] is True

        listado = client.get(
            f'/api/estacionalidad/droga/{droga_demo}/escenarios').get_json()
        por_id = {e['id']: e for e in listado['escenarios']}
        assert por_id[e1['id']]['es_default'] is False
        assert por_id[e2['id']]['es_default'] is True

    def test_default_via_post_es_exclusivo(self, client, droga_demo):
        """Crear un escenario con es_default=True debe desmarcar los anteriores."""
        e1 = client.post(f'/api/estacionalidad/droga/{droga_demo}/escenarios',
                         json={'nombre': 'a', 'indices': _indices_validos(),
                               'es_default': True}).get_json()
        client.post(f'/api/estacionalidad/droga/{droga_demo}/escenarios',
                    json={'nombre': 'b', 'indices': _indices_validos(),
                          'es_default': True})

        listado = client.get(
            f'/api/estacionalidad/droga/{droga_demo}/escenarios').get_json()
        defaults = [e for e in listado['escenarios'] if e['es_default']]
        assert len(defaults) == 1
        assert defaults[0]['nombre'] == 'b'


class TestEliminar:

    def test_eliminar_escenario(self, client, droga_demo):
        e = client.post(f'/api/estacionalidad/droga/{droga_demo}/escenarios',
                        json={'nombre': 'a', 'indices': _indices_validos()}).get_json()
        r = client.delete(
            f'/api/estacionalidad/droga/{droga_demo}/escenarios/{e["id"]}')
        assert r.status_code == 200

        listado = client.get(
            f'/api/estacionalidad/droga/{droga_demo}/escenarios').get_json()
        assert listado['escenarios'] == []

    def test_eliminar_inexistente_404(self, client, droga_demo):
        r = client.delete(
            f'/api/estacionalidad/droga/{droga_demo}/escenarios/99999')
        assert r.status_code == 404


class TestPersistencia:

    def test_se_guarda_en_db(self, client, droga_demo):
        payload = {'nombre': 'real', 'indices': _indices_validos(),
                   'lead_time_meses': 1, 'cobertura_meses': 0.75}
        client.post(f'/api/estacionalidad/droga/{droga_demo}/escenarios', json=payload)

        s = database.SessionLocal()
        try:
            esc = s.query(EstacionalidadEscenario).filter_by(
                droga_id=droga_demo, nombre='real').first()
            assert esc is not None
            assert json.loads(esc.indices_json) == _indices_validos()
            assert esc.lead_time_meses == 1
            assert float(esc.cobertura_meses) == 0.75
        finally:
            s.close()
