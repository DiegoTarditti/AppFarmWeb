"""Planilla de carga del robot: qué mover del depósito a la máquina.

`services/rowa_planilla` es lógica pura, así que se testea sin robot ni DB. Vale
la pena cubrirla porque es lo que alguien imprime y sale a caminar el depósito:
un número mal no se nota en pantalla, se nota con el changuito en la mano.
"""
import pytest

from services.rowa_planilla import (
    DIAS_CONFIANZA_PLENA,
    FACTOR_OBSERVER,
    confianza_snapshots,
    construir_planilla,
    salidas_estimadas,
)


def art(pid=1, nombre="PROD", lab="Lab", en_robot=2, maximo=6, stock_total=10,
        snap=None, obs=None):
    return {"producto_observer": pid, "nombre": nombre, "laboratorio": lab,
            "ean": "779", "en_robot": en_robot, "maximo": maximo,
            "stock_total": stock_total, "salidas_snapshot": snap,
            "salidas_observer": obs}


def sola(a, dias_ventana=14.0, dias_autonomia=7):
    """Arma la planilla con un solo artículo y devuelve su fila."""
    r = construir_planilla([a], dias_ventana, dias_autonomia)
    return r["filas"][0] if r["filas"] else None


# ── La regla operativa: llenar hasta el máximo con lo que haya ────────────
def test_mueve_la_diferencia_hasta_el_maximo():
    f = sola(art(en_robot=2, maximo=6, stock_total=10, snap=5))
    assert f["a_mover_max"] == 4          # 6 - 2, y el depósito tiene 8


def test_si_el_deposito_no_alcanza_mueve_lo_que_hay_y_avisa():
    """stock_total 3 con 2 en el robot → sólo 1 en depósito para un hueco de 4."""
    f = sola(art(en_robot=2, maximo=6, stock_total=3, snap=5))
    assert f["a_mover_max"] == 1
    assert f["parcial"] is True


def test_sin_deposito_no_entra_a_la_planilla():
    """Hueco pero nada que mover: mandar a buscar lo que no está no es trabajo."""
    assert sola(art(en_robot=2, maximo=6, stock_total=2, snap=5)) is None


def test_robot_en_cero_con_deposito_es_el_caso_mas_fuerte():
    f = sola(art(en_robot=0, maximo=6, stock_total=20, snap=1))
    assert f["vacio"] is True
    assert f["a_mover_max"] == 6


# ── El dato inteligente ───────────────────────────────────────────────────
def test_el_sugerido_pide_menos_cuando_el_maximo_esta_holgado():
    """1 u/día × 7 días = 7, pero ya hay 6 en el robot → no mover nada."""
    f = sola(art(en_robot=6, maximo=20, stock_total=100, snap=1.0))
    assert f["objetivo_sug"] == 7
    assert f["a_mover_sug"] == 1
    assert f["a_mover_max"] == 14          # la regla del máximo pediría 14


def test_el_sugerido_nunca_supera_al_maximo():
    """Aunque la demanda justifique más, no entra más de lo que cabe."""
    f = sola(art(en_robot=1, maximo=6, stock_total=100, snap=10.0))
    assert f["objetivo_sug"] == 6
    assert f["a_mover_sug"] == f["a_mover_max"] == 5


def test_el_objetivo_nunca_baja_de_uno():
    """Dejar el canal en cero es sacar el producto de circulacion.

    Aplica cuando HAY senal: 0,05 x 7 = 0,35, que redondeado para arriba da 1.
    Con salidas en cero no se opina — eso es `test_sin_senal_se_respeta_el_maximo`."""
    f = sola(art(en_robot=0, maximo=6, stock_total=5, snap=0.05))
    assert f["objetivo_sug"] == 1


# ── Avisos ────────────────────────────────────────────────────────────────
def test_marca_corregir_cuando_el_maximo_no_se_justifica():
    """20 de máximo con 0,2 u/día → objetivo 2. Sobran 18, el 90%."""
    f = sola(art(en_robot=1, maximo=20, stock_total=50, snap=0.2))
    assert f["corregir_a"] == 2


def test_no_marca_corregir_por_diferencias_chicas():
    """Editar ObServer es trabajo manual: no se avisa por 1 pack."""
    f = sola(art(en_robot=1, maximo=6, stock_total=50, snap=0.7))
    assert f["objetivo_sug"] == 5          # sobra 1, no llega al umbral
    assert f["corregir_a"] is None


def test_marca_diferencia_cuando_el_robot_tiene_mas_que_observer():
    """Imposible: el robot es parte del stock total. Antes desaparecía."""
    f = sola(art(en_robot=5, maximo=6, stock_total=2, snap=1))
    assert f["diferencia"] is True
    assert f["deposito"] == -3
    assert f["a_mover_max"] == 0           # no se calcula sobre un dato roto


def test_sin_maximo_entra_si_esta_en_la_maquina():
    """Hay que ir a cargarle el cupo en ObServer para que entre al circuito."""
    f = sola(art(maximo=None, en_robot=3))
    assert f["sin_maximo"] is True
    assert f["a_mover_max"] == 0


def test_sin_maximo_y_fuera_del_robot_no_entra():
    """Un producto sin cupo que no esta en la maquina simplemente no va al robot
    (brochas, jeringas, electrodos). Dejarlos pasar metia 7.886 filas de ruido."""
    assert sola(art(maximo=None, en_robot=0, stock_total=50)) is None


def test_stock_negativo_en_observer_no_se_confunde_con_diferencia():
    """Con el robot en cero, un deposito negativo es stock negativo en ObServer:
    otro problema, y no se arregla mirando la maquina."""
    assert sola(art(en_robot=0, maximo=6, stock_total=-3, snap=1)) is None


# ── La mezcla que se extingue ─────────────────────────────────────────────
def test_sin_snapshots_manda_observer_calibrado():
    v, origen = salidas_estimadas(0.0, 10.0, dias_ventana=0.0)
    assert v == pytest.approx(10.0 * FACTOR_OBSERVER)
    assert origen == "mezcla"


def test_con_ventana_completa_observer_deja_de_pesar():
    v, _ = salidas_estimadas(4.0, 99.0, dias_ventana=DIAS_CONFIANZA_PLENA)
    assert v == pytest.approx(4.0)


def test_a_media_ventana_pesan_mitad_y_mitad():
    v, _ = salidas_estimadas(4.0, 4.0, dias_ventana=DIAS_CONFIANZA_PLENA / 2)
    assert v == pytest.approx(0.5 * 4.0 + 0.5 * 4.0 * FACTOR_OBSERVER)


@pytest.mark.parametrize("dias,esperado", [(0, 0.0), (3.5, 0.25), (14, 1.0), (30, 1.0)])
def test_la_confianza_crece_y_se_topea(dias, esperado):
    assert confianza_snapshots(dias) == pytest.approx(esperado)


def test_sin_ninguna_senal_no_inventa():
    v, origen = salidas_estimadas(None, None, dias_ventana=7)
    assert v == 0.0
    assert origen == "sin datos"


# ── Totales y orden ───────────────────────────────────────────────────────
def test_ordena_por_laboratorio_y_despues_alfabetico():
    r = construir_planilla([
        art(pid=1, nombre="ZZZ", lab="Bayer", snap=5),
        art(pid=2, nombre="AAA", lab="Bayer", snap=5),
        art(pid=3, nombre="MMM", lab="Andromaco", snap=5),
    ], dias_ventana=14.0)
    assert [f["nombre"] for f in r["filas"]] == ["MMM", "AAA", "ZZZ"]


def test_los_sin_laboratorio_van_al_final():
    r = construir_planilla([
        art(pid=1, nombre="AAA", lab="", snap=5),
        art(pid=2, nombre="BBB", lab="Zeta", snap=5),
    ], dias_ventana=14.0)
    assert [f["nombre"] for f in r["filas"]] == ["BBB", "AAA"]


def test_los_totales_cuentan_cada_aviso():
    r = construir_planilla([
        art(pid=1, en_robot=0, maximo=6, stock_total=20, snap=1),    # vacío
        art(pid=2, en_robot=2, maximo=6, stock_total=3, snap=5),     # parcial
        art(pid=3, en_robot=5, maximo=6, stock_total=2, snap=1),     # diferencia
        art(pid=4, maximo=None),                                     # sin máximo
    ], dias_ventana=14.0)
    t = r["totales"]
    assert (t["vacios"], t["parciales"], t["diferencias"], t["sin_maximo"]) == (1, 1, 1, 1)
    assert t["dias_autonomia"] == 7
    assert t["confianza"] == 1.0


def test_planilla_vacia_no_rompe():
    r = construir_planilla([], dias_ventana=0.0)
    assert r["filas"] == []
    assert r["totales"]["articulos"] == 0


# ── Sin señal no se opina ─────────────────────────────────────────────────
def test_sin_senal_se_respeta_el_maximo():
    """Salidas 0 puede ser "no se mueve" o "no tenemos con que medirlo". Un
    articulo fuera del robot no genera snapshots, asi que tratar la ausencia de
    dato como demanda cero llevaba a proponer bajar un maximo de 6 a 1."""
    f = sola(art(en_robot=0, maximo=6, stock_total=6, snap=None, obs=0.0))
    assert f["sin_senal"] is True
    assert f["objetivo_sug"] == 6              # se respeta la decision humana
    assert f["a_mover_sug"] == f["a_mover_max"] == 6
    assert f["corregir_a"] is None             # no se propone nada sin evidencia


def test_con_senal_si_se_opina():
    f = sola(art(en_robot=0, maximo=6, stock_total=6, snap=0.1))
    assert f["sin_senal"] is False
    assert f["corregir_a"] == 1                # 0,1 x 7 = 0,7 -> piso 1


def test_los_totales_cuentan_los_sin_senal():
    r = construir_planilla([
        art(pid=1, en_robot=0, maximo=6, stock_total=6, obs=0.0),
        art(pid=2, en_robot=0, maximo=6, stock_total=6, snap=2.0),
    ], dias_ventana=14.0)
    assert r["totales"]["sin_senal"] == 1
