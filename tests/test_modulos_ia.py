"""Smoke tests del extractor IA de módulos (services.modulos_ia).

Mockea el cliente Claude → valida shape compatible con pack_detector / wizard.
"""
import json
import tempfile
from unittest.mock import patch

import openpyxl
import pytest

from services import modulos_ia


JSON_OK = json.dumps({
    'modulos': [{
        'nombre': 'MOD. OPTAMOX DUO',
        'items': [
            {'ean': '7793450121123',
             'descripcion': 'OPTAMOX DUO 850 PACK X 10 ENV. x 14 COM',
             'cant': 10, 'desc_pct': 7.0, 'destacado': True},
            {'ean': '7793450988888',
             'descripcion': 'AMOXIDAL 500 x 8',
             'cant': 5, 'desc_pct': 5, 'destacado': False},
        ],
    }],
})


def _stub_llamar(raw):
    def _stub(content, api_key, model):
        return raw, type('U', (), {})()
    return _stub


def test_extraer_pdf_shape_compatible_con_parser():
    """El shape devuelto debe ser igual al de parsers/modulos_xlsx (lista de
    modulos con items)."""
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
        tmp.write(b'%PDF-1.4'); path = tmp.name
    with patch.object(modulos_ia, '_llamar_claude', _stub_llamar(JSON_OK)):
        out = modulos_ia.extraer(path, '.pdf', api_key='test')
    assert isinstance(out, list)
    assert len(out) == 1
    mod = out[0]
    assert mod['nombre'] == 'MOD. OPTAMOX DUO'
    assert len(mod['items']) == 2
    it = mod['items'][0]
    # claves esperadas por pack_detector
    assert {'ean', 'descripcion', 'cant', 'desc_pct', 'destacado'} <= set(it)
    # IA siempre devuelve destacado=False (no ve resaltado del Excel)
    assert it['destacado'] is False
    assert mod['items'][1]['destacado'] is False


def test_xlsx_a_texto_serializa_hojas():
    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
        path = tmp.name
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Mod1'
    ws.append(['EAN', 'Desc', 'Cant', '%'])
    ws.append(['779', 'X PACK X 10', 10, 7])
    wb.save(path)
    txt = modulos_ia._xlsx_a_texto(path)
    assert 'HOJA: Mod1' in txt
    assert 'EAN|Desc|Cant|%' in txt
    assert '779|X PACK X 10|10|7' in txt


def test_xlsx_usa_text_no_vision():
    """XLSX se manda como texto a Claude (Vision no soporta XLSX)."""
    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
        path = tmp.name
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(['EAN', 'Desc']); ws.append(['779', 'X'])
    wb.save(path)

    captured = {}
    def _spy(content, api_key, model):
        captured['content'] = content
        return JSON_OK, type('U', (), {})()
    with patch.object(modulos_ia, '_llamar_claude', _spy):
        modulos_ia.extraer(path, '.xlsx', api_key='test')
    assert len(captured['content']) == 1
    assert captured['content'][0]['type'] == 'text'


def test_falta_api_key_lanza():
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
        tmp.write(b'%PDF'); path = tmp.name
    with patch.dict('os.environ', {}, clear=True):
        with pytest.raises(RuntimeError, match='ANTHROPIC_API_KEY'):
            modulos_ia.extraer(path, '.pdf')


def test_json_invalido_lanza():
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
        tmp.write(b'%PDF'); path = tmp.name
    with patch.object(modulos_ia, '_llamar_claude',
                      _stub_llamar('no es json válido')):
        with pytest.raises(ValueError, match='JSON válido'):
            modulos_ia.extraer(path, '.pdf', api_key='test')
