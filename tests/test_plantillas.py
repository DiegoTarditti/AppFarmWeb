"""Tests de integración del flujo unificado de Plantillas.

Cubre los 5 endpoints de routes/plantillas.py:
  - GET    /partner/<tipo>/<id>/plantillas                 → listar
  - POST   /partner/<tipo>/<id>/plantillas/new             → crear
  - GET    /partner/<tipo>/<id>/plantillas/<pid>           → editor
  - POST   /partner/<tipo>/<id>/plantillas/<pid>/save      → guardar config
  - POST   /partner/<tipo>/<id>/plantillas/<pid>/delete    → eliminar
  - POST   /partner/<tipo>/<id>/plantillas/<pid>/duplicate → duplicar

Plus migración legacy idempotente.
"""

import json
import uuid
import pytest
import database
from database import (
    Plantilla, Laboratorio, Provider,
    ExportTemplate, PlantillaExportacion, PlantillaCampo,
)


@pytest.fixture
def db_session():
    s = database.SessionLocal()
    yield s
    s.rollback()
    s.close()


@pytest.fixture
def lab(db_session):
    lab = Laboratorio(nombre=f'LAB {uuid.uuid4().hex[:8]}')
    db_session.add(lab)
    db_session.commit()
    return lab


@pytest.fixture
def drogueria(db_session):
    suffix = uuid.uuid4().hex[:8]
    prov = Provider(razon_social=f'DROG {suffix}', cuit=f'30-{suffix}-1', tipo='drogueria')
    db_session.add(prov)
    db_session.commit()
    return prov


# ── Crear y listar ────────────────────────────────────────────────────────────

class TestCrearYListar:

    def test_lista_vacia_200(self, client, lab):
        resp = client.get(f'/partner/laboratorio/{lab.id}/plantillas')
        assert resp.status_code == 200
        assert b'Sin plantillas' in resp.data or b'PLANTILLAS' in resp.data

    def test_crear_xlsx_redirige_al_editor(self, client, lab, db_session):
        resp = client.post(
            f'/partner/laboratorio/{lab.id}/plantillas/new',
            data={'nombre': 'Plantilla mensual', 'formato': 'xlsx', 'tipo_doc': 'pedido'},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert '/plantillas/' in resp.headers['Location']

        db_session.expire_all()
        plant = db_session.query(Plantilla).filter_by(entidad_tipo='laboratorio', entidad_id=lab.id).first()
        assert plant is not None
        assert plant.nombre == 'Plantilla mensual'
        assert plant.formato == 'xlsx'
        assert plant.tipo_doc == 'pedido'

    def test_crear_txt_fijo_inicializa_config(self, client, drogueria, db_session):
        resp = client.post(
            f'/partner/drogueria/{drogueria.id}/plantillas/new',
            data={'nombre': 'TXT droguería', 'formato': 'txt_fijo', 'tipo_doc': 'pedido'},
        )
        assert resp.status_code == 302
        db_session.expire_all()
        plant = db_session.query(Plantilla).filter_by(entidad_id=drogueria.id).first()
        cfg = json.loads(plant.config_json)
        assert 'campos' in cfg
        assert cfg['encoding'] == 'UTF-8'

    def test_crear_nombre_corto_flash(self, client, lab):
        resp = client.post(
            f'/partner/laboratorio/{lab.id}/plantillas/new',
            data={'nombre': 'x', 'formato': 'xlsx'},
        )
        assert resp.status_code == 302
        assert f'/partner/laboratorio/{lab.id}/plantillas' in resp.headers['Location']

    def test_tipo_invalido_404(self, client):
        resp = client.get('/partner/inexistente/1/plantillas')
        assert resp.status_code == 404

    def test_entidad_inexistente_404(self, client):
        resp = client.get('/partner/laboratorio/99999/plantillas')
        assert resp.status_code == 404


# ── Editar / guardar ──────────────────────────────────────────────────────────

class TestEditarGuardar:

    def test_save_actualiza_nombre_y_config(self, client, lab, db_session):
        p = Plantilla(entidad_tipo='laboratorio', entidad_id=lab.id,
                      nombre='orig', formato='xlsx', tipo_doc='pedido',
                      config_json='{}')
        db_session.add(p); db_session.commit()

        resp = client.post(
            f'/partner/laboratorio/{lab.id}/plantillas/{p.id}/save',
            json={'nombre': 'editado', 'config': {'columnas': ['codigo_barra', 'descripcion']}},
        )
        assert resp.status_code == 200
        assert resp.get_json()['ok'] is True

        db_session.expire_all()
        refreshed = db_session.get(Plantilla, p.id)
        assert refreshed.nombre == 'editado'
        assert json.loads(refreshed.config_json) == {'columnas': ['codigo_barra', 'descripcion']}

    def test_save_marca_default_desmarca_otras(self, client, lab, db_session):
        a = Plantilla(entidad_tipo='laboratorio', entidad_id=lab.id,
                      nombre='A', formato='xlsx', tipo_doc='pedido',
                      config_json='{}', es_default=True)
        b = Plantilla(entidad_tipo='laboratorio', entidad_id=lab.id,
                      nombre='B', formato='xlsx', tipo_doc='pedido',
                      config_json='{}', es_default=False)
        db_session.add_all([a, b]); db_session.commit()

        resp = client.post(
            f'/partner/laboratorio/{lab.id}/plantillas/{b.id}/save',
            json={'es_default': True},
        )
        assert resp.status_code == 200

        db_session.expire_all()
        assert db_session.get(Plantilla, a.id).es_default is False
        assert db_session.get(Plantilla, b.id).es_default is True

    def test_save_plantilla_de_otra_entidad_404(self, client, lab, db_session):
        otra_lab = Laboratorio(nombre='OTRO LAB')
        db_session.add(otra_lab); db_session.commit()

        p = Plantilla(entidad_tipo='laboratorio', entidad_id=otra_lab.id,
                      nombre='ajena', formato='xlsx', tipo_doc='pedido', config_json='{}')
        db_session.add(p); db_session.commit()

        resp = client.post(
            f'/partner/laboratorio/{lab.id}/plantillas/{p.id}/save',
            json={'nombre': 'hack'},
        )
        assert resp.status_code == 404


# ── Eliminar / duplicar ───────────────────────────────────────────────────────

class TestEliminarDuplicar:

    def test_delete_plantilla(self, client, lab, db_session):
        p = Plantilla(entidad_tipo='laboratorio', entidad_id=lab.id,
                      nombre='a borrar', formato='xlsx', tipo_doc='pedido', config_json='{}')
        db_session.add(p); db_session.commit()
        pid = p.id

        resp = client.post(f'/partner/laboratorio/{lab.id}/plantillas/{pid}/delete')
        assert resp.status_code == 302

        db_session.expire_all()
        assert db_session.get(Plantilla, pid) is None

    def test_duplicate_crea_copia(self, client, lab, db_session):
        orig = Plantilla(entidad_tipo='laboratorio', entidad_id=lab.id,
                         nombre='original', formato='xlsx', tipo_doc='pedido',
                         config_json='{"columnas":["codigo_barra"]}', es_default=True)
        db_session.add(orig); db_session.commit()

        resp = client.post(f'/partner/laboratorio/{lab.id}/plantillas/{orig.id}/duplicate')
        assert resp.status_code == 302

        db_session.expire_all()
        plants = db_session.query(Plantilla).filter_by(entidad_tipo='laboratorio', entidad_id=lab.id).all()
        assert len(plants) == 2
        copia = next(p for p in plants if p.id != orig.id)
        assert copia.nombre == 'original (copia)'
        assert copia.config_json == orig.config_json
        assert copia.es_default is False  # nunca duplica como default


# ── Migración legacy ──────────────────────────────────────────────────────────

class TestMigracionLegacy:

    def test_migra_export_template_a_plantilla(self, db_session):
        from database import _migrate_legacy_plantillas

        lab = Laboratorio(nombre='LEGACY LAB')
        db_session.add(lab); db_session.commit()
        et = ExportTemplate(
            laboratorio_id=lab.id,
            columns_json=json.dumps(['codigo_barra', 'cantidad']),
            custom_header='Header X',
        )
        db_session.add(et); db_session.commit()

        _migrate_legacy_plantillas()

        db_session.expire_all()
        plants = db_session.query(Plantilla).filter_by(
            entidad_tipo='laboratorio', entidad_id=lab.id
        ).all()
        assert len(plants) == 1
        p = plants[0]
        assert p.formato == 'xlsx'
        assert p.nombre == '[legacy] Plantilla XLSX'
        cfg = json.loads(p.config_json)
        assert cfg['columnas'] == ['codigo_barra', 'cantidad']
        assert cfg['custom_header'] == 'Header X'
        assert p.es_default is True

    def test_migracion_es_idempotente(self, db_session):
        from database import _migrate_legacy_plantillas

        lab = Laboratorio(nombre='IDEMP LAB')
        db_session.add(lab); db_session.commit()
        et = ExportTemplate(laboratorio_id=lab.id, columns_json='["x"]')
        db_session.add(et); db_session.commit()

        _migrate_legacy_plantillas()
        _migrate_legacy_plantillas()
        _migrate_legacy_plantillas()

        db_session.expire_all()
        count = db_session.query(Plantilla).filter_by(entidad_id=lab.id).count()
        assert count == 1

    def test_migra_plantilla_exportacion_proveedor(self, db_session):
        from database import _migrate_legacy_plantillas

        prov = Provider(razon_social='PROV LEGACY', cuit='30-LEG-1', tipo='proveedor')
        db_session.add(prov); db_session.commit()

        pe = PlantillaExportacion(proveedor_id=prov.id, nombre='Fija 80', extension='txt')
        db_session.add(pe); db_session.commit()
        db_session.add(PlantillaCampo(
            plantilla_id=pe.id, nombre='EAN', campo_sistema='codigo_barra',
            col_inicio=1, longitud=13, alineacion='L', relleno=' ',
        ))
        db_session.commit()

        _migrate_legacy_plantillas()

        db_session.expire_all()
        p = db_session.query(Plantilla).filter_by(
            entidad_tipo='proveedor', entidad_id=prov.id
        ).first()
        assert p is not None
        assert p.formato == 'txt_fijo'
        assert p.nombre == '[legacy] Fija 80'
        cfg = json.loads(p.config_json)
        assert len(cfg['campos']) == 1
        assert cfg['campos'][0]['campo'] == 'codigo_barra'
        assert cfg['campos'][0]['longitud'] == 13
