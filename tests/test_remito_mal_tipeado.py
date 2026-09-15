"""Rescate de remitos mal tipeados en ObServer.

Caso real (31/08/2026): la recepción 207994 quedó cargada como R004700033082
cuando el remito de la factura era R004700330825 — le faltaba el 5 final y
ObServer completó con ceros a la izquierda. La factura 00046-00314484 figuraba
como "no entró" aunque la mercadería estaba, y de paso nadie vio que esa entrega
vino incompleta (2 de 4 renglones).

No se puede corregir en ObServer, así que se rescata acá exigiendo DOS señales:
número parecido Y que todos los productos recibidos estén en la factura.
"""
from observer_source import _digitos, _una_edicion


def test_digitos_ignora_formato_y_ceros():
    """El relleno de ceros de ObServer no puede contar como diferencia."""
    assert _digitos('R0047-00330825') == _digitos('R004700330825') == '4700330825'
    assert _digitos('00033082') == '33082'
    assert _digitos(None) == ''


def test_el_caso_real_es_una_edicion():
    correcto = _digitos('00330825')     # el de la factura
    tipeado = _digitos('00033082')      # el que quedó en ObServer
    assert correcto == '330825' and tipeado == '33082'
    assert _una_edicion(correcto, tipeado)


def test_una_edicion_cubre_los_tres_errores_de_tipeo():
    assert _una_edicion('330825', '33082')      # falta un dígito
    assert _una_edicion('330825', '3308254')    # sobra un dígito
    assert _una_edicion('330825', '330815')     # un dígito cambiado
    assert _una_edicion('330825', '330825')     # iguales


def test_una_edicion_rechaza_lo_que_no_es_un_tipeo():
    assert not _una_edicion('330825', '330852')   # dos dígitos permutados
    assert not _una_edicion('330825', '331925')   # dos cambiados
    assert not _una_edicion('330825', '3308')     # faltan dos
    assert not _una_edicion('330825', '440936')   # otro remito
    assert not _una_edicion('330825', '')


def test_no_afirma_con_numeros_de_largo_muy_distinto():
    """Un remito corto no puede rescatar a uno largo por más que empiece igual."""
    assert not _una_edicion('330825', '33')
