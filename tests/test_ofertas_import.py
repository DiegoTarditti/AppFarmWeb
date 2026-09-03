"""Tests para routes/ofertas_import.py — resolución de laboratorio por EAN.

Cubre el fix de _guardar_modo_drog: el bulk EAN→laboratorio_id solo
consultaba Producto + producto_codigos_barra (catálogo interno, ~600
productos curados a mano), sin fallback a obs_codigos_barras. Un EAN de un
import de droguería (fuente externa) que no coincidiera con el "principal"
cargado localmente quedaba con laboratorio_id=NULL (contaba en `sin_lab`)
aunque el producto ya existiera y tuviera laboratorio asignado — mismo
patrón que el bug de Kellerhoff en compare_invoice_vs_erp.
"""
import pytest

import database
from database import Laboratorio, ObsCodigoBarras, ObsProducto, OfertaMinimo, Producto, Provider
from routes.ofertas_import import _guardar_modo_drog


@pytest.fixture
def session():
    s = database.SessionLocal()
    yield s
    s.rollback()
    s.close()


def test_lab_resuelto_por_catalogo_observer_sin_ean_local(session, flask_app):
    """El caso que fallaba: el EAN importado no está ni como principal ni en
    la 1-a-N local de Producto, pero ObServer ya sabe que corresponde a un
    Producto con laboratorio asignado."""
    prov = Provider(razon_social='DROGUERIA TEST', cuit='30-OFE-1')
    lab = Laboratorio(nombre='LAB TEST')
    session.add_all([prov, lab])
    session.flush()

    session.add(ObsProducto(observer_id=88001, descripcion='PROD OFERTA'))
    session.add(ObsCodigoBarras(id_codigo_barras=880011, producto_observer=88001,
                                codigo_barras='EAN_OFERTA_IMPORT', orden=1))
    session.add(Producto(codigo_barra='EAN_OFERTA_PPAL', descripcion='PROD OFERTA',
                         observer_id=88001, laboratorio_id=lab.id))
    session.commit()

    with flask_app.app_context():
        resp = _guardar_modo_drog({
            'drogueria_id': prov.id,
            'items': [{'ean': 'EAN_OFERTA_IMPORT', 'descripcion': 'PROD OFERTA'}],
        })
        data = resp.get_json()

    assert data['ok'] is True
    assert data['sin_lab'] == 0
    assert data['insertados'] == 1

    oferta = session.query(OfertaMinimo).filter_by(drogueria_id=prov.id).first()
    assert oferta.laboratorio_id == lab.id


def test_sin_bridge_sigue_quedando_sin_lab(session, flask_app):
    """Sin ningún vínculo (ni local ni ObServer) sigue sin poder deducir el
    laboratorio, como antes del fix — no se inventa un match."""
    prov = Provider(razon_social='DROGUERIA TEST 2', cuit='30-OFE-2')
    session.add(prov)
    session.commit()

    with flask_app.app_context():
        resp = _guardar_modo_drog({
            'drogueria_id': prov.id,
            'items': [{'ean': 'EAN_SIN_VINCULO', 'descripcion': 'X'}],
        })
        data = resp.get_json()

    assert data['sin_lab'] == 1
