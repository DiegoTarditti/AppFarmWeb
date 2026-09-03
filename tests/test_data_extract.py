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
    sugerir_cruce_manual,
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

    def _obs_bridge(self, session, observer_id, ean_factura, ean_erp, descripcion='PROD OBS'):
        """Registra en el catálogo de ObServer que dos EAN distintos (el de
        factura del proveedor y el que ObServer usa al ingreso) son el mismo
        producto_observer — sin que exista ningún Producto local."""
        from database import ObsCodigoBarras, ObsProducto
        session.add(ObsProducto(observer_id=observer_id, descripcion=descripcion))
        session.add(ObsCodigoBarras(id_codigo_barras=observer_id * 10 + 1,
                                    producto_observer=observer_id,
                                    codigo_barras=ean_factura, orden=1))
        session.add(ObsCodigoBarras(id_codigo_barras=observer_id * 10 + 2,
                                    producto_observer=observer_id,
                                    codigo_barras=ean_erp, orden=2))
        session.flush()

    def test_observer_bridge_step4_sin_diferencia(self, session):
        """Caso Kellerhoff: EAN de factura y EAN de ingreso son distintos y
        ningún Producto local los tiene cargados, pero ObServer ya sabe que
        son el mismo producto_observer → matchea, sin diferencia real."""
        self._obs_bridge(session, 90001, 'FAC_EAN_KH', 'ERP_EAN_KH', 'ACEMUK 600')
        inv = _make_invoice(session, [{'codigo_barra': 'FAC_EAN_KH', 'descripcion': 'ACEMUK 600', 'cantidad': 3}])
        _make_erp(session, [{'codigo_barra': 'ERP_EAN_KH', 'descripcion': 'ACEMUK 600', 'cantidad': 3}], inv)
        diffs = compare_invoice_vs_erp(session, inv.id)
        assert diffs == []

    def test_observer_bridge_step4_con_diferencia(self, session):
        """Mismo bridge, pero cantidad distinta → sigue reportando la
        diferencia real (el bridge resuelve el match, no la esconde)."""
        self._obs_bridge(session, 90002, 'FAC_EAN_2', 'ERP_EAN_2', 'DELTISONA')
        inv = _make_invoice(session, [{'codigo_barra': 'FAC_EAN_2', 'descripcion': 'DELTISONA B 40MG CPR X20', 'cantidad': 5}])
        _make_erp(session, [{'codigo_barra': 'ERP_EAN_2', 'descripcion': 'DELTISONA-B 40 mg COM x 20 (ObServer)', 'cantidad': 2}], inv)
        diffs = compare_invoice_vs_erp(session, inv.id)
        assert len(diffs) == 1
        assert diffs[0]['diferencia'] == 3
        assert 'observer' in diffs[0]['observaciones'].lower()

    def test_observer_bridge_no_aplica_sin_bridge(self, session):
        """Sin el bridge de ObServer (EAN nunca vistos por ese catálogo)
        sigue cayendo en 'no encontrado', como antes de este fix."""
        inv = _make_invoice(session, [{'codigo_barra': 'FAC_SIN_BRIDGE', 'descripcion': 'X', 'cantidad': 1}])
        _make_erp(session, [{'codigo_barra': 'ERP_SIN_BRIDGE', 'descripcion': 'Y', 'cantidad': 1}], inv)
        diffs = compare_invoice_vs_erp(session, inv.id)
        assert len(diffs) == 1
        assert 'no encontrado' in diffs[0]['observaciones'].lower()

    # ── pack↔unidad: la factura cuenta cajas, el ingreso unidades ─────────────

    def _obs_producto(self, session, observer_id, ean, cantidad_envase, descripcion='PROD'):
        """Un producto del catálogo ObServer con su EAN y su contenido de envase."""
        from database import ObsCodigoBarras, ObsProducto
        session.add(ObsProducto(observer_id=observer_id, descripcion=descripcion,
                                cantidad_envase=cantidad_envase))
        session.add(ObsCodigoBarras(id_codigo_barras=observer_id * 10 + 1,
                                    producto_observer=observer_id,
                                    codigo_barras=ean, orden=1))
        session.flush()

    def test_pack_equivalencia_resuelve_la_diferencia(self, session):
        """Caso OPTAMOX real (2026-09-03): la factura trae 2 packs de 10 y el
        ingreso registra 20 unidades. El pack ya estaba en `pack_equivalencias`
        hace rato — el cruce simplemente no miraba la tabla."""
        from database import PackEquivalencia
        session.add(PackEquivalencia(ean_pack='EAN_PACK_OPT', ean_unidad='EAN_UNID_OPT',
                                     cantidad=10, desc_pack='OPTAMOX PACK X 10'))
        session.flush()
        inv = _make_invoice(session, [{'codigo_barra': 'EAN_PACK_OPT',
                                       'descripcion': 'OPTAMOX DUO 1 GR CPR X 8 PACK X 10',
                                       'cantidad': 2}])
        _make_erp(session, [{'codigo_barra': 'EAN_PACK_OPT',
                             'descripcion': 'OPTAMOX DUO', 'cantidad': 20}], inv)
        assert compare_invoice_vs_erp(session, inv.id) == []

    def test_cantidad_envase_resuelve_sin_pack_equivalencia(self, session):
        """Caso LOTRIAL: no está en `pack_equivalencias`, pero ObServer sabe que
        el envase facturado trae 100 y el del ingreso 10 → factor 10.

        Ojo con la precondición: pack y unidad son productos DISTINTOS de
        ObServer, así que el puente por catálogo (que exige el mismo
        producto_observer) no los une. Para que la conversión llegue a
        aplicarse, los dos lados tienen que haber matcheado antes por otra vía
        — acá por descripción."""
        self._obs_producto(session, 91001, 'EAN_PACK_LOT', 100, 'LOTRIAL (PACK 10X10) COM x 100')
        self._obs_producto(session, 91002, 'EAN_UNID_LOT', 10, 'LOTRIAL 10 mg (PACK) COM x 10')
        inv = _make_invoice(session, [{'codigo_barra': 'EAN_PACK_LOT',
                                       'descripcion': 'LOTRIAL 10 MG', 'cantidad': 2}])
        _make_erp(session, [{'codigo_barra': 'EAN_UNID_LOT',
                             'descripcion': 'LOTRIAL 10 MG', 'cantidad': 20}], inv)
        assert compare_invoice_vs_erp(session, inv.id) == []

    def test_multiplo_exacto_sin_pack_declarado_sigue_siendo_diferencia(self, session):
        """EL test que define el criterio. Caso TAPON P/OIDOS real: facturado 2,
        recibido 1 — "múltiplo exacto x2" para cualquier detector aritmético, y
        un faltante de verdad. Sin pack declarado (ni tabla ni cantidad_envase
        distinta) NO se convierte nada. Inferir el factor de que las cantidades
        den múltiplo habría silenciado 5 faltantes reales de 14."""
        inv = _make_invoice(session, [{'codigo_barra': 'EAN_TAPON',
                                       'descripcion': 'TAPON P/OIDOS OTOSAN 1 PAR AD', 'cantidad': 2}])
        _make_erp(session, [{'codigo_barra': 'EAN_TAPON',
                             'descripcion': 'TAPON P/OIDOS OTOSAN', 'cantidad': 1}], inv)
        diffs = compare_invoice_vs_erp(session, inv.id)
        assert len(diffs) == 1
        assert diffs[0]['diferencia'] == 1
        assert diffs[0]['observaciones'] == 'No coincide con ERP'

    def test_mismo_producto_de_ambos_lados_no_convierte(self, session):
        """Si el ingreso quedó bajo el MISMO producto que factura, el cociente de
        cantidad_envase es 1: no hay pack que convertir aunque el envase sea
        grande. Evita normalizar contra sí mismo."""
        self._obs_producto(session, 91003, 'EAN_MISMO', 100, 'ALGO COM x 100')
        inv = _make_invoice(session, [{'codigo_barra': 'EAN_MISMO', 'descripcion': 'ALGO', 'cantidad': 3}])
        _make_erp(session, [{'codigo_barra': 'EAN_MISMO', 'descripcion': 'ALGO', 'cantidad': 1}], inv)
        diffs = compare_invoice_vs_erp(session, inv.id)
        assert len(diffs) == 1
        assert diffs[0]['diferencia'] == 2

    def test_pack_con_faltante_real_reporta_en_unidades(self, session):
        """Pack declarado PERO además falta mercadería: 2 packs de 10 = 20
        unidades, llegaron 15 → 5 de diferencia, dicha en unidades y explicando
        la conversión (si no, la resta no cierra contra las columnas)."""
        from database import PackEquivalencia
        session.add(PackEquivalencia(ean_pack='EAN_PACK_F', ean_unidad='EAN_UNID_F', cantidad=10))
        session.flush()
        inv = _make_invoice(session, [{'codigo_barra': 'EAN_PACK_F', 'descripcion': 'ALGO PACK X 10', 'cantidad': 2}])
        _make_erp(session, [{'codigo_barra': 'EAN_PACK_F', 'descripcion': 'ALGO', 'cantidad': 15}], inv)
        diffs = compare_invoice_vs_erp(session, inv.id)
        assert len(diffs) == 1
        assert diffs[0]['cantidad_factura'] == 20
        assert diffs[0]['diferencia'] == 5
        assert 'x10' in diffs[0]['observaciones']

    def test_envase_no_entero_no_convierte(self, session):
        """Cociente que no da entero exacto (400 ml contra 150 ml) no es un pack:
        son presentaciones distintas del mismo producto. No se toca."""
        self._obs_producto(session, 91004, 'EAN_400', 400, 'NUTRILON Lata POL x 400')
        self._obs_producto(session, 91005, 'EAN_150', 150, 'NUTRILON Lata POL x 150')
        inv = _make_invoice(session, [{'codigo_barra': 'EAN_400', 'descripcion': 'NUTRILON HA', 'cantidad': 10}])
        _make_erp(session, [{'codigo_barra': 'EAN_150', 'descripcion': 'NUTRILON HA', 'cantidad': 20}], inv)
        diffs = compare_invoice_vs_erp(session, inv.id)
        assert len(diffs) == 1
        assert diffs[0]['diferencia'] == -10


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

class TestNormalizacionDelCruce:
    """El cruce por descripción usa el matcher central (producto_matcher).

    Antes era lower()+espacios: cualquier acento o decimal escrito distinto entre la
    factura y el ERP tiraba el match y el ítem caía a "no encontrado".
    """

    def test_matchea_con_acentos_distintos(self, session):
        inv = _make_invoice(session, [{'codigo_barra': 'F1', 'descripcion': 'AMOXICILINA 500MG CÁPSULAS', 'cantidad': 5}])
        _make_erp(session, [{'codigo_barra': 'E1', 'descripcion': 'AMOXICILINA 500MG CAPSULAS', 'cantidad': 5}], inv)
        assert compare_invoice_vs_erp(session, inv.id) == []

    def test_matchea_decimales_equivalentes(self, session):
        inv = _make_invoice(session, [{'codigo_barra': 'F2', 'descripcion': 'PARACETAMOL 0.5G', 'cantidad': 3}])
        _make_erp(session, [{'codigo_barra': 'E2', 'descripcion': 'PARACETAMOL 0.50G', 'cantidad': 3}], inv)
        assert compare_invoice_vs_erp(session, inv.id) == []

    def test_matchea_vitaminas_espaciadas(self, session):
        inv = _make_invoice(session, [{'codigo_barra': 'F3', 'descripcion': 'XEDENOL B 12', 'cantidad': 2}])
        _make_erp(session, [{'codigo_barra': 'E3', 'descripcion': 'XEDENOL B12', 'cantidad': 2}], inv)
        assert compare_invoice_vs_erp(session, inv.id) == []

    def test_no_matchea_productos_distintos(self, session):
        """La normalización no puede volverse tan laxa que junte productos distintos."""
        inv = _make_invoice(session, [{'codigo_barra': 'F4', 'descripcion': 'IBUPROFENO 400MG', 'cantidad': 1}])
        _make_erp(session, [{'codigo_barra': 'E4', 'descripcion': 'IBUPROFENO 600MG', 'cantidad': 1}], inv)
        diffs = compare_invoice_vs_erp(session, inv.id)
        assert len(diffs) == 1
        assert diffs[0]['cantidad_erp'] == 0

    def test_descripcion_ambigua_no_matchea_a_ninguno(self, session):
        """Dos ítems del ERP que normalizan igual: sin match, va al cruce manual.

        Antes ganaba uno arbitrario (el último del dict) y podía cruzar contra el
        producto equivocado en silencio.
        """
        inv = _make_invoice(session, [{'codigo_barra': 'F5', 'descripcion': 'GASA ESTERIL', 'cantidad': 4}])
        _make_erp(session, [
            {'codigo_barra': 'E5A', 'descripcion': 'GASA ESTERIL', 'cantidad': 4},
            {'codigo_barra': 'E5B', 'descripcion': 'GASA ESTÉRIL', 'cantidad': 9},
        ], inv)
        diffs = compare_invoice_vs_erp(session, inv.id)
        assert len(diffs) == 1
        assert diffs[0]['cantidad_erp'] == 0   # no adivina: cae al cruce manual


class TestSugerirCruceManual:
    """La sugerencia es una pista para el operador, nunca un auto-match."""

    class _Fila:
        def __init__(self, id, descripcion):
            self.id = id
            self.descripcion = descripcion

    def test_sugiere_el_renglon_parecido(self):
        diffs = [self._Fila(1, 'AMOXIDAL 500 COMP X 16'), self._Fila(2, 'IBUPIRAC 600 X 10')]
        erp = [self._Fila(77, 'Amoxidal 500 comprimidos x16')]
        sug = sugerir_cruce_manual(diffs, erp)
        assert sug[77]['nro'] == 1          # el primero de la lista
        assert sug[77]['score'] >= 0.55

    def test_no_sugiere_si_no_se_parece_a_nada(self):
        diffs = [self._Fila(1, 'AMOXIDAL 500 COMP X 16')]
        erp = [self._Fila(88, 'ALCOHOL EN GEL 250ML')]
        assert sugerir_cruce_manual(diffs, erp) == {}

    def test_no_sugiere_ante_empate(self):
        """Dos renglones igual de parecidos: no mandar al operador a jugar a la moneda."""
        diffs = [self._Fila(1, 'GASA ESTERIL'), self._Fila(2, 'GASA ESTERIL')]
        erp = [self._Fila(99, 'GASA ESTERIL')]
        assert sugerir_cruce_manual(diffs, erp) == {}

    def test_sin_datos_no_explota(self):
        assert sugerir_cruce_manual([], []) == {}
        assert sugerir_cruce_manual([self._Fila(1, None)], [self._Fila(2, None)]) == {}

    # ── Falsos positivos: lo que NO se puede sugerir ──────────────────────────
    # En farmacia los números son la presentación. "AMOXIDAL 500 COMP X 16" y
    # "AMOXIDAL 600 COMP X 16" comparten marca, forma y envase: puntúan 64% de
    # parecido y se sugerían. Un click distraído del operador = reclamo a la
    # droguería por el producto equivocado.

    @pytest.mark.parametrize('factura,erp,motivo', [
        ('AMOXIDAL 500 COMP X 16', 'AMOXIDAL 600 COMP X 16', 'distinta dosis'),
        ('AMOXIDAL 500 COMP X 16', 'AMOXIDAL 500 COMP X 30', 'distinto envase'),
        ('DEXALERGIN CREMA 30G',   'DEXALERGIN GOTAS 30ML',  'distinta forma'),
        ('IBUPIRAC 400 X 10',      'IBUPIRAC 400 X 20',      'distinta cantidad'),
    ])
    def test_no_sugiere_presentaciones_distintas(self, factura, erp, motivo):
        sug = sugerir_cruce_manual([self._Fila(1, factura)], [self._Fila(99, erp)])
        assert sug == {}, f'sugirió pese a {motivo}: {factura!r} vs {erp!r}'

    @pytest.mark.parametrize('factura,erp,motivo', [
        ('AMOXIDAL 500 COMP X 16',     'Amoxidal 500 comprimidos x16', 'mismo producto escrito distinto'),
        ('GASA ESTERIL X 10',          'Gasa Estéril x10',             'acento'),
        ('IBUPROFENO 400MG X 10 COMP', 'Ibuprofeno 400 mg x10 comp.',  'unidad pegada al número'),
        ('PARACETAMOL 0.5G X 20',      'Paracetamol 0.50 g x20',       'decimal equivalente'),
    ])
    def test_si_sugiere_el_mismo_producto(self, factura, erp, motivo):
        sug = sugerir_cruce_manual([self._Fila(1, factura)], [self._Fila(99, erp)])
        assert sug.get(99, {}).get('nro') == 1, f'no sugirió pese a ser el mismo ({motivo})'



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


# ── backfill de pack_equivalencias.cantidad ───────────────────────────────────

class TestBackfillPackEquivalenciasCantidad:
    """El Excel dejó las 10 filas de producción con cantidad=1 (medido el
    2026-09-03), así que la tabla no convertía nada. El factor está en
    desc_pack."""

    def _run(self, session, dry_run):
        import database
        from scripts.backfill_pack_equivalencias_cantidad import ejecutar
        orig = getattr(database, 'SessionLocal', None)
        database.SessionLocal = lambda: session
        # `ejecutar` cierra la sesión al terminar; en el test la seguimos usando.
        session.close = lambda: None
        try:
            return ejecutar(dry_run=dry_run)
        finally:
            if orig is not None:
                database.SessionLocal = orig

    def _fila(self, session, ean, desc, cantidad=1):
        from database import PackEquivalencia
        pe = PackEquivalencia(ean_pack=ean, ean_unidad='', cantidad=cantidad, desc_pack=desc)
        session.add(pe)
        session.flush()
        return pe

    def test_completa_cantidad_desde_desc_pack(self, session, monkeypatch):
        monkeypatch.setattr('database.init_engine', lambda *a, **k: None)
        pe = self._fila(session, 'E1', 'OPTAMOX DUO 1G COMP REC X 8 PACK X 10')
        stats = self._run(session, dry_run=False)
        assert stats['actualizadas'] == 1
        assert pe.cantidad == 10

    def test_dry_run_no_escribe(self, session, monkeypatch):
        monkeypatch.setattr('database.init_engine', lambda *a, **k: None)
        pe = self._fila(session, 'E2', 'SERTAL COMP X 200 (PACK X 20 ) VL')
        stats = self._run(session, dry_run=True)
        assert stats['actualizadas'] == 1
        assert pe.cantidad == 1

    def test_no_pisa_lo_cargado_a_mano(self, session, monkeypatch):
        monkeypatch.setattr('database.init_engine', lambda *a, **k: None)
        pe = self._fila(session, 'E3', 'ALGO PACK X 10', cantidad=6)
        stats = self._run(session, dry_run=False)
        assert stats['ya_tenian'] == 1
        assert pe.cantidad == 6

    def test_sin_factor_declarado_no_inventa(self, session, monkeypatch):
        """'X 10 COMP' es la presentación, no un pack. Sin PACK explícito no
        se toca — mismo criterio que el cruce."""
        monkeypatch.setattr('database.init_engine', lambda *a, **k: None)
        pe = self._fila(session, 'E4', 'LOTRIAL 10 MG CPR X 100')
        stats = self._run(session, dry_run=False)
        assert stats['sin_factor'] == 1
        assert pe.cantidad == 1
