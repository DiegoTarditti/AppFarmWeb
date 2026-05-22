"""Tests del source-of-truth de presentación de flags (services/flags.py)."""
import json

import pytest

import database
from database import ProductoFlag, TipoPedidoConfig
from services.flags import (
    FLAG_COLOR_CLASES,
    construir_flag_dict,
    flags_display_por_producto,
)


@pytest.fixture
def session_demo():
    s = database.SessionLocal()
    try:
        s.add(TipoPedidoConfig(
            slug='DISCONTINUADO', nombre='Discontinuado', categoria='flag',
            descripcion='', activo=True,
            config_json=json.dumps({'icono': '🚫', 'color': 'red',
                                    'efecto_armado': 'badge_cero'})))
        s.add(TipoPedidoConfig(
            slug='NOTA', nombre='Nota informativa', categoria='flag',
            descripcion='', activo=True,
            config_json=json.dumps({'icono': '📝', 'color': 'sky'})))
        s.commit()
        yield s
    finally:
        s.close()


def test_construir_flag_dict_con_cfg(session_demo):
    s = session_demo
    cfg = s.query(TipoPedidoConfig).filter_by(slug='DISCONTINUADO').first()
    flag = ProductoFlag(flag_slug='DISCONTINUADO', ean='779', nota='fuera de linea',
                        ean_reemplazo='780')
    d = construir_flag_dict(flag, cfg)
    assert d['slug'] == 'DISCONTINUADO'
    assert d['nombre'] == 'Discontinuado'
    assert d['icono'] == '🚫'
    assert d['color_clases'] == FLAG_COLOR_CLASES['red']
    assert d['nota'] == 'fuera de linea'
    assert d['ean_reemplazo'] == '780'


def test_construir_flag_dict_sin_cfg_fallback(session_demo):
    """Sin TipoPedidoConfig: usa el slug como nombre, icono/color default."""
    flag = ProductoFlag(flag_slug='RARO', ean='1')
    d = construir_flag_dict(flag, None)
    assert d['nombre'] == 'RARO'
    assert d['icono'] == '🚩'
    assert d['color_clases'] == FLAG_COLOR_CLASES['sky']


def test_flags_display_por_producto_primer_ean(session_demo):
    """El flag se resuelve por CUALQUIER EAN del producto (principal o alt)."""
    s = session_demo
    s.add(ProductoFlag(flag_slug='NOTA', ean='ALT2', nota='ojo'))
    s.commit()
    # producto A: flag en su segundo EAN. producto B: sin flag.
    eans = {'A': ['PRINC_A', 'ALT2'], 'B': ['PRINC_B']}
    out = flags_display_por_producto(s, eans)
    assert out['A'] is not None
    assert out['A']['slug'] == 'NOTA'
    assert out['B'] is None


def test_flags_display_sin_eans(session_demo):
    out = flags_display_por_producto(session_demo, {'A': [], 'B': None})
    assert out == {'A': None, 'B': None}


def test_flags_display_vacio(session_demo):
    assert flags_display_por_producto(session_demo, {}) == {}
