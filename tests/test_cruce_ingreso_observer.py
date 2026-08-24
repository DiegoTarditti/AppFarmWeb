"""Cruce de una factura contra el ingreso de mercadería de ObServer.

El número de comprobante en ObServer está sucio —sobre 400 recepciones de
Kellerhoff sólo 7 traían el de la factura— así que encontrar el ingreso no puede
depender de él. Lo que identifica es **fecha + proveedor + unidades + renglones**.
Ver `docs/controles_kellerhoff.md`.
"""
from datetime import date, datetime

import pytest

import database
import observer_source as obs


# ── Normalización del número ────────────────────────────────────────────────

@pytest.mark.parametrize('crudo', [
    '00046-00255798',   # nuestro
    '0046A00255798',    # Kellerhoff (letra en el medio)
    'A004600255798',    # ObServer (letra adelante)
    '46-255798',        # ya normalizado
])
def test_los_formatos_reales_normalizan_igual(crudo):
    assert obs._norm_nro(crudo) == '46-255798'


def test_el_remito_no_se_confunde_con_la_factura():
    # 0046 es el punto de venta de facturas, 0047 el de remitos.
    assert obs._norm_nro('R004700269365') != obs._norm_nro('A004600269365')


def test_el_like_usa_el_numero_sin_punto_de_venta():
    """'A004600255798' contiene '255798' pero NO '46255798': filtrar por los
    dígitos completos dejaría afuera justo lo que se busca."""
    patron = obs._like_nro('00046-00255798')
    assert patron == '255798'
    assert patron in 'A004600255798'


# ── Scoring ─────────────────────────────────────────────────────────────────

FECHA = date(2026, 8, 18)


def _fila(**kw):
    base = {'IdRecepcion': 1, 'fecha': datetime(2026, 8, 18, 13, 49),
            'nros': 'A 0046-00255798', 'nro_fac': 'A004600255798', 'nro_rem': None,
            'renglones': 138, 'unidades': 199}
    base.update(kw)
    return base


def test_la_recepcion_correcta_puntua_100():
    score, motivos = obs.puntuar_candidata(_fila(), FECHA, numero='00046-00255798',
                                           unidades=199, renglones=137)
    assert score == 100
    assert len(motivos) == 4


def test_las_unidades_solas_alcanzan_para_destacar():
    """Es el caso real: el número casi nunca está bien cargado."""
    fila = _fila(nros=None, nro_fac='R004700269365', nro_rem=None)
    score, motivos = obs.puntuar_candidata(fila, FECHA, numero='00046-00255798',
                                           unidades=199, renglones=137)
    assert score == 50          # unidades 30 + renglones 15 + fecha 5
    assert 'mismas unidades (199)' in motivos


def test_una_recepcion_cualquiera_del_mismo_dia_puntua_casi_cero():
    fila = _fila(IdRecepcion=2, nros=None, nro_fac='R004700269714', nro_rem=None,
                 renglones=8, unidades=20)
    score, _ = obs.puntuar_candidata(fila, FECHA, numero='00046-00255798',
                                     unidades=199, renglones=137)
    assert score == 5


def test_el_renglon_abierto_de_mas_no_rompe_el_match():
    """ObServer suele dejar un renglón en cero que la factura no tiene: la
    0046-00255798 tiene 137 y la recepción 138."""
    s137, _ = obs.puntuar_candidata(_fila(renglones=137), FECHA, renglones=137)
    s138, _ = obs.puntuar_candidata(_fila(renglones=138), FECHA, renglones=137)
    assert s137 == s138


def test_sin_datos_para_comparar_no_inventa_score():
    score, motivos = obs.puntuar_candidata(_fila(fecha=None), date(2020, 1, 1))
    assert score == 0
    assert motivos == []


# ── Elección del EAN ────────────────────────────────────────────────────────

def _cb(pk, producto, codigo, orden):
    return database.ObsCodigoBarras(id_codigo_barras=pk, producto_observer=producto,
                                    codigo_barras=codigo, orden=orden)


def test_elige_el_ean_que_usa_la_factura_y_no_el_principal():
    """Caso real de la 0046-00255798: GLAUCOSTAT tiene tres EAN y Kellerhoff
    factura con el SEGUNDO. Con `orden == 1` —el patrón del resto del módulo—
    ese renglón no cruzaba. Medido sobre la factura entera: 122 de 137 contra
    137 de 137."""
    with database.get_db() as session:
        session.add_all([
            _cb(1, 9591, '7791763000453', 1),
            _cb(2, 9591, '7798137190222', 2),
            _cb(3, 9591, '7798009279697', 3),
        ])
        session.commit()

    sin_pista = obs._eans_por_producto([9591])
    con_pista = obs._eans_por_producto([9591], eans_factura=['7798137190222'])

    assert sin_pista[9591] == '7791763000453'      # el principal
    assert con_pista[9591] == '7798137190222'      # el que usa la factura


def test_si_ninguno_coincide_cae_al_principal():
    with database.get_db() as session:
        session.add_all([_cb(1, 500, '111', 1), _cb(2, 500, '222', 2)])
        session.commit()

    assert obs._eans_por_producto([500], eans_factura=['999'])[500] == '111'


def test_los_codigos_dados_de_baja_no_se_usan():
    with database.get_db() as session:
        vigente = _cb(1, 600, '111', 2)
        baja = _cb(2, 600, '222', 1)
        baja.fecha_baja = datetime(2025, 1, 1)
        session.add_all([vigente, baja])
        session.commit()

    assert obs._eans_por_producto([600]) == {600: '111'}


def test_un_producto_sin_ean_no_aparece():
    assert obs._eans_por_producto([12345]) == {}
    assert obs._eans_por_producto([]) == {}
