"""Tests unitarios para data_extract.py.
Usa SQLite en memoria para aislar cada test de la DB real.
"""

import datetime
import pytest
import database
from database import (
    Invoice, InvoiceItem, ErpStock, Provider, StockDifference,
    BarcodeMapping, Producto,
)
from data_extract import (
    _normalize,
    compare_invoice_vs_erp,
    save_invoice_to_db,
    save_erp_to_db,
    save_differences,
    save_barcode_mapping,
    get_erp_items_with_issues,
    carga_erp_actual,
    recalcular_diferencias,
    create_claim,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def session():
    """Sesión limpia para cada test — hace rollback al terminar."""
    s = database.SessionLocal()
    yield s
    s.rollback()
    s.close()


def _make_invoice(session, items, proveedor_cuit='30-111-1', match_strategy='barcode'):
    """Helper: crea proveedor + factura + items en DB."""
    prov = Provider(razon_social='TEST S.A.', cuit=proveedor_cuit,
                    match_strategy=match_strategy)
    session.add(prov)
    session.flush()

    inv = Invoice(
        numero_factura='F001', fecha=datetime.date.today(),
        proveedor_razon='TEST S.A.', proveedor_cuit=proveedor_cuit,
        tipo_comprobante='FAC', total=0,
    )
    session.add(inv)
    session.flush()

    for it in items:
        session.add(InvoiceItem(
            factura_id=inv.id,
            codigo_barra=it.get('codigo_barra'),
            descripcion=it.get('descripcion'),
            cantidad=it.get('cantidad', 1),
        ))
    session.flush()
    return inv


def _make_erp(session, items, invoice=None):
    """Helper: carga ítems ERP por el mismo camino que producción.

    Vincula la carga a la factura (Invoice.erp_carga_id). Sin ese vínculo el cruce
    trata a la factura como "sin ERP propio" y no compara nada — que es justamente lo
    que evita cruzarla contra el ingreso de otro chequeo. Pasar invoice=None simula
    ese caso a propósito.
    """
    carga_id = save_erp_to_db(session, items)
    if invoice is not None:
        invoice.erp_carga_id = carga_id
        session.flush()
    return carga_id


# ── _normalize ────────────────────────────────────────────────────────────────

class TestNormalize:
    def test_lowercase(self):
        assert _normalize('IBUPROFENO 400MG') == 'ibuprofeno 400mg'

    def test_extra_spaces(self):
        assert _normalize('  ibuprofeno   400mg  ') == 'ibuprofeno 400mg'

    def test_none(self):
        assert _normalize(None) == ''

    def test_empty(self):
        assert _normalize('') == ''

    def test_already_normalized(self):
        assert _normalize('amoxicilina 500mg') == 'amoxicilina 500mg'


# ── save_invoice_to_db ────────────────────────────────────────────────────────

class TestSaveInvoiceToDB:
    def test_fac_positive_amounts(self, session):
        data = {
            'numero_factura': 'F100', 'fecha': datetime.date.today(),
            'proveedor_razon': 'PROV X', 'proveedor_cuit': '20-999-9',
            'total': 1000.0,
            'items': [{'codigo_barra': '111', 'descripcion': 'PROD A',
                        'cantidad': 2, 'precio_unitario': 500.0, 'importe': 1000.0}],
        }
        inv = save_invoice_to_db(session, data, tipo_comprobante='FAC')
        assert float(inv.total) == 1000.0
        item = session.query(InvoiceItem).filter_by(factura_id=inv.id).first()
        assert float(item.precio_unitario) == 500.0
        assert float(item.importe) == 1000.0

    def test_ncr_negative_amounts(self, session):
        data = {
            'numero_factura': 'NC100', 'fecha': datetime.date.today(),
            'proveedor_razon': 'PROV Y', 'proveedor_cuit': '20-888-8',
            'total': 500.0,
            'items': [{'codigo_barra': '222', 'descripcion': 'PROD B',
                        'cantidad': 1, 'precio_unitario': 500.0, 'importe': 500.0}],
        }
        inv = save_invoice_to_db(session, data, tipo_comprobante='NCR')
        assert float(inv.total) == -500.0
        item = session.query(InvoiceItem).filter_by(factura_id=inv.id).first()
        assert float(item.precio_unitario) == -500.0
        assert float(item.importe) == -500.0

    def test_total_articulos_from_items(self, session):
        data = {
            'numero_factura': 'F101', 'fecha': datetime.date.today(),
            'proveedor_razon': 'PROV Z', 'total': 0,
            'items': [
                {'codigo_barra': '1', 'descripcion': 'A', 'cantidad': 1},
                {'codigo_barra': '2', 'descripcion': 'B', 'cantidad': 1},
                {'codigo_barra': '3', 'descripcion': 'C', 'cantidad': 1},
            ],
        }
        inv = save_invoice_to_db(session, data)
        assert inv.total_articulos == 3


# ── save_erp_to_db ────────────────────────────────────────────────────────────

class TestSaveErpToDB:
    def test_replaces_existing(self, session):
        session.add(ErpStock(codigo_barra='OLD', descripcion='VIEJO', cantidad=5))
        session.flush()
        save_erp_to_db(session, [{'codigo_barra': 'NEW', 'descripcion': 'NUEVO', 'cantidad': 3}])
        items = session.query(ErpStock).all()
        assert len(items) == 1
        assert items[0].codigo_barra == 'NEW'

    def test_empty_clears_all(self, session):
        session.add(ErpStock(codigo_barra='X', descripcion='X', cantidad=1))
        session.flush()
        save_erp_to_db(session, [])
        assert session.query(ErpStock).count() == 0


# ── compare_invoice_vs_erp ───────────────────────────────────────────────────

class TestCompareInvoiceVsErp:

    def test_exact_barcode_match_no_difference(self, session):
        inv = _make_invoice(session, [{'codigo_barra': 'BC001', 'descripcion': 'PROD A', 'cantidad': 5}])
        _make_erp(session, [{'codigo_barra': 'BC001', 'descripcion': 'PROD A', 'cantidad': 5}], inv)
        diffs = compare_invoice_vs_erp(session, inv.id)
        assert diffs == []

    def test_exact_barcode_match_with_difference(self, session):
        inv = _make_invoice(session, [{'codigo_barra': 'BC002', 'descripcion': 'PROD B', 'cantidad': 10}])
        _make_erp(session, [{'codigo_barra': 'BC002', 'descripcion': 'PROD B', 'cantidad': 7}], inv)
        diffs = compare_invoice_vs_erp(session, inv.id)
        assert len(diffs) == 1
        assert diffs[0]['diferencia'] == 3
        assert diffs[0]['codigo_barra'] == 'BC002'

    def test_no_match_reports_not_found(self, session):
        inv = _make_invoice(session, [{'codigo_barra': 'BC999', 'descripcion': 'INEXISTENTE', 'cantidad': 2}])
        _make_erp(session, [{'codigo_barra': 'OTHER', 'descripcion': 'OTRO', 'cantidad': 2}], inv)
        diffs = compare_invoice_vs_erp(session, inv.id)
        assert len(diffs) == 1
        assert 'no encontrado' in diffs[0]['observaciones'].lower()
        assert diffs[0]['cantidad_erp'] == 0

    def test_description_match_step2(self, session):
        """Barcode diferente pero descripción igual → coincide por descripción."""
        inv = _make_invoice(session, [{'codigo_barra': 'FACBC', 'descripcion': 'Ibuprofeno 400mg', 'cantidad': 4}])
        _make_erp(session, [{'codigo_barra': 'ERPBC', 'descripcion': 'IBUPROFENO 400MG', 'cantidad': 4}], inv)
        diffs = compare_invoice_vs_erp(session, inv.id)
        assert diffs == []

    def test_description_match_registers_in_observaciones(self, session):
        """Coincidencia por descripción con diferencia registra el tipo."""
        inv = _make_invoice(session, [{'codigo_barra': 'FAC01', 'descripcion': 'Amoxicilina 500mg', 'cantidad': 3}])
        _make_erp(session, [{'codigo_barra': 'ERP01', 'descripcion': 'AMOXICILINA 500MG', 'cantidad': 1}], inv)
        diffs = compare_invoice_vs_erp(session, inv.id)
        assert len(diffs) == 1
        assert 'descripción' in diffs[0]['observaciones'].lower()

    def test_mapping_step3(self, session):
        """Sin match por barcode ni descripción → usa BarcodeMapping guardado."""
        prov = Provider(razon_social='PROV MAP', cuit='30-MAP-1', match_strategy='barcode')
        session.add(prov)
        session.flush()

        inv = Invoice(
            numero_factura='FM01', fecha=datetime.date.today(),
            proveedor_razon='PROV MAP', proveedor_cuit='30-MAP-1',
            tipo_comprobante='FAC', total=0,
        )
        session.add(inv)
        session.flush()
        session.add(InvoiceItem(factura_id=inv.id, codigo_barra='FAC_BC', descripcion='PROD X', cantidad=5))
        session.flush()

        _make_erp(session, [{'codigo_barra': 'ERP_BC', 'descripcion': 'PROD DISTINTO', 'cantidad': 5}], inv)

        session.add(BarcodeMapping(
            proveedor_id=prov.id,
            codigo_barra_factura='FAC_BC',
            codigo_barra_erp='ERP_BC',
        ))
        session.flush()

        diffs = compare_invoice_vs_erp(session, inv.id)
        assert diffs == []

    def test_strategy_descripcion_first(self, session):
        """match_strategy='descripcion' busca por descripción primero."""
        inv = _make_invoice(
            session,
            [{'codigo_barra': 'COD_FAC', 'descripcion': 'Losartan 50mg', 'cantidad': 2}],
            match_strategy='descripcion',
        )
        # ERP tiene barcode diferente pero descripción igual
        _make_erp(session, [{'codigo_barra': 'COD_ERP', 'descripcion': 'LOSARTAN 50MG', 'cantidad': 2}], inv)
        diffs = compare_invoice_vs_erp(session, inv.id)
        assert diffs == []

    def test_multiple_items_mixed(self, session):
        """Varios ítems: uno con match, uno sin match."""
        inv = _make_invoice(session, [
            {'codigo_barra': 'MATCH', 'descripcion': 'PROD M', 'cantidad': 3},
            {'codigo_barra': 'NOMATCH', 'descripcion': 'PROD N', 'cantidad': 1},
        ])
        _make_erp(session, [{'codigo_barra': 'MATCH', 'descripcion': 'PROD M', 'cantidad': 3}], inv)
        diffs = compare_invoice_vs_erp(session, inv.id)
        assert len(diffs) == 1
        assert diffs[0]['codigo_barra'] == 'NOMATCH'


# ── save_barcode_mapping ──────────────────────────────────────────────────────

class TestSaveBarcodeMapping:
    def test_creates_new(self, session):
        prov = Provider(razon_social='P1', cuit='30-P1-1')
        session.add(prov)
        session.flush()
        save_barcode_mapping(session, prov.id, 'FAC_BC', 'ERP_BC', 'desc fac', 'desc erp')
        m = session.query(BarcodeMapping).filter_by(proveedor_id=prov.id).first()
        assert m.codigo_barra_erp == 'ERP_BC'

    def test_updates_existing(self, session):
        prov = Provider(razon_social='P2', cuit='30-P2-1')
        session.add(prov)
        session.flush()
        save_barcode_mapping(session, prov.id, 'FAC_BC', 'ERP_OLD')
        save_barcode_mapping(session, prov.id, 'FAC_BC', 'ERP_NEW')
        m = session.query(BarcodeMapping).filter_by(proveedor_id=prov.id).first()
        assert m.codigo_barra_erp == 'ERP_NEW'
        assert session.query(BarcodeMapping).filter_by(proveedor_id=prov.id).count() == 1


# ── get_erp_items_with_issues ─────────────────────────────────────────────────

class TestErpDeOtroChequeo:
    """erp_stock es global y guarda UNA carga a la vez.

    Bug real: como el Excel del ERP es opcional al subir la factura, la tabla se
    quedaba con el ingreso del chequeo anterior y el cruce mostraba esas diferencias
    como si fueran de la factura nueva.
    """

    def test_factura_sin_erp_no_se_cruza_contra_la_carga_anterior(self, session):
        # Chequeo 1: factura con su Excel.
        inv_a = _make_invoice(session, [{'codigo_barra': 'BC_A', 'descripcion': 'PROD A', 'cantidad': 10}])
        _make_erp(session, [{'codigo_barra': 'BC_A', 'descripcion': 'PROD A', 'cantidad': 7}], inv_a)
        assert len(compare_invoice_vs_erp(session, inv_a.id)) == 1

        # Chequeo 2: factura subida SIN Excel. erp_stock sigue teniendo el ingreso de A.
        inv_b = _make_invoice(session, [{'codigo_barra': 'BC_B', 'descripcion': 'PROD B', 'cantidad': 3}],
                              proveedor_cuit='30-222-2')
        assert compare_invoice_vs_erp(session, inv_b.id) == []
        assert get_erp_items_with_issues(session, inv_b.id) == []

    def test_una_carga_nueva_desvincula_la_factura_anterior(self, session):
        inv_a = _make_invoice(session, [{'codigo_barra': 'BC_A', 'descripcion': 'PROD A', 'cantidad': 10}])
        _make_erp(session, [{'codigo_barra': 'BC_A', 'descripcion': 'PROD A', 'cantidad': 7}], inv_a)
        assert len(compare_invoice_vs_erp(session, inv_a.id)) == 1

        # Otra factura carga su Excel y pisa erp_stock: A se queda sin su ingreso.
        inv_b = _make_invoice(session, [{'codigo_barra': 'BC_B', 'descripcion': 'PROD B', 'cantidad': 3}],
                              proveedor_cuit='30-222-2')
        _make_erp(session, [{'codigo_barra': 'BC_B', 'descripcion': 'PROD B', 'cantidad': 3}], inv_b)
        assert compare_invoice_vs_erp(session, inv_a.id) == []
        assert get_erp_items_with_issues(session, inv_a.id) == []

    def test_cada_carga_recibe_un_id_estrictamente_mayor(self, session):
        """Dos cargas en el mismo milisegundo no pueden compartir id."""
        inv = _make_invoice(session, [{'codigo_barra': 'X', 'descripcion': 'PROD X', 'cantidad': 1}])
        ids = [_make_erp(session, [{'codigo_barra': 'X', 'descripcion': 'PROD X', 'cantidad': 1}], inv)
               for _ in range(5)]
        assert ids == sorted(set(ids)), ids
        assert carga_erp_actual(session) == ids[-1]

    def test_batch_comparte_carga_entre_facturas(self, session):
        """Un Excel para N facturas: todas cruzan contra la misma carga."""
        inv_a = _make_invoice(session, [{'codigo_barra': 'BC_A', 'descripcion': 'PROD A', 'cantidad': 5}])
        inv_b = _make_invoice(session, [{'codigo_barra': 'BC_B', 'descripcion': 'PROD B', 'cantidad': 9}],
                              proveedor_cuit='30-222-2')
        carga_id = _make_erp(session, [
            {'codigo_barra': 'BC_A', 'descripcion': 'PROD A', 'cantidad': 5},
            {'codigo_barra': 'BC_B', 'descripcion': 'PROD B', 'cantidad': 4},
        ], inv_a)
        inv_b.erp_carga_id = carga_id
        session.flush()

        assert compare_invoice_vs_erp(session, inv_a.id) == []      # coincide
        assert len(compare_invoice_vs_erp(session, inv_b.id)) == 1  # 9 vs 4


class TestRecalcularDiferencias:
    """save_differences borra y reinserta: recalcular sin ERP propio borraría las buenas."""

    def test_no_borra_las_diferencias_si_el_erp_ya_no_es_de_la_factura(self, session):
        inv = _make_invoice(session, [{'codigo_barra': 'BC_R', 'descripcion': 'PROD R', 'cantidad': 10}])
        _make_erp(session, [{'codigo_barra': 'BC_R', 'descripcion': 'PROD R', 'cantidad': 6}], inv)
        assert recalcular_diferencias(session, inv.id) is True
        guardadas = session.query(StockDifference).filter_by(factura_id=inv.id).all()
        assert len(guardadas) == 1 and guardadas[0].diferencia == 4

        # Otra carga pisa erp_stock. Recalcular ahora no debe tocar nada.
        _make_erp(session, [{'codigo_barra': 'OTRA', 'descripcion': 'OTRA COSA', 'cantidad': 1}])
        assert recalcular_diferencias(session, inv.id) is False
        siguen = session.query(StockDifference).filter_by(factura_id=inv.id).all()
        assert len(siguen) == 1 and siguen[0].diferencia == 4

    def test_recalcula_cuando_el_erp_es_el_de_la_factura(self, session):
        inv = _make_invoice(session, [{'codigo_barra': 'BC_S', 'descripcion': 'PROD S', 'cantidad': 10}])
        _make_erp(session, [{'codigo_barra': 'BC_S', 'descripcion': 'PROD S', 'cantidad': 2}], inv)
        assert recalcular_diferencias(session, inv.id) is True
        assert session.query(StockDifference).filter_by(factura_id=inv.id).one().diferencia == 8

        # Llega el ingreso completo: la diferencia se va.
        _make_erp(session, [{'codigo_barra': 'BC_S', 'descripcion': 'PROD S', 'cantidad': 10}], inv)
        assert recalcular_diferencias(session, inv.id) is True
        assert session.query(StockDifference).filter_by(factura_id=inv.id).all() == []


class TestGetErpItemsWithIssues:
    def test_returns_erp_not_in_invoice(self, session):
        inv = _make_invoice(session, [{'codigo_barra': 'A', 'descripcion': 'PROD A', 'cantidad': 1}])
        _make_erp(session, [
            {'codigo_barra': 'A', 'descripcion': 'PROD A', 'cantidad': 1},
            {'codigo_barra': 'B', 'descripcion': 'PROD B', 'cantidad': 1},
        ], inv)
        issues = get_erp_items_with_issues(session, inv.id)
        assert len(issues) == 1
        assert issues[0].codigo_barra == 'B'

    def test_empty_when_all_match(self, session):
        inv = _make_invoice(session, [{'codigo_barra': 'C', 'descripcion': 'PROD C', 'cantidad': 2}])
        _make_erp(session, [{'codigo_barra': 'C', 'descripcion': 'PROD C', 'cantidad': 2}], inv)
        assert get_erp_items_with_issues(session, inv.id) == []

    def test_empty_erp(self, session):
        inv = _make_invoice(session, [{'codigo_barra': 'D', 'descripcion': 'PROD D', 'cantidad': 1}])
        _make_erp(session, [], inv)
        assert get_erp_items_with_issues(session, inv.id) == []


# ── create_claim ──────────────────────────────────────────────────────────────

class TestCreateClaim:
    def test_creates_claim_with_items(self, session):
        inv = _make_invoice(session, [{'codigo_barra': 'BC_CLM', 'descripcion': 'PROD CLM', 'cantidad': 5}])
        _make_erp(session, [{'codigo_barra': 'OTHER_CLM', 'descripcion': 'OTRO', 'cantidad': 3}], inv)
        diffs_data = compare_invoice_vs_erp(session, inv.id)
        save_differences(session, inv.id, diffs_data)

        diff = session.query(StockDifference).filter_by(factura_id=inv.id).first()
        claim = create_claim(session, inv.id, [diff.id])
        assert claim.estado == 'ABIERTO'
        assert claim.factura_id == inv.id
        assert len(claim.items) == 1

    def test_raises_on_invalid_invoice(self, session):
        with pytest.raises(ValueError, match='Factura no encontrada'):
            create_claim(session, 999999, [])
