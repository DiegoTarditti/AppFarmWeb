"""Impresión de la planilla de carga: PDF y XLSX.

Es una orden de trabajo, no un informe: alguien la imprime y camina el depósito
con ella. Si el agrupado por laboratorio se rompe, o si el número que hay que
ejecutar deja de estar resaltado, no se nota en pantalla — se nota con el
changuito en la mano.
"""
import zipfile
from datetime import datetime
from io import BytesIO

import pytest

from services.rowa_planilla_export import construir_pdf, construir_xlsx

TOTALES = {"articulos": 3, "packs_sug": 10, "packs_max": 27}


def fila(nombre="PROD", lab="Bayer", robot=2, maximo=6, deposito=10,
         sug=2, mover_max=4, **kw):
    d = {"nombre": nombre, "laboratorio": lab, "en_robot": robot, "maximo": maximo,
         "deposito": deposito, "a_mover_sug": sug, "a_mover_max": mover_max,
         "vacio": robot == 0, "parcial": False, "diferencia": False,
         "sin_maximo": False, "corregir_a": None}
    d.update(kw)
    return d


def celdas(contenido):
    """Valores de las celdas. Leídas con openpyxl y no del XML crudo: el XML
    trae la paleta de colores por defecto y da falsos positivos."""
    import openpyxl
    ws = openpyxl.load_workbook(BytesIO(contenido)).active
    return [str(c.value) for f in ws.iter_rows() for c in f if c.value is not None]


# ── XLSX ──────────────────────────────────────────────────────────────────
def test_xlsx_es_valido():
    out = construir_xlsx([fila()], TOTALES, datetime(2026, 8, 24, 15, 30))
    assert out[:2] == b"PK"
    assert zipfile.ZipFile(BytesIO(out)).testzip() is None


def test_xlsx_agrupa_por_laboratorio_con_titulo():
    """El laboratorio deja de ser columna: en papel repetirlo no separa nada."""
    c = celdas(construir_xlsx([fila(nombre="A", lab="Bayer"),
                               fila(nombre="B", lab="Bayer"),
                               fila(nombre="C", lab="Roemmers")], TOTALES))
    assert "Bayer  (2)" in c
    assert "Roemmers  (1)" in c


def test_xlsx_los_sin_laboratorio_llevan_titulo_propio():
    assert "Sin laboratorio  (1)" in celdas(construir_xlsx([fila(lab="")], TOTALES))


def test_xlsx_no_repite_el_laboratorio_como_columna():
    c = celdas(construir_xlsx([fila(lab="Bayer")], TOTALES))
    assert "Laboratorio" not in c        # no hay encabezado de esa columna


def test_xlsx_trae_el_encabezado_con_los_totales():
    c = " ".join(celdas(construir_xlsx([fila()], TOTALES, datetime(2026, 8, 24, 15, 30))))
    assert "24/08/2026" in c
    assert "10 packs a mover" in c


# ── PDF ───────────────────────────────────────────────────────────────────
def test_pdf_es_valido_y_cierra():
    out = construir_pdf([fila()], TOTALES, datetime(2026, 8, 24))
    assert out[:4] == b"%PDF"
    assert b"%%EOF" in out[-1024:]


def test_pdf_crece_con_las_filas():
    chico = construir_pdf([fila()], TOTALES)
    grande = construir_pdf([fila(nombre="P%03d" % i, lab="Lab %d" % (i // 20))
                            for i in range(300)], TOTALES)
    assert len(grande) > len(chico)


# ── Avisos ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("kw,esperado", [
    ({"robot": 0}, "VACÍO"),
    ({"parcial": True}, "parcial"),
    ({"corregir_a": 3}, "máx→3"),
    ({"diferencia": True}, "DIF"),
    ({"sin_maximo": True}, "sin máx"),
])
def test_los_avisos_salen_abreviados(kw, esperado):
    """En papel no hay tooltip: se abrevian y la explicación va al pie."""
    assert esperado in " ".join(celdas(construir_xlsx([fila(**kw)], TOTALES)))


def test_un_articulo_puede_llevar_varios_avisos():
    txt = " ".join(celdas(construir_xlsx([fila(robot=0, parcial=True)], TOTALES)))
    assert "VACÍO" in txt and "parcial" in txt


# ── Bordes ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("construir", [construir_xlsx, construir_pdf])
def test_planilla_vacia_no_rompe(construir):
    """Con todos los filtros puestos puede no quedar nada: tiene que bajar un
    archivo vacío, no un error 500."""
    assert len(construir([], {"articulos": 0, "packs_sug": 0, "packs_max": 0})) > 100


@pytest.mark.parametrize("construir", [construir_xlsx, construir_pdf])
def test_tolera_deposito_en_none(construir):
    """`stock_total` llega en None cuando el artículo no cruzó con ObServer."""
    assert construir([fila(deposito=None)], TOTALES)


def test_a_mover_cero_se_muestra_igual():
    """Un cero explícito dice "no muevas nada", que es información. La columna
    de referencia sí queda en blanco cuando no aporta."""
    c = celdas(construir_xlsx([fila(sug=0, mover_max=0)], TOTALES))
    assert "0" in c
