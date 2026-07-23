"""Smoke tests del extractor IA de catálogos de ofertas (services.ofertas_ia).

Mockea el cliente Claude → valida que el shape devuelto sea compatible con el
wizard de ofertas (headers / rows / mapping / count_filas), que vigencia global
caiga al item cuando el item no la trae, y que XLSX se serialice a texto.
"""
import json
import tempfile
from unittest.mock import patch

import openpyxl
import pytest

from services import ofertas_ia


# ── Fixtures ────────────────────────────────────────────────────────────────

JSON_OK = json.dumps({
    'laboratorio': 'Roemmers',
    'vigencia_hasta': '2026-12-31',
    'items': [
        {'ean': '7791234567890', 'codigo': 'R-001', 'descripcion': 'AMOXIDAL 875 x14',
         'precio': 12345.67, 'unidades_minima': 6, 'descuento_psl': 32.5,
         'rentabilidad': 18.0, 'plazo_pago': 30, 'grupo_id': 'mod1',
         'vigencia_hasta': None},
        {'ean': None, 'codigo': '999X', 'descripcion': 'IBUPIREX 600 x30',
         'precio': 2000, 'unidades_minima': 12, 'descuento_psl': None,
         'rentabilidad': None, 'plazo_pago': None, 'grupo_id': None,
         'vigencia_hasta': '2026-06-30'},
    ],
})


def _stub_llamar_claude(raw):
    """Devuelve un stub para _llamar_claude que ignora el content y retorna `raw`."""
    def _stub(content, api_key, model):
        return raw, type('U', (), {'input_tokens': 100, 'output_tokens': 200})()
    return _stub


# ── Tests ───────────────────────────────────────────────────────────────────

def test_extraer_pdf_shape_compatible():
    """El shape devuelto debe matchear lo que espera el wizard."""
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
        tmp.write(b'%PDF-1.4 dummy')
        path = tmp.name
    with patch.object(ofertas_ia, '_llamar_claude', _stub_llamar_claude(JSON_OK)):
        out = ofertas_ia.extraer(path, '.pdf', api_key='test')
    assert set(out) >= {'headers', 'rows', 'mapping', 'header_row',
                        'count_filas', 'fuente'}
    assert out['fuente'] == 'ia'
    assert out['headers'] == ofertas_ia.COLUMNAS_STANDARD
    assert out['count_filas'] == 2 == len(out['rows'])
    # Cada fila alineada con headers
    assert all(len(r) == len(out['headers']) for r in out['rows'])
    # Mapping es identidad (las columnas ya vienen normalizadas)
    assert out['mapping'] == {c: c for c in ofertas_ia.COLUMNAS_STANDARD}


def test_vigencia_global_cae_al_item():
    """Si el item no trae vigencia, se completa con la global del catálogo."""
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
        tmp.write(b'%PDF-1.4 dummy'); path = tmp.name
    with patch.object(ofertas_ia, '_llamar_claude', _stub_llamar_claude(JSON_OK)):
        out = ofertas_ia.extraer(path, '.pdf', api_key='test')
    headers = out['headers']
    idx_vig = headers.index('vigencia_hasta')
    # primer item no tenía vigencia propia → toma la global
    assert out['rows'][0][idx_vig] == '2026-12-31'
    # segundo item sí trae la suya → la suya gana
    assert out['rows'][1][idx_vig] == '2026-06-30'


def test_xlsx_a_texto_serializa_filas():
    """Pequeño chequeo de que la serialización CSV-like funciona."""
    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
        path = tmp.name
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Ofertas'
    ws.append(['EAN', 'Desc', 'Precio'])
    ws.append(['779', 'X', 100])
    ws.append([None, None, None])      # fila vacía: se saltea
    ws.append(['780', 'Y', 200])
    wb.save(path)

    texto = ofertas_ia._xlsx_a_texto(path)
    assert 'HOJA: Ofertas' in texto
    assert 'EAN|Desc|Precio' in texto
    assert '779|X|100' in texto
    assert '780|Y|200' in texto


def test_extraer_xlsx_usa_texto_no_vision():
    """Para XLSX se manda texto a Claude (Vision no soporta XLSX)."""
    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
        path = tmp.name
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(['EAN', 'Desc', 'Precio']); ws.append(['779', 'X', 100])
    wb.save(path)

    captured = {}
    def _spy(content, api_key, model):
        captured['content'] = content
        return JSON_OK, type('U', (), {})()
    with patch.object(ofertas_ia, '_llamar_claude', _spy):
        out = ofertas_ia.extraer(path, '.xlsx', api_key='test')
    # Una sola parte, type='text' (no document, no image)
    assert len(captured['content']) == 1
    assert captured['content'][0]['type'] == 'text'
    assert 'EAN|Desc|Precio' in captured['content'][0]['text']
    assert out['count_filas'] == 2


def test_falta_api_key_lanza():
    """Sin api_key (env tampoco) → RuntimeError."""
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
        tmp.write(b'%PDF'); path = tmp.name
    with patch.dict('os.environ', {}, clear=True):
        with pytest.raises(RuntimeError, match='ANTHROPIC_API_KEY'):
            ofertas_ia.extraer(path, '.pdf')


def test_parse_json_tolera_fences():
    """_parse_json acepta JSON envuelto en ```json ... ``` o con texto alrededor."""
    raw = '```json\n{"items": [{"descripcion": "X"}]}\n```'
    assert ofertas_ia._parse_json(raw)['items'][0]['descripcion'] == 'X'
    raw2 = 'algo antes {"items": []} algo después'
    assert ofertas_ia._parse_json(raw2)['items'] == []
    assert ofertas_ia._parse_json('basura sin json') is None
