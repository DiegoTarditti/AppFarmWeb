"""Tests de observer_source.sync_fraccionado_master.

Cubre el paso 0 (materializar el master para fraccionables de ObServer que no
tienen fila local) + el espejo del flag fraccionado + el envase desde obs.
ObServer manda en presentación.
"""
import pytest

import database
import observer_source
from database import ObsCodigoBarras, ObsProducto, Producto, ProductoAtributo


@pytest.fixture
def session():
    s = database.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _obs(s, oid, frac, envase=None, desc='Obs', baja=None):
    op = ObsProducto(observer_id=oid, descripcion=desc, es_fraccionable=frac,
                     cantidad_envase=envase, fecha_baja=baja)
    s.add(op)
    s.commit()
    return op


def test_materializa_fraccionable_sin_master(session):
    """Una fraccionable de obs sin fila de master se materializa y queda marcada."""
    _obs(session, 100, frac=True, envase=10, desc='ALIKAL SUELTO')
    session.add(ObsCodigoBarras(id_codigo_barras=1, producto_observer=100,
                                codigo_barras='7791234567890', orden=1))
    session.commit()

    st = observer_source.sync_fraccionado_master(session)
    session.commit()

    assert st['mat_nuevos'] == 1
    prod = session.query(Producto).filter_by(observer_id=100).one()
    assert prod.fraccionado is True
    assert prod.codigo_barra == '7791234567890'
    atr = session.query(ProductoAtributo).filter_by(producto_id=prod.id).one()
    assert atr.cantidad_envase == 10
    assert atr.fuente == 'observer'


def test_ignora_no_fraccionables(session):
    """Un producto obs no-fraccionable no se materializa."""
    _obs(session, 200, frac=False, envase=5)
    st = observer_source.sync_fraccionado_master(session)
    session.commit()
    assert st['mat_nuevos'] == 0
    assert session.query(Producto).filter_by(observer_id=200).first() is None


def test_flag_se_espeja_en_master_existente(session):
    """Master ya linkeado: el flag se actualiza desde obs aunque ya exista la fila."""
    _obs(session, 300, frac=True, envase=4)
    session.add(Producto(codigo_barra='EAN300', descripcion='Existente',
                         observer_id=300, fraccionado=False))
    session.commit()

    st = observer_source.sync_fraccionado_master(session)
    session.commit()

    assert st['mat_nuevos'] == 0  # ya existía
    prod = session.query(Producto).filter_by(observer_id=300).one()
    assert prod.fraccionado is True
    atr = session.query(ProductoAtributo).filter_by(producto_id=prod.id).one()
    assert atr.cantidad_envase == 4


def test_envase_observer_pisa_manual(session):
    """ObServer manda: el envase de obs pisa un cantidad_envase cargado a mano."""
    _obs(session, 400, frac=True, envase=12)
    p = Producto(codigo_barra='EAN400', descripcion='Manual',
                 observer_id=400, fraccionado=True)
    session.add(p)
    session.flush()
    session.add(ProductoAtributo(producto_id=p.id, cantidad_envase=3, fuente='manual'))
    session.commit()

    observer_source.sync_fraccionado_master(session)
    session.commit()

    atr = session.query(ProductoAtributo).filter_by(producto_id=p.id).one()
    assert atr.cantidad_envase == 12
    assert atr.fuente == 'observer'
