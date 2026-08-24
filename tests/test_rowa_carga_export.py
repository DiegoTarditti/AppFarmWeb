"""Exportaciones de la lista de carga del robot (XLSX / PDF).

`services/rowa_carga_export` es logica pura —recibe los items ya ordenados y
filtrados— asi que se testea sin robot ni DB. Vale la pena cubrirlo porque es lo
que se imprime para caminar el deposito: si el agrupado por laboratorio se
rompe, la lista deja de servir y no hay forma de notarlo desde la pantalla.
"""
import zipfile
from datetime import datetime
from io import BytesIO

import pytest

from services.rowa_carga_export import construir_pdf, construir_xlsx


def item(nombre, lab, *, cobertura=5, sug=3, deposito=9):
    return {
        "article_id": "A" + nombre, "ean": "7793742001234", "nombre": nombre,
        "laboratorio": lab, "cantidad": 4, "stock_deposito": deposito,
        "stock_total": 13, "salidas_dia": 0.8, "cobertura": cobertura,
        "urgencia": 1, "sug_cargar": sug, "tipo_aumento": None,
    }


ITEMS = [
    item("ADERMICINA CREMA", "Andromaco"),
    item("MACRIL OVULOS", "Andromaco", cobertura=999, sug=0),
    item("ASPIRINA 500", "Bayer"),
    item("PRODUCTO SUELTO", ""),          # sin laboratorio
]


def _celdas(contenido):
    """Valores de las celdas, como texto.

    Se leen con openpyxl y no del XML crudo: el XML trae la paleta de colores
    por defecto, donde aparecen cosas como "009999FF", y un `"999" in xml` da
    falso positivo.
    """
    import openpyxl
    ws = openpyxl.load_workbook(BytesIO(contenido)).active
    return [str(c.value) for fila in ws.iter_rows() for c in fila if c.value is not None]


def _texto_xlsx(contenido):
    return chr(10).join(_celdas(contenido))


# ── XLSX ──────────────────────────────────────────────────────────────────
def test_xlsx_es_un_archivo_valido():
    out = construir_xlsx(ITEMS, datetime(2026, 8, 24, 15, 30))
    assert out[:2] == b"PK"                      # firma zip → xlsx
    assert zipfile.ZipFile(BytesIO(out)).testzip() is None


def test_xlsx_pone_cada_laboratorio_como_titulo():
    texto = _texto_xlsx(construir_xlsx(ITEMS))
    # El laboratorio va con su conteo, no como columna repetida.
    assert "Andromaco  (2)" in texto
    assert "Bayer  (1)" in texto


def test_xlsx_agrupa_los_sin_laboratorio_bajo_un_titulo_propio():
    texto = _texto_xlsx(construir_xlsx(ITEMS))
    assert "Sin laboratorio  (1)" in texto


def test_xlsx_no_inventa_cobertura_cuando_no_hay_ventas():
    """cobertura 999 es 'sin salidas conocidas'; mostrar 999 d seria mentir."""
    celdas = _celdas(construir_xlsx([item("X", "L", cobertura=999)]))
    assert "999 d" not in celdas
    assert "—" in celdas                    # guion largo


# ── PDF ───────────────────────────────────────────────────────────────────
def test_pdf_es_un_archivo_valido():
    out = construir_pdf(ITEMS, datetime(2026, 8, 24, 15, 30))
    assert out[:4] == b"%PDF"
    assert b"%%EOF" in out[-1024:]


def test_pdf_crece_con_la_cantidad_de_items():
    chico = construir_pdf(ITEMS)
    grande = construir_pdf([item(f"PROD {i:03d}", f"Lab {i // 20}") for i in range(300)])
    assert len(grande) > len(chico)


# ── Bordes ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("construir", [construir_xlsx, construir_pdf])
def test_lista_vacia_no_rompe(construir):
    """Con todos los filtros puestos puede no quedar nada: tiene que bajar un
    archivo vacio, no un error 500."""
    out = construir([], datetime(2026, 8, 24))
    assert out and len(out) > 100


@pytest.mark.parametrize("construir", [construir_xlsx, construir_pdf])
def test_tolera_campos_en_none(construir):
    """stock_deposito/stock_total llegan en None cuando el articulo no cruzo
    con ObServer. No puede reventar la exportacion."""
    incompleto = item("SIN CRUCE", "Lab")
    incompleto["stock_deposito"] = None
    incompleto["stock_total"] = None
    assert construir([incompleto], datetime(2026, 8, 24))
