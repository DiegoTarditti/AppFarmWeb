"""Sugerencia de cuánto cargar al robot en /rowa/carga.

La columna "Cargar" existía para decidir qué reponer y era **estructuralmente
siempre 0**: salía de `sug_en_robot - cantidad`, y `_recomendar()` sólo sabe
reducir — nunca fija un objetivo mayor al stock actual. Era el P0 de
`docs/backlog_rowa.md`.

Ahora usa la misma fórmula que `/rowa/planilla`: objetivo por demanda, acotado
por el cupo de ObServer y por lo que hay en el depósito.
"""
import pytest

from services.rowa_planilla import DIAS_AUTONOMIA


def _fila(en_robot, deposito, maximo, salidas, dias=DIAS_AUTONOMIA):
    """`_fila` recibe `stock_total` (robot + depósito) y deriva el depósito."""
    from services.rowa_planilla import _fila as fila_real
    return fila_real(
        pid=1, nombre='X', laboratorio='L', ean='1', en_robot=en_robot,
        maximo=maximo, stock_total=en_robot + deposito, salidas=salidas,
        origen_salidas='snap', dias_autonomia=dias)


def test_sin_stock_en_deposito_no_se_sugiere_nada():
    """Es el corte que pidió el operador: no se puede cargar lo que no está."""
    assert _fila(en_robot=0, deposito=0, maximo=6, salidas=2)['a_mover_sug'] == 0


def test_sin_cupo_cargado_no_se_opina():
    """Sin `cantidad_maxima` en ObServer no hay objetivo contra el cual comparar."""
    assert _fila(en_robot=0, deposito=10, maximo=None, salidas=2)['a_mover_sug'] == 0


def test_el_robot_lleno_no_pide_nada():
    assert _fila(en_robot=6, deposito=10, maximo=6, salidas=2)['a_mover_sug'] == 0


def test_sugiere_cubrir_la_demanda_sin_pasar_el_cupo():
    """objetivo = salidas/día × días de autonomía, techo en el máximo."""
    f = _fila(en_robot=0, deposito=99, maximo=6, salidas=0.1, dias=20)
    assert f['objetivo_sug'] == 2          # ceil(0.1 * 20) = 2, menor que el cupo
    assert f['a_mover_sug'] == 2


def test_el_cupo_le_gana_a_la_demanda():
    f = _fila(en_robot=0, deposito=99, maximo=6, salidas=5, dias=20)
    assert f['objetivo_sug'] == 6          # ceil(5*20)=100, pero el cupo es 6
    assert f['a_mover_sug'] == 6


def test_no_se_sugiere_mas_de_lo_que_hay_en_deposito():
    """El operador no puede mover packs que no existen."""
    f = _fila(en_robot=0, deposito=2, maximo=6, salidas=5, dias=20)
    assert f['a_mover_sug'] == 2
    assert f['parcial'] is True, 'tiene que avisar que el depósito no alcanzó'


def test_sin_señal_de_ventas_se_respeta_el_maximo():
    """Salidas en cero puede ser "no se mueve" o "no tenemos con qué medirlo".
    Tratarlo como demanda cero llevaba a vaciar el robot sin evidencia."""
    f = _fila(en_robot=1, deposito=10, maximo=6, salidas=0)
    assert f['sin_senal'] is True
    assert f['a_mover_sug'] == 5           # llena hasta el cupo, no opina de más
