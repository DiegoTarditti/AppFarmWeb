"""Verificación de que la mercadería cargada entró de verdad al robot.

Registrar una carga no prueba nada: guarda lo que el operador declaró. El
25/8/2026 se registraron 23 packs y el robot nunca los tomó — su total sólo bajó
(10.115 → 10.102) y los 11 artículos quedaron con el mismo stock. La app los
daba por cargados.
"""
import pytest

from services.rowa_analisis import clasificar_carga


def test_el_stock_subio_lo_cargado():
    assert clasificar_carga(cantidad=5, stock_antes=14, stock_despues=19) == 'confirmada'


def test_subio_mas_de_lo_cargado_tambien_confirma():
    """Puede haber entrado algo más por otra vía; lo declarado está adentro."""
    assert clasificar_carga(cantidad=5, stock_antes=14, stock_despues=25) == 'confirmada'


def test_el_caso_del_25_de_agosto():
    """No subió nada: o sigue en la cinta, o el robot no la tomó."""
    assert clasificar_carga(cantidad=5, stock_antes=14, stock_despues=14) == 'no_detectada'


def test_si_el_stock_bajo_tampoco_se_detecta():
    """Bajar entre las dos mediciones significa que se despachó más de lo que
    entró: la carga no se puede dar por buena."""
    assert clasificar_carga(cantidad=5, stock_antes=14, stock_despues=12) == 'no_detectada'


def test_subio_menos_de_lo_cargado_es_parcial():
    assert clasificar_carga(cantidad=6, stock_antes=28, stock_despues=31) == 'parcial'


@pytest.mark.parametrize('antes,despues', [(None, 19), (14, None), (None, None)])
def test_sin_las_dos_mediciones_no_se_opina(antes, despues):
    """Si el robot no respondía al registrar, no hay "antes" contra el cual
    comparar: queda pendiente en vez de inventar un veredicto."""
    assert clasificar_carga(cantidad=5, stock_antes=antes, stock_despues=despues) == 'pendiente'


def test_una_carga_de_cero_no_se_confirma_sola():
    """Sin cantidad declarada no hay nada que verificar; que el stock no se
    mueva no la convierte en válida."""
    assert clasificar_carga(cantidad=0, stock_antes=14, stock_despues=14) == 'no_detectada'
