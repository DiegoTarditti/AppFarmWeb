"""Tests para helpers._eans_a_observer_ids y su uso en los detectores de
pack (pack_detector.detectar_packs, routes/purchase._detectar_packs_en_modulos).

Antes del fix, el bulk EAN→observer_id de estos 3 lugares (pack_detector.py,
routes/purchase.py, routes/modulos_import.py) consultaba SOLO Producto —
sin fallback a producto_codigos_barra ni obs_codigos_barras. Un EAN de
módulo Excel de laboratorio (fuente externa) que no fuera el "principal"
cargado a mano quedaba sin observer_id resuelto → se trataba como "nunca
vendido" (sin_ventas=True) aunque el producto tuviera historial real de
ventas — sesgaba la heurística de detección de packs hacia falsos
positivos. Mismo patrón que Kellerhoff (data_extract.py) y producto_flags.py.
"""
import pytest

import database
from database import (
    ObsCodigoBarras,
    ObsProducto,
    ObsVentaMensual,
    Producto,
    ProductoCodigoBarra,
)
from helpers import _eans_a_observer_ids


@pytest.fixture
def session():
    s = database.SessionLocal()
    yield s
    s.rollback()
    s.close()


class TestEansAObserverIds:

    def test_match_directo_producto(self, session):
        session.add(Producto(codigo_barra='EAN_DIRECTO', descripcion='X', observer_id=91001))
        session.add(ObsProducto(observer_id=91001, descripcion='X'))
        session.commit()
        assert _eans_a_observer_ids(session, {'EAN_DIRECTO'}) == {'EAN_DIRECTO': 91001}

    def test_match_via_tabla_1_a_n(self, session):
        p = Producto(codigo_barra='EAN_PPAL', descripcion='Y', observer_id=91002)
        session.add(p)
        session.add(ObsProducto(observer_id=91002, descripcion='Y'))
        session.flush()
        session.add(ProductoCodigoBarra(producto_id=p.id, codigo_barra='EAN_ALT_1AN',
                                        es_principal=False, fuente='manual'))
        session.commit()
        assert _eans_a_observer_ids(session, {'EAN_ALT_1AN'}) == {'EAN_ALT_1AN': 91002}

    def test_match_via_catalogo_observer_sin_producto_local(self, session):
        """El caso que fallaba: ningún Producto tiene este EAN cargado (ni
        principal ni 1-a-N), pero ObServer sí lo conoce."""
        session.add(ObsProducto(observer_id=91003, descripcion='Z'))
        session.add(ObsCodigoBarras(id_codigo_barras=910031, producto_observer=91003,
                                    codigo_barras='EAN_SOLO_OBSERVER', orden=1))
        session.commit()
        assert _eans_a_observer_ids(session, {'EAN_SOLO_OBSERVER'}) == {'EAN_SOLO_OBSERVER': 91003}

    def test_sin_match_no_aparece_en_el_dict(self, session):
        assert _eans_a_observer_ids(session, {'EAN_INEXISTENTE'}) == {}

    def test_bulk_mixto(self, session):
        session.add(Producto(codigo_barra='E1', descripcion='A', observer_id=91004))
        session.add(ObsProducto(observer_id=91004, descripcion='A'))
        session.add(ObsProducto(observer_id=91005, descripcion='B'))
        session.add(ObsCodigoBarras(id_codigo_barras=910051, producto_observer=91005,
                                    codigo_barras='E2', orden=1))
        session.commit()
        result = _eans_a_observer_ids(session, {'E1', 'E2', 'E3_INEXISTENTE'})
        assert result == {'E1': 91004, 'E2': 91005}


def _con_ventas(session, observer_id):
    session.add(ObsVentaMensual(id_farmacia=1, producto_observer=observer_id,
                                anio=2026, mes=8, unidades=10, monto=1000))


class TestPackDetectorConBridgeObserver:

    def test_ean_solo_en_observer_con_ventas_no_es_falso_positivo(self, session):
        """Antes del fix: como el EAN no resolvía (solo Producto se
        consultaba), tuvo_ventas() devolvía False siempre → 'sin_ventas'
        quedaba en True aunque el producto tuviera ventas reales, sumando
        una señal falsa de pack."""
        from pack_detector import detectar_packs

        session.add(ObsProducto(observer_id=92001, descripcion='PROD CON VENTAS'))
        session.add(ObsCodigoBarras(id_codigo_barras=920011, producto_observer=92001,
                                    codigo_barras='EAN_MODULO', orden=1))
        _con_ventas(session, 92001)
        session.commit()

        modules = [{'nombre': 'MOD X', 'items': [
            {'ean': 'EAN_MODULO', 'desc': 'PROD CON VENTAS', 'destacado': False},
        ]}]
        candidatos = detectar_packs(modules, session)
        # Sin ninguna señal (no amarillo, sin regex PACK, con ventas) no debería
        # aparecer como candidato a pack.
        assert candidatos == []


class TestDetectarPacksEnModulosPurchase:

    def test_ean_solo_en_observer_con_ventas_no_es_falso_positivo(self, session):
        from routes.purchase import _detectar_packs_en_modulos

        session.add(ObsProducto(observer_id=92002, descripcion='PROD CON VENTAS 2'))
        session.add(ObsCodigoBarras(id_codigo_barras=920021, producto_observer=92002,
                                    codigo_barras='EAN_MODULO_2', orden=1))
        _con_ventas(session, 92002)
        session.commit()

        modules = [{'nombre': 'MOD Y', 'items': [
            {'ean': 'EAN_MODULO_2', 'desc': 'PROD CON VENTAS 2', 'destacado': False},
        ]}]
        candidatos = _detectar_packs_en_modulos(modules, session)
        assert candidatos == []
