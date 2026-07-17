"""Tests de los parsers de factura escritos a mano (los que usa /ingresos).

No había ninguno. Los PDFs se generan con reportlab (ya es dependencia) en vez de
versionar facturas reales: son datos de proveedores y no van al repo.

Lo que se protege acá es que los parsers usen el MISMO pipeline de extracción que
los que genera /converter (`_normalize_quadrupled(extract_text_with_ocr_fallback(...))`):
sin eso, un PDF escaneado devuelve 0 ítems y la factura no se puede importar por
ningún lado, y los artefactos de pdfplumber se comen los campos.
"""
import importlib
import inspect

import pytest

pytest.importorskip('reportlab')
pytest.importorskip('pdfplumber')

from reportlab.lib.pagesizes import A4          # noqa: E402
from reportlab.pdfgen import canvas             # noqa: E402


def _pdf(tmp_path, nombre, lineas):
    p = tmp_path / nombre
    c = canvas.Canvas(str(p), pagesize=A4)
    c.setFont('Helvetica', 9)
    y = 800
    for ln in lineas:
        if ln:
            c.drawString(40, y, ln)
        y -= 14
    c.save()
    return str(p)


def _cuadruplicar(s):
    """Simula el artefacto de pdfplumber en fuentes bold: cada carácter x4."""
    return ''.join(ch * 4 for ch in s)


def _parser(nombre):
    return importlib.import_module(f'parsers.{nombre}')


PARSERS_PDF = ['pharmos', '20_de_junio', '_template']


# ── Pipeline de extracción ────────────────────────────────────────────────────

@pytest.mark.parametrize('nombre', PARSERS_PDF)
def test_usa_el_pipeline_de_extraccion_comun(nombre):
    """OCR fallback + normalización, igual que los parsers generados por /converter.

    Si este test falla, el parser volvió a leer con pdfplumber directo: las facturas
    escaneadas de ese proveedor dejan de poder importarse (0 ítems, sin aviso útil).
    """
    src = inspect.getsource(_parser(nombre))
    assert 'extract_text_with_ocr_fallback' in src, f'{nombre}: sin OCR fallback'
    assert '_normalize_quadrupled' in src, f'{nombre}: sin normalización de artefactos'
    assert 'pdfplumber.open' not in src, f'{nombre}: sigue leyendo el PDF a mano'


# ── 20 de Junio ───────────────────────────────────────────────────────────────

FAC_20J = [
    'DROGUERIA 20 DE JUNIO S.A.',
    'Res.Insc.01 23-17460511-4',
    'FACTURA 0011-19376868 / 01',
    'FECHA 15/07/2026',
    '',
    '2 IBUPIRAC 600 COMP X 10 PFI 7791234567890 1234,56 10,00 1111,10 2.222,20',
    '5 AMOXIDAL 500 COMP X 16 ROE 7790987654321 800,00 5,00 760,00 3.800,00',
    '1 TAFIROL 500 X 20 GEN 7791111111111 500,00 0,00 500,00 500,00',
    '',
    # El pie sale cuadruplicado del PDF real (fuente bold).
    _cuadruplicar('TOTAL NETO') + ' ' + _cuadruplicar('$') + ' ' + _cuadruplicar('6.522,20'),
]


class Test20DeJunio:
    def test_encabezado(self, tmp_path):
        r = _parser('20_de_junio').parse_invoice_pdf(_pdf(tmp_path, '20j.pdf', FAC_20J))
        assert r['numero_factura'] == '0011-19376868'
        assert r['proveedor_cuit'] == '23-17460511-4'
        assert r['fecha'].isoformat() == '2026-07-15'

    def test_total_con_la_linea_cuadruplicada(self, tmp_path):
        """El pie viene con cada carácter x4; el total tiene que salir igual.

        Antes se rearmaba a mano tomando cada 4º carácter, y sólo si el largo era
        múltiplo de 4 — si no, total = 0 en silencio.
        """
        r = _parser('20_de_junio').parse_invoice_pdf(_pdf(tmp_path, '20j.pdf', FAC_20J))
        assert r['total'] == 6522.20

    def test_items(self, tmp_path):
        r = _parser('20_de_junio').parse_invoice_pdf(_pdf(tmp_path, '20j.pdf', FAC_20J))
        assert len(r['items']) == 3
        it = r['items'][0]
        assert it['codigo_barra'] == '7791234567890'
        assert it['cantidad'] == 2
        assert it['precio_unitario'] == 1111.10
        assert it['importe'] == 2222.20
        # El laboratorio (3-4 mayúsculas) no es parte de la descripción.
        assert it['descripcion'] == 'IBUPIRAC 600 COMP X 10'


# ── Pharmos ───────────────────────────────────────────────────────────────────

FAC_PHARMOS = [
    'PHARMOS S.A.',
    'C.U.I.T.: 30-64266156-2',
    'FECHA: 24/02/2026 0142-00964164',
    '',
    '79-65 IBUPIRAC 600 COMP X 10 21,00 2 1.234,5600 2.469,12',
    '80-272 AMOXIDAL 500 COMP X 16 5 800,0000 4.000,00',
]


class TestPharmos:
    def test_encabezado(self, tmp_path):
        r = _parser('pharmos').parse_invoice_pdf(_pdf(tmp_path, 'pha.pdf', FAC_PHARMOS))
        assert r['numero_factura'] == '0142-00964164'
        assert r['proveedor_cuit'] == '30-64266156-2'

    def test_items_usan_el_codigo_interno_como_barcode(self, tmp_path):
        """Pharmos no trae EAN: el código interno ('79-65') va de codigo_barra."""
        r = _parser('pharmos').parse_invoice_pdf(_pdf(tmp_path, 'pha.pdf', FAC_PHARMOS))
        assert len(r['items']) == 2
        assert r['items'][0]['codigo_barra'] == '79-65'
        assert r['items'][0]['cantidad'] == 2
        assert r['items'][0]['precio_unitario'] == 1234.56
