"""Tests del fix: helpers._upsert_producto y _bulk_upsert_productos NO crean
productos fantasma (observer_id=NULL) cuando el EAN existe en
``obs_codigos_barras``.

Regresión: antes del fix, importar una factura con un EAN nuevo creaba un
Producto sin observer_id ni fuente_creacion. Eso rompía el bridge a ObServer
y dejaba el badge "en catálogo" del wizard de módulos como falso negativo.
"""
import pytest

import database
from database import (Laboratorio, ObsCodigoBarras, ObsLaboratorio,
                      ObsProducto, Producto)
from helpers import _bulk_upsert_productos, _upsert_producto


@pytest.fixture
def db_session():
    s = database.SessionLocal()
    yield s
    s.rollback()
    s.close()


@pytest.fixture
def obs_seed(db_session):
    """Sembrar un laboratorio + producto + EAN en el maestro ObServer."""
    db_session.add(ObsLaboratorio(observer_id=9001, descripcion='ROEMMERS-TEST'))
    db_session.add(ObsProducto(
        observer_id=999001,
        descripcion='TESTAMOX 500 mg COM x 16',
        laboratorio_observer=9001,
        es_habilitado_venta=True,
        requiere_cadena_frio=False,
        es_fraccionable=False,
    ))
    db_session.add(ObsCodigoBarras(
        id_codigo_barras=8881,
        producto_observer=999001,
        codigo_barras='7790000000001',
        orden=1,
    ))
    db_session.commit()
    yield
    db_session.query(ObsCodigoBarras).filter_by(id_codigo_barras=8881).delete()
    db_session.query(ObsProducto).filter_by(observer_id=999001).delete()
    db_session.query(ObsLaboratorio).filter_by(observer_id=9001).delete()
    db_session.query(Producto).filter_by(codigo_barra='7790000000001').delete()
    db_session.query(Producto).filter_by(codigo_barra='7790000000999').delete()
    db_session.query(Laboratorio).filter_by(observer_id=9001).delete()
    db_session.commit()


def test_upsert_materializa_si_esta_en_obs(db_session, obs_seed):
    """Si el EAN está en obs_codigos_barras, el Producto se crea con
    observer_id linkeado y fuente='materializar_obs'."""
    _upsert_producto(db_session, '7790000000001', 'desc del import')
    db_session.commit()

    prod = db_session.query(Producto).filter_by(
        codigo_barra='7790000000001').first()
    assert prod is not None
    assert prod.observer_id == 999001, 'debe linkear contra ObServer'
    assert prod.fuente_creacion == 'materializar_obs'
    # La descripción debe venir de ObServer, no del import
    assert prod.descripcion == 'TESTAMOX 500 mg COM x 16'


def test_upsert_marca_huerfano_si_no_esta_en_obs(db_session, obs_seed):
    """Si el EAN NO está en ObServer, se crea como huérfano con marca
    explícita (en vez del NULL silencioso que existía antes del fix)."""
    _upsert_producto(db_session, '7790000000999', 'EAN privado')
    db_session.commit()

    prod = db_session.query(Producto).filter_by(
        codigo_barra='7790000000999').first()
    assert prod is not None
    assert prod.observer_id is None
    assert prod.fuente_creacion == 'import_huerfano', \
        'debe marcarse para auditoria, no quedar con fuente=NULL'


def test_bulk_upsert_no_genera_fantasmas(db_session, obs_seed):
    """Regresion del bug original: _bulk_upsert_productos creaba un Producto
    sin observer_id ni fuente para un EAN que estaba en obs_codigos_barras.

    Reproduce el escenario de factura que disparó los 9 fantasmas hoy."""
    _bulk_upsert_productos(db_session, [
        ('7790000000001', 'desc factura', 3500.00, None),
        ('7790000000999', 'EAN raro', 1000.00, None),
    ])
    db_session.commit()

    materializado = db_session.query(Producto).filter_by(
        codigo_barra='7790000000001').first()
    assert materializado.observer_id == 999001
    assert materializado.fuente_creacion == 'materializar_obs'

    huerfano = db_session.query(Producto).filter_by(
        codigo_barra='7790000000999').first()
    assert huerfano.fuente_creacion == 'import_huerfano'

    # Asercion clave: NO debe haber filas con observer_id=NULL Y fuente=NULL
    # entre las recien creadas. Antes del fix, ambas las tendrian asi.
    fantasmas = (db_session.query(Producto)
                 .filter(Producto.codigo_barra.in_(
                     ['7790000000001', '7790000000999']))
                 .filter(Producto.observer_id.is_(None),
                         Producto.fuente_creacion.is_(None))
                 .count())
    assert fantasmas == 0, 'no se deben crear fantasmas (obs_id=NULL + fuente=NULL)'
