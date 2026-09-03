"""Tests para routes/producto_flags.py — resolución de producto por EAN.

Cubre el fix de _resolver_producto_por_ean: antes solo miraba Producto
(principal + alt1/2/3) y producto_codigos_barra, sin fallback a
obs_codigos_barras — un EAN válido de ObServer que no fuera el "principal"
local devolvía 404 ("Producto sin ficha master local. Cataloga el producto
primero.") aunque el producto ya existiera. Mismo patrón que el bug de
Kellerhoff en compare_invoice_vs_erp.
"""
import pytest

import database
from database import Producto, ProductoCodigoBarra
from routes.producto_flags import _resolver_producto_por_ean


@pytest.fixture
def session():
    s = database.SessionLocal()
    yield s
    s.rollback()
    s.close()


class TestResolverProductoPorEan:

    def test_match_por_codigo_principal(self, session):
        p = Producto(codigo_barra='EAN_PRINCIPAL', descripcion='X')
        session.add(p)
        session.flush()
        assert _resolver_producto_por_ean(session, 'EAN_PRINCIPAL').id == p.id

    def test_match_por_tabla_1_a_n(self, session):
        p = Producto(codigo_barra='EAN_PPAL2', descripcion='Y')
        session.add(p)
        session.flush()
        session.add(ProductoCodigoBarra(producto_id=p.id, codigo_barra='EAN_ALT',
                                        es_principal=False, fuente='manual'))
        session.flush()
        assert _resolver_producto_por_ean(session, 'EAN_ALT').id == p.id

    def test_match_por_catalogo_observer_sin_ean_local(self, session):
        """El caso que fallaba: el EAN no está ni como principal ni en la
        1-a-N local, pero ObServer ya sabe que corresponde a este Producto
        (vía observer_id) — antes del fix esto devolvía None."""
        from database import ObsCodigoBarras, ObsProducto
        session.add(ObsProducto(observer_id=77001, descripcion='Z'))
        session.add(ObsCodigoBarras(id_codigo_barras=770011, producto_observer=77001,
                                    codigo_barras='EAN_OBSERVER_ALT', orden=1))
        p = Producto(codigo_barra='EAN_PPAL3', descripcion='Z', observer_id=77001)
        session.add(p)
        session.flush()
        assert _resolver_producto_por_ean(session, 'EAN_OBSERVER_ALT').id == p.id

    def test_sin_match_devuelve_none(self, session):
        assert _resolver_producto_por_ean(session, 'EAN_INEXISTENTE') is None
