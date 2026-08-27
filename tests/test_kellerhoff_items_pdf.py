"""Parseo de ítems del PDF Kellerhoff (con descuento).

La tabla HTML del portal NO trae la columna %Dto; el PDF sí. Estas funciones
sacan el dto (y los ítems completos) del texto del PDF para completar lo que el
HTML deja sin descuento. Texto real de 0013-17582775 (la muestra de Diego).
"""
from services.kellerhoff_analizador import dto_por_barcode, parsear_items_pdf

# Cuerpo real (un renglón) + líneas TRF/Vto + pie + sección de faltantes.
_PDF = """DROGUERÍA KELLERHOFF S.A. FACTURA Nº: 0013-17582775
Código Barra Cant. Descripción Precio Público % Dto. Precio Unitario Importe
7798084684133 800 PARACETAMOL RAFFO GRIP NF CPR X 20 TL 12.918,24 55,17 5.791,31 4.633.049,20
TRF 0032915501Y
Vto. 28/07/2026
Perc.II.BB.Santa Fe $23165,25
*** PRODUCTOS EN FALTA MOMENTANEA ***
7791234567890 10 ALGO QUE NO SE FACTURO X 30
TOTAL: SON PESOS
"""

_PDF_MULTI = """7798084684133 800 PARACETAMOL RAFFO GRIP NF CPR X 20 TL 12.918,24 55,17 5.791,31 4.633.049,20
7791111111111 12 IBUPIRAC 600 COMP X 20 WEB 3.400,00 0,00 3.400,00 40.800,00
"""


def test_saca_el_dto_del_renglon():
    items = parsear_items_pdf(_PDF)
    assert len(items) == 1
    it = items[0]
    assert it['barcode'] == '7798084684133'
    assert it['cantidad'] == 800
    assert it['dto_pct'] == 55.17          # lo que el HTML deja en None
    assert it['precio_pub'] == 12918.24
    assert it['precio_unitario'] == 5791.31
    assert it['importe'] == 4633049.20
    assert 'PARACETAMOL' in it['descripcion']


def test_no_mezcla_la_seccion_de_faltantes():
    # El renglón de faltantes (sin precios) no debe entrar como ítem.
    items = parsear_items_pdf(_PDF)
    assert all(i['barcode'] != '7791234567890' for i in items)


def test_dto_por_barcode_mapea():
    m = dto_por_barcode(_PDF_MULTI)
    assert m == {'7798084684133': 55.17, '7791111111111': 0.0}


def test_dto_cero_es_cero_no_none():
    # Un ítem sin descuento tiene dto 0,00 explícito (distinto de "no se parseó").
    items = parsear_items_pdf(_PDF_MULTI)
    ibu = next(i for i in items if i['barcode'] == '7791111111111')
    assert ibu['dto_pct'] == 0.0
