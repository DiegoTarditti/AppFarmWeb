"""Unit tests de las funciones puras del scraper Kellerhoff.

No tocan el portal ni Playwright: fijan los dos arreglos de esta sesión que se
pueden testear sin navegador — el parseo de números en formato argentino (tabla
de detalle) y la captura de los args del onclick que abre el detalle.
"""
import pytest

from services.kellerhoff_scraper import (
    _RE_ONCLICK_DETALLE,
    _parse_dec,
    _parse_dec_ar,
)


# ── Números: la tabla de detalle es formato AR; el listado es US ─────────────

@pytest.mark.parametrize('s, esperado', [
    ('22.814,37', 22814.37),
    ('14.948,32', 14948.32),
    ('471.794,30', 471794.30),
    ('8.999,00', 8999.0),
    ('1.234.567,89', 1234567.89),
    ('50,00', 50.0),
    ('5', 5.0),
    ('$ 22.814,37', 22814.37),
    ('', 0.0),
])
def test_parse_dec_ar(s, esperado):
    assert _parse_dec_ar(s) == esperado


def test_ar_y_us_no_se_confunden():
    # El mismo string se interpreta distinto según la fuente. La tabla de
    # detalle ('22.814,37') es AR; el listado ('230,261.36') es US.
    assert _parse_dec_ar('22.814,37') == 22814.37
    assert _parse_dec('230,261.36') == 230261.36
    # Un número AR pasado por el parser US da cualquier cosa (por eso hubo bug):
    assert _parse_dec_ar('230.261,36') == 230261.36


# ── onclick del link de comprobante → args de navegación ────────────────────

def test_onclick_captura_los_cuatro_args():
    oc = "verComprobanteEnPestaña('DG', '0046A00061895', '2026-08-19', '1000002873');"
    m = _RE_ONCLICK_DETALLE.search(oc)
    assert m is not None
    assert m.groups() == ('DG', '0046A00061895', '2026-08-19', '1000002873')


def test_onclick_tolera_espacios_y_tipo_dr():
    oc = "verComprobanteEnPestaña('DR','0046A00255782','2026-08-18','1000002873')"
    m = _RE_ONCLICK_DETALLE.search(oc)
    assert m.groups() == ('DR', '0046A00255782', '2026-08-18', '1000002873')


def test_onclick_sin_match_devuelve_none():
    assert _RE_ONCLICK_DETALLE.search('') is None
    assert _RE_ONCLICK_DETALLE.search("otraFuncion('x')") is None
