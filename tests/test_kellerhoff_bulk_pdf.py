"""Parser del export masivo Kellerhoff + backfill sobre facturas existentes."""
import database
from database import Invoice, InvoiceItem
from services.kellerhoff_bulk_pdf import (
    backfill,
    iter_comprobantes_texto,
    parsear_items_comprobante,
)

# Factura con las DOS secciones (medicamentos 4-num + gravados 2-num), texto real.
_FAC = """DROGUERIA KELLERHOFF S.A. FACTURA N: 0046-00069104
FECHA: 01/07/2026 10:43
COND. DE PAGO: 180 dias FF
Codigo Barra Cant. Descripcion Precio Publico % Dto. Precio Unitario Importe
7798129415043 4 ACEMUK 600 MG TABL EFERV X 10 WEB 16.754,87 33,41 11.157,07 44.628,27
7795345011097 2 AMOXIDAL DUO RESP CPR X 14 WEB 17.654,00 33,41 11.755,80 23.511,60
TRF 0032915501Y
*** PRODUCTOS GRAVADOS ***
7790064002104 6 ESTRELLA ALGODON BABY X 40 WEB 1.773,78 10.642,68
Hoja Cant Un Monto Exento Monto Gravado IVA Inscrip.
1/1 12 ...
"""

_NC_FIN = """DROGUERIA KELLERHOFF S.A. NOTA CREDITO N: 0046-00017300
FECHA: 02/07/2026
1 RECUPERO NC PRESERFAR S26/2026 48.016,83 48.016,83
"""


def test_parsea_las_dos_secciones_con_dto():
    items = parsear_items_comprobante(_FAC)
    # 2 medicamentos (con dto) + 1 gravado (sin dto)
    assert len(items) == 3
    med = items[0]
    assert med['barcode'] == '7798129415043'
    assert med['dto_pct'] == 33.41
    assert med['precio_unitario'] == 11157.07
    assert med['importe'] == 44628.27
    grav = items[-1]
    assert grav['barcode'] == '7790064002104'
    assert grav['dto_pct'] == 0.0          # gravado: sin descuento
    assert grav['importe'] == 10642.68


def test_reconoce_factura_trf_y_recupero():
    cs = list(iter_comprobantes_texto(_FAC + _NC_FIN))
    assert len(cs) == 2
    fac = cs[0]
    assert fac['tipo'] == 'FAC'
    assert fac['numero'] == '00046-00069104'
    assert fac['trf'] == '0032915501Y'
    assert fac['condicion_pago'] == '180 dias FF'
    nc = cs[1]
    assert nc['tipo'] == 'NCR'
    assert nc['nc_financiera'] is True
    assert 'PRESERFAR' in nc['concepto']
    assert nc['items'] == []


def _session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    eng = create_engine('sqlite:///:memory:')
    database.Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def test_backfill_completa_items_y_trf():
    s = _session()
    # factura Kellerhoff existente SIN ítems (como quedaron tras el wipe)
    inv = Invoice(numero_factura='00046-00069104', fecha=__import__('datetime').date(2026, 7, 1),
                  proveedor_cuit='30539756490', tipo_comprobante='FAC', total=100)
    s.add(inv)
    s.commit()
    comps = list(iter_comprobantes_texto(_FAC + _NC_FIN))
    st = backfill(s, comps, '30539756490')
    assert st['match'] == 1
    assert st['facturas_backfill'] == 1
    assert st['items_creados'] == 3
    assert st['trf_set'] == 1
    assert st['saltados_fin'] == 1          # la RECUPERO se saltea
    inv2 = s.query(Invoice).filter_by(numero_factura='00046-00069104').first()
    assert len(inv2.items) == 3
    assert inv2.trf == '0032915501Y'
    assert inv2.total_articulos == 3
    # el dto llegó al ítem
    med = [it for it in inv2.items if it.codigo_barra == '7798129415043'][0]
    assert float(med.dto) == 33.41


def test_backfill_no_pisa_facturas_con_items():
    s = _session()
    inv = Invoice(numero_factura='00046-00069104', fecha=__import__('datetime').date(2026, 7, 1),
                  proveedor_cuit='30539756490', tipo_comprobante='FAC', total=100)
    s.add(inv)
    s.flush()
    s.add(InvoiceItem(factura_id=inv.id, codigo_barra='YA', cantidad=1, importe=1))
    s.commit()
    comps = list(iter_comprobantes_texto(_FAC))
    st = backfill(s, comps, '30539756490')
    assert st['ya_tenian_items'] == 1
    assert st['facturas_backfill'] == 0     # no tocó la que ya tenía
    inv2 = s.query(Invoice).filter_by(numero_factura='00046-00069104').first()
    assert len(inv2.items) == 1


def test_backfill_sin_match_no_crea():
    s = _session()
    comps = list(iter_comprobantes_texto(_FAC))
    st = backfill(s, comps, '30539756490')
    assert st['sin_match'] == 1
    assert s.query(Invoice).count() == 0    # no crea facturas nuevas
