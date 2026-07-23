"""Guard de farmacia en el import de "Mis Comprobantes" de ARCA.

Existe porque el 2026-07-21 se importaron 9.724 comprobantes de Pieristei en la
base de Badía. `facturas` no guarda a qué farmacia pertenece cada comprobante
(no hay `farmacia_id` ni CUIT del receptor), así que un CSV ajeno entra sin
dejar rastro y hay que reconstruir a mano cuál era de quién.

El único dato que distingue una farmacia de otra es el CUIT del RECEPTOR: el
emisor es la droguería, y es la misma para las dos. Estos tests fijan que ese
dato se lea y que, si no se puede leer, el import falle cerrado en vez de
importar a ciegas.
"""

import pytest

from routes.cuentas import _col_cuit_receptor, _leer_comprobantes_arca, _norm_hdr


def _hdr(*columnas):
    return {_norm_hdr(c): i for i, c in enumerate(columnas)}


# ── detección de la columna del receptor ────────────────────────────────────
# ARCA no usa un nombre estable entre exports, por eso se matchea por forma.

@pytest.mark.parametrize('columnas, esperado', [
    (('Fecha de Emisión', 'Nro. Doc. Receptor'), 1),
    (('Fecha de Emisión', 'CUIT Receptor'), 1),
    (('Fecha de Emisión', 'Nro. Doc. Emisor', 'Nro. Doc. Receptor'), 2),
])
def test_encuentra_la_columna_del_receptor(columnas, esperado):
    assert _col_cuit_receptor(_hdr(*columnas)) == esperado


def test_no_confunde_el_tipo_de_documento_con_el_numero():
    # 'Tipo Doc. Receptor' es el código de tipo (80 = CUIT), no el CUIT.
    hdr = _hdr('Fecha de Emisión', 'Tipo Doc. Receptor', 'Nro. Doc. Receptor')
    assert _col_cuit_receptor(hdr) == 2


def test_la_denominacion_no_cuenta_como_cuit():
    # Es la razón social del receptor, no sirve para comparar contra el CUIT.
    assert _col_cuit_receptor(_hdr('Fecha de Emisión', 'Denominación Receptor')) is None


def test_las_columnas_del_emisor_no_se_toman_por_receptor():
    hdr = _hdr('Fecha de Emisión', 'Nro. Doc. Emisor', 'Denominación Emisor')
    assert _col_cuit_receptor(hdr) is None


# ── lectura del CSV ─────────────────────────────────────────────────────────

_HDR_CSV = ('Fecha de Emisión;Tipo de Comprobante;Punto de Venta;Número Desde;'
            'Nro. Doc. Emisor;Denominación Emisor;Imp. Total')
_FILA_CSV = '2026-03-15;1;00001;00000123;30642661562;PHARMOS S.A.;1000,50'


def test_sin_columna_de_receptor_no_devuelve_ninguna_fila():
    # Falla cerrado: si no se puede verificar de quién son, no se importa nada.
    filas, err = _leer_comprobantes_arca(f'{_HDR_CSV}\n{_FILA_CSV}\n'.encode())
    assert filas == []
    assert err and 'receptor' in err.lower()


def test_lee_el_cuit_del_receptor_normalizado_a_digitos():
    csv = (f'{_HDR_CSV};Nro. Doc. Receptor\n'
           f'{_FILA_CSV};23-17460511-4\n').encode()
    filas, err = _leer_comprobantes_arca(csv)
    assert err is None
    # Sin guiones: se compara contra FARMACIA_CUIT, que puede venir en cualquier formato.
    assert filas[0]['cuit_receptor'] == '23174605114'
    assert filas[0]['cuit_emisor'] == '30642661562'


def test_la_fila_sin_cuit_de_receptor_queda_marcada_vacia():
    # El caller la trata como "no verificable" y rechaza el import completo.
    csv = (f'{_HDR_CSV};Nro. Doc. Receptor\n'
           f'{_FILA_CSV};\n').encode()
    filas, err = _leer_comprobantes_arca(csv)
    assert err is None
    assert filas[0]['cuit_receptor'] == ''
