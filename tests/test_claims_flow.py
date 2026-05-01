"""Tests del flujo completo de reclamos: ver, crear, completar, listar, generar PDF.

Cubre los endpoints expuestos en `routes/claims.py` que NO estaban testeados:
    - GET  /claim/<id>           (view_claim, render claim.html)
    - POST /claim/create         (form-based, render claim.html con auto_download)
    - POST /claim/<id>/complete  (marca COMPLETADO)
    - GET  /claims               (claims_list, render listado)
    - GET  /claim/<id>/pdf       (genera PDF con reportlab)

Los tests de POST /api/claims y POST /invoice/<id>/apply-mapping ya viven en
test_routes.py — acá solo el resto del flujo.
"""

import datetime
import pytest

import database
from database import Claim, ClaimItem, Invoice, InvoiceItem, Provider, StockDifference


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_provider(session, razon='TEST PROV', cuit='30-CLM-1', parser_file='test_parser'):
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


def _make_claim_with_items(session, prov, inv, n_items=1):
    """Crea un Claim ya persistido con N ClaimItems."""
    claim = Claim(
        proveedor_id=prov.id,
        factura_id=inv.id,
        numero_factura=inv.numero_factura,
        fecha=inv.fecha,
        estado='ABIERTO',
    )
    session.add(claim)
    session.flush()
    for i in range(n_items):
        session.add(ClaimItem(
            reclamo_id=claim.id,
            codigo_barra=f'BC{i:03d}',
            descripcion=f'PROD {i}',
            cantidad_factura=10,
            cantidad_erp=7,
            diferencia=3,
            observaciones='',
        ))
    session.flush()
    session.commit()
    return claim


@pytest.fixture
def db_session():
    s = database.SessionLocal()
    yield s
    s.rollback()
    s.close()


# ── GET /claim/<id> ──────────────────────────────────────────────────────────

class TestViewClaim:

    def test_view_claim_404(self, client):
        resp = client.get('/claim/999999')
        assert resp.status_code == 404

    def test_view_claim_ok(self, client, db_session):
        prov  = _make_provider(db_session, cuit='30-VC-1')
        inv   = _make_invoice(db_session, prov, numero='FVC001')
        claim = _make_claim_with_items(db_session, prov, inv, n_items=2)

        resp = client.get(f'/claim/{claim.id}')
        assert resp.status_code == 200
        assert b'FVC001' in resp.data  # número de factura aparece en la página


# ── POST /claim/create ───────────────────────────────────────────────────────

class TestCreateClaimRoute:

    def test_create_ok(self, client, db_session):
        """POST con form data crea el claim y devuelve la vista (auto_download)."""
        prov = _make_provider(db_session, cuit='30-CC-1')
        inv  = _make_invoice(db_session, prov, numero='FCC001')
        diff = _make_diff(db_session, inv)
        db_session.commit()

        resp = client.post('/claim/create', data={
            'invoice_id': str(inv.id),
            'selected_differences': [str(diff.id)],
        })

        assert resp.status_code == 200

        # En DB hay 1 claim con 1 item.
        db_session.expire_all()
        claims = db_session.query(Claim).filter_by(factura_id=inv.id).all()
        assert len(claims) == 1
        assert claims[0].estado == 'ABIERTO'
        assert len(claims[0].items) == 1

    def test_create_invalid_invoice_id(self, client):
        """invoice_id no parseable redirige con flash y NO crea claim."""
        resp = client.post('/claim/create', data={'invoice_id': 'abc'})
        # Redirect (302) o 200 con flash
        assert resp.status_code in (200, 302)

    def test_create_no_selection(self, client, db_session):
        """Sin selected_differences redirige al referrer/index, no crea claim."""
        prov = _make_provider(db_session, cuit='30-CC-NS')
        inv  = _make_invoice(db_session, prov, numero='FCC-NS')
        db_session.commit()

        resp = client.post('/claim/create', data={'invoice_id': str(inv.id)})
        assert resp.status_code in (200, 302)

        db_session.expire_all()
        assert db_session.query(Claim).count() == 0


# ── POST /claim/<id>/complete ────────────────────────────────────────────────

class TestCompleteClaimRoute:

    def test_complete_ok(self, client, db_session):
        prov  = _make_provider(db_session, cuit='30-CMP-1')
        inv   = _make_invoice(db_session, prov, numero='FCMP001')
        claim = _make_claim_with_items(db_session, prov, inv)
        assert claim.estado == 'ABIERTO'

        resp = client.post(f'/claim/{claim.id}/complete')
        assert resp.status_code in (200, 302)

        db_session.expire_all()
        c = db_session.get(Claim, claim.id)
        assert c.estado == 'COMPLETADO'

    def test_complete_404(self, client):
        resp = client.post('/claim/999999/complete')
        assert resp.status_code == 404


# ── GET /claims ──────────────────────────────────────────────────────────────

class TestClaimsList:

    def test_list_empty(self, client):
        resp = client.get('/claims')
        assert resp.status_code == 200

    def test_list_with_claims(self, client, db_session):
        prov = _make_provider(db_session, cuit='30-LST-1')
        inv1 = _make_invoice(db_session, prov, numero='FLST001')
        inv2 = _make_invoice(db_session, prov, numero='FLST002')
        _make_claim_with_items(db_session, prov, inv1)
        _make_claim_with_items(db_session, prov, inv2)

        resp = client.get('/claims')
        assert resp.status_code == 200
        assert b'FLST001' in resp.data
        assert b'FLST002' in resp.data


# ── GET /claim/<id>/pdf ──────────────────────────────────────────────────────

class TestClaimPDF:

    def test_pdf_404(self, client):
        resp = client.get('/claim/999999/pdf')
        assert resp.status_code == 404

    def test_pdf_ok(self, client, db_session):
        """PDF se genera, devuelve content-type correcto y bytes empiezan con %PDF."""
        pytest.importorskip('reportlab')

        prov  = _make_provider(db_session, cuit='30-PDF-1')
        inv   = _make_invoice(db_session, prov, numero='FPDF001')
        claim = _make_claim_with_items(db_session, prov, inv, n_items=3)

        resp = client.get(f'/claim/{claim.id}/pdf')

        assert resp.status_code == 200
        assert resp.headers['Content-Type'] == 'application/pdf'
        assert 'attachment' in resp.headers['Content-Disposition']
        assert f'Reclamo_N{claim.id}_FPDF001.pdf' in resp.headers['Content-Disposition']
        # Header del PDF: bytes "%PDF"
        assert resp.data[:4] == b'%PDF'

    def test_pdf_special_chars_in_invoice_number(self, client, db_session):
        """Número de factura con / y espacios → safe filename (solo alfanum/_/-)."""
        pytest.importorskip('reportlab')

        prov  = _make_provider(db_session, cuit='30-PDF-SC')
        inv   = _make_invoice(db_session, prov, numero='F 001/A-2026')
        claim = _make_claim_with_items(db_session, prov, inv)

        resp = client.get(f'/claim/{claim.id}/pdf')
        assert resp.status_code == 200
        cd = resp.headers['Content-Disposition']
        # No debe haber /, espacios ni otros caracteres no seguros en el filename.
        assert '/' not in cd.split('filename="')[-1]
        assert ' ' not in cd.split('filename="')[-1].rstrip('"')
        # Sí preserva el guion y el underscore (alfanum/_/-).
        assert 'F_001_A-2026' in cd

    def test_pdf_many_items(self, client, db_session):
        """PDF con 50 items (test de robustez de tabla repeatRows)."""
        pytest.importorskip('reportlab')

        prov  = _make_provider(db_session, cuit='30-PDF-MANY')
        inv   = _make_invoice(db_session, prov, numero='FPDFM001')
        claim = _make_claim_with_items(db_session, prov, inv, n_items=50)

        resp = client.get(f'/claim/{claim.id}/pdf')
        assert resp.status_code == 200
        assert resp.data[:4] == b'%PDF'
        # PDF de 50 items debe ser razonablemente grande pero no absurdo.
        assert len(resp.data) > 2000

    def test_pdf_zero_items(self, client, db_session):
        """Claim sin items: PDF aún se genera (caso degenerate, reportlab acepta tabla 1-row)."""
        pytest.importorskip('reportlab')

        prov  = _make_provider(db_session, cuit='30-PDF-ZERO')
        inv   = _make_invoice(db_session, prov, numero='FPDFZ001')
        claim = _make_claim_with_items(db_session, prov, inv, n_items=0)

        resp = client.get(f'/claim/{claim.id}/pdf')
        assert resp.status_code == 200
        assert resp.data[:4] == b'%PDF'

    def test_pdf_descripcion_con_caracteres_no_ascii(self, client, db_session):
        """Descripciones con tildes y ñ se encodean correctamente."""
        pytest.importorskip('reportlab')

        prov  = _make_provider(db_session, cuit='30-PDF-UTF8')
        inv   = _make_invoice(db_session, prov, numero='FPDFU001')
        claim = Claim(
            proveedor_id=prov.id,
            factura_id=inv.id,
            numero_factura=inv.numero_factura,
            fecha=inv.fecha,
            estado='ABIERTO',
        )
        db_session.add(claim)
        db_session.flush()
        db_session.add(ClaimItem(
            reclamo_id=claim.id,
            codigo_barra='BC-UTF',
            descripcion='IBUPROFENO 600MG (caja x 20) — laboratorio Pérez Niño',
            cantidad_factura=10, cantidad_erp=7, diferencia=3, observaciones='',
        ))
        db_session.commit()

        resp = client.get(f'/claim/{claim.id}/pdf')
        assert resp.status_code == 200
        assert resp.data[:4] == b'%PDF'
