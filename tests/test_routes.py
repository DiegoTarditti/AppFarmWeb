"""Tests de integración para las rutas críticas de Flask."""

import datetime
import io
import pytest
import database
from database import (
    Provider, Invoice, InvoiceItem, ErpStock, StockDifference, Claim, BarcodeMapping,
)


@pytest.fixture
def db_session():
    s = database.SessionLocal()
    yield s
    s.rollback()
    s.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_provider(session, razon='TEST PROV', cuit='30-TEST-1', parser_file='test_parser'):
    prov = Provider(razon_social=razon, cuit=cuit, parser_file=parser_file)
    session.add(prov)
    session.flush()
    return prov


def _make_invoice(session, prov, numero='F001'):
    inv = Invoice(
        numero_factura=numero,
        fecha=datetime.date.today(),
        proveedor_razon=prov.razon_social,
        proveedor_cuit=prov.cuit,
        tipo_comprobante='FAC',
        total=500.0,
    )
    session.add(inv)
    session.flush()
    return inv


def _make_diff(session, inv, cb='BC001', desc='PROD A', cant_fac=10, cant_erp=7):
    diff = StockDifference(
        factura_id=inv.id,
        codigo_barra=cb,
        descripcion=desc,
        cantidad_factura=cant_fac,
        cantidad_erp=cant_erp,
        diferencia=cant_fac - cant_erp,
        observaciones='',
    )
    session.add(diff)
    session.flush()
    return diff


# ── /api/claims ───────────────────────────────────────────────────────────────

class TestApiClaims:

    def test_create_claim_ok(self, client, db_session):
        prov = _make_provider(db_session, cuit='30-CLM-1')
        inv  = _make_invoice(db_session, prov, numero='FC001')
        diff = _make_diff(db_session, inv)
        db_session.commit()

        resp = client.post('/api/claims', json={
            'factura_id': inv.id,
            'difference_ids': [diff.id],
        })

        assert resp.status_code == 201
        data = resp.get_json()
        assert 'claim_id' in data
        assert data['estado'] == 'ABIERTO'

        claim = db_session.get(Claim, data['claim_id'])
        assert claim is not None
        assert claim.factura_id == inv.id
        assert len(claim.items) == 1

    def test_create_claim_missing_params(self, client):
        resp = client.post('/api/claims', json={})
        assert resp.status_code == 400

    def test_create_claim_invalid_invoice(self, client):
        resp = client.post('/api/claims', json={
            'factura_id': 999999,
            'difference_ids': [1],
        })
        assert resp.status_code == 400


# ── /invoice/<id>/apply-mapping ───────────────────────────────────────────────

class TestApplyMapping:

    def test_diff_deleted_when_mapping_closes_gap(self, client, db_session):
        """Cuando diferencia llega a 0 tras el cruce, la diferencia se elimina."""
        prov = _make_provider(db_session, razon='MAP LAB', cuit='30-MAP-1')
        inv  = _make_invoice(db_session, prov, numero='FM001')
        session_item = InvoiceItem(
            factura_id=inv.id, codigo_barra='FAC_X', descripcion='PROD X', cantidad=5,
        )
        db_session.add(session_item)
        db_session.flush()

        erp = ErpStock(codigo_barra='ERP_X', descripcion='PROD X ERP', cantidad=5)
        db_session.add(erp)
        db_session.flush()

        diff = _make_diff(db_session, inv, cb='FAC_X', desc='PROD X', cant_fac=5, cant_erp=0)
        db_session.commit()

        resp = client.post(f'/invoice/{inv.id}/apply-mapping', data={
            f'mapping_{erp.id}': '1',
        })

        assert resp.status_code in (200, 302)

        db_session.expire_all()
        remaining = db_session.query(StockDifference).filter_by(factura_id=inv.id).all()
        assert remaining == []

        mapping = db_session.query(BarcodeMapping).filter_by(proveedor_id=prov.id).first()
        assert mapping is not None
        assert mapping.codigo_barra_factura == 'FAC_X'
        assert mapping.codigo_barra_erp == 'ERP_X'

    def test_diff_updated_when_mapping_partial(self, client, db_session):
        """Cuando diferencia != 0 tras el cruce, la diferencia se actualiza (no elimina)."""
        prov = _make_provider(db_session, razon='MAP LAB 2', cuit='30-MAP-2')
        inv  = _make_invoice(db_session, prov, numero='FM002')
        db_session.add(InvoiceItem(
            factura_id=inv.id, codigo_barra='FAC_Y', descripcion='PROD Y', cantidad=10,
        ))
        db_session.flush()

        erp = ErpStock(codigo_barra='ERP_Y', descripcion='PROD Y ERP', cantidad=7)
        db_session.add(erp)
        db_session.flush()

        diff = _make_diff(db_session, inv, cb='FAC_Y', desc='PROD Y', cant_fac=10, cant_erp=0)
        db_session.commit()

        resp = client.post(f'/invoice/{inv.id}/apply-mapping', data={
            f'mapping_{erp.id}': '1',
        })

        assert resp.status_code in (200, 302)

        db_session.expire_all()
        remaining = db_session.query(StockDifference).filter_by(factura_id=inv.id).all()
        assert len(remaining) == 1
        assert remaining[0].cantidad_erp == 7
        assert remaining[0].diferencia == 3


# ── /api/upload ───────────────────────────────────────────────────────────────

class TestApiUpload:

    def test_upload_ok(self, client, db_session, monkeypatch):
        """Upload con parsers mockeados devuelve invoice + diferencias."""
        import openpyxl

        prov = _make_provider(db_session, razon='PROV UPLOAD', cuit='30-UP-1',
                              parser_file='test_parser')
        db_session.commit()

        monkeypatch.setattr('routes.invoices.parse_invoice_pdf', lambda path, parser: {
            'numero_factura': 'FUP001',
            'fecha': datetime.date.today(),
            'proveedor_razon': 'PROV UPLOAD',
            'proveedor_cuit': '30-UP-1',
            'total': 500.0,
            'items': [
                {'codigo_barra': 'BCUP1', 'descripcion': 'PROD UP', 'cantidad': 5,
                 'precio_unitario': 100.0, 'importe': 500.0,
                 'dto': None, 'lote': None, 'vencimiento': None},
            ],
        })
        monkeypatch.setattr('routes.invoices.parse_erp_excel', lambda path: [
            {'codigo_barra': 'BCUP1', 'descripcion': 'PROD UP', 'cantidad': 3,
             'precio_unitario': 100.0},
        ])

        wb = openpyxl.Workbook()
        wb.active.append(['codigo_barra', 'cantidad'])
        wb.active.append(['BCUP1', 3])
        erp_buf = io.BytesIO()
        wb.save(erp_buf)
        erp_buf.seek(0)

        resp = client.post('/api/upload', data={
            'proveedor_id': str(prov.id),
            'tipo_comprobante': 'FAC',
            'invoice_pdf': (io.BytesIO(b'%PDF-1.4 fake'), 'invoice.pdf'),
            'erp_excel': (erp_buf, 'erp.xlsx'),
        }, content_type='multipart/form-data')

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['invoice']['numero_factura'] == 'FUP001'
        assert len(data['differences']) == 1
        assert data['differences'][0]['diferencia'] == 2   # 5 − 3

    def test_upload_missing_erp(self, client, db_session):
        """Sin archivo ERP devuelve 400."""
        prov = _make_provider(db_session, razon='PROV NO ERP', cuit='30-NOERP-1',
                              parser_file='x')
        db_session.commit()

        resp = client.post('/api/upload', data={
            'proveedor_id': str(prov.id),
            'invoice_pdf': (io.BytesIO(b'%PDF'), 'test.pdf'),
        }, content_type='multipart/form-data')

        assert resp.status_code == 400

    def test_upload_unknown_provider(self, client):
        """Con proveedor inexistente devuelve 400."""
        resp = client.post('/api/upload', data={
            'proveedor_id': '999999',
            'invoice_pdf': (io.BytesIO(b'%PDF'), 'test.pdf'),
            'erp_excel':   (io.BytesIO(b'fake'), 'erp.xlsx'),
        }, content_type='multipart/form-data')

        assert resp.status_code == 400


# ── /invoice/<id>/compare — ERP de otro chequeo ───────────────────────────────

class TestCompareViewErpDeOtroChequeo:
    """erp_stock es global: el cruce nunca puede mostrar el ingreso de otra factura.

    El smoke test de /invoice/1/compare no cubre esto porque redirige (no existe la
    factura) y nunca llega a renderizar el template.
    """

    def test_avisa_y_no_lista_el_erp_ajeno(self, client, db_session):
        prov = _make_provider(db_session, razon='ERP AJENO', cuit='30-EAJ-1')
        inv = _make_invoice(db_session, prov, numero='FEA01')
        db_session.add(InvoiceItem(factura_id=inv.id, codigo_barra='FAC_Z',
                                   descripcion='PROD Z', cantidad=5))
        # Ingreso que quedó de OTRO chequeo: no es de esta factura (inv.erp_carga_id NULL).
        db_session.add(ErpStock(codigo_barra='OTRO_CHEQUEO', descripcion='PROD DE OTRO',
                                cantidad=9, carga_id=111))
        db_session.commit()

        resp = client.get(f'/invoice/{inv.id}/compare')
        assert resp.status_code == 200
        body = resp.data.decode('utf-8')
        assert 'no está cruzada contra un ingreso de ERP' in body
        # El bug: esto se listaba como si fuera el ingreso de esta factura.
        assert 'PROD DE OTRO' not in body

    def test_sin_aviso_cuando_el_erp_es_de_la_factura(self, client, db_session):
        prov = _make_provider(db_session, razon='ERP PROPIO', cuit='30-EPR-1')
        inv = _make_invoice(db_session, prov, numero='FEP01')
        db_session.add(InvoiceItem(factura_id=inv.id, codigo_barra='FAC_W',
                                   descripcion='PROD W', cantidad=5))
        db_session.add(ErpStock(codigo_barra='SOLO_ERP', descripcion='PROD SOLO ERP',
                                cantidad=2, carga_id=222))
        inv.erp_carga_id = 222
        db_session.commit()

        resp = client.get(f'/invoice/{inv.id}/compare')
        assert resp.status_code == 200
        body = resp.data.decode('utf-8')
        assert 'no está cruzada contra un ingreso de ERP' not in body
        assert 'PROD SOLO ERP' in body


class TestCompareViewSugerencias:
    """El cruce manual sugiere el renglón parecido, sin aplicarlo solo."""

    def test_muestra_el_chip_de_sugerencia(self, client, db_session):
        prov = _make_provider(db_session, razon='SUG LAB', cuit='30-SUG-1')
        inv = _make_invoice(db_session, prov, numero='FSG01')
        db_session.add(InvoiceItem(factura_id=inv.id, codigo_barra='FAC_SG',
                                   descripcion='AMOXIDAL 500 COMP X 16', cantidad=5))
        # Item del ERP sin match exacto pero parecido -> debe sugerirse.
        db_session.add(ErpStock(codigo_barra='ERP_SG', descripcion='Amoxidal 500 comprimidos x16',
                                cantidad=5, carga_id=333))
        inv.erp_carga_id = 333
        _make_diff(db_session, inv, cb='FAC_SG', desc='AMOXIDAL 500 COMP X 16',
                   cant_fac=5, cant_erp=0)
        db_session.commit()

        resp = client.get(f'/invoice/{inv.id}/compare')
        assert resp.status_code == 200
        body = resp.data.decode('utf-8')
        assert 'usarSugerencia(' in body, 'no se renderizó el chip de sugerencia'
        # La sugerencia NO se aplica sola: el input queda vacío.
        assert 'name="mapping_' in body
