"""Colisiones de UNIQUE al materializar y matchear contra ObServer.

Los dos bugs que cubren estos tests no reventaban en la fila que los causaba
sino en el `commit()` posterior, arrastrando el batch entero. Por eso cada test
commitea explícitamente al final: sin el fix, falla ahí y no en el assert.
"""
import pytest
from sqlalchemy.exc import IntegrityError

import database
from database import Laboratorio, ObsLaboratorio, ObsProducto, Producto
from helpers import materializar_producto, resolver_laboratorio
from observer_matcher import match_productos


@pytest.fixture
def session():
    s = database.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def test_alfabeta_duplicado_no_revienta_el_batch(session):
    """Dos productos locales con el mismo alfabeta apuntan al mismo obs_id.

    Sin el set `tomados`, ambos reciben observer_id=1000 y el commit viola
    uq_productos_observer_id, revirtiendo todo lo linkeado en la corrida.
    """
    session.add(ObsProducto(observer_id=1000, descripcion='AMOXIDAL 500 COMP X 16',
                            codigo_alfabeta='ALFA1'))
    session.add(Producto(codigo_barra='7790001', descripcion='AMOXIDAL 500',
                         codigo_alfabeta='ALFA1'))
    session.add(Producto(codigo_barra='7790002', descripcion='AMOXIDAL 500 DUPLICADO',
                         codigo_alfabeta='ALFA1'))
    session.commit()

    stats = match_productos(session)
    session.commit()

    assert stats['linked_alfabeta'] == 1
    assert stats['colision'] == 1
    vinculados = session.query(Producto).filter(Producto.observer_id.isnot(None)).all()
    assert len(vinculados) == 1
    assert vinculados[0].observer_id == 1000


def test_lab_existente_por_nombre_se_reusa(session):
    """El lab ya está cargado por otra fuente, sin observer_id.

    Crear uno nuevo con el mismo nombre viola laboratorios_nombre_key. El helper
    tiene que reusar el existente y backfillear su observer_id.
    """
    session.add(Laboratorio(nombre='BAGO', observer_id=None, activo=True))
    session.add(ObsLaboratorio(observer_id=50, descripcion='BAGO'))
    session.commit()

    lab = resolver_laboratorio(session, 50)
    session.commit()

    assert lab.observer_id == 50
    assert session.query(Laboratorio).filter_by(nombre='BAGO').count() == 1


def test_materializar_con_lab_homonimo(session):
    """Camino completo: materializar un obs_producto cuyo lab ya existe local."""
    session.add(Laboratorio(nombre='ELEA', observer_id=None, activo=True))
    session.add(ObsLaboratorio(observer_id=60, descripcion='ELEA'))
    session.add(ObsProducto(observer_id=2000, descripcion='IBUPIRAC 600',
                            laboratorio_observer=60))
    session.commit()

    prod, err = materializar_producto(session, 2000)
    session.commit()

    assert err is None
    assert prod.laboratorio_id == session.query(Laboratorio).filter_by(nombre='ELEA').one().id
    assert session.query(Laboratorio).filter_by(nombre='ELEA').count() == 1


def test_el_bug_del_alfabeta_era_real(session):
    """Guardia: confirma que el UNIQUE existe y que sin el fix esto explotaría.

    Si alguien afloja el índice, este test avisa antes de que
    test_alfabeta_duplicado_no_revienta_el_batch pase por la razón equivocada.
    """
    session.add(ObsProducto(observer_id=3000, descripcion='X'))
    session.add(Producto(codigo_barra='7790003', observer_id=3000))
    session.add(Producto(codigo_barra='7790004', observer_id=3000))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
