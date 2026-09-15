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


def test_digitos_saca_la_sucursal_y_los_ceros():
    """Los dos sistemas guardan el remito con estructura distinta.

        appfarmweb  0047R00330825  -> sucursal 0047 + numero 00330825
        ObServer    R004700033082  -> R + sucursal 0047 + numero 00033082

    Hay que quedarse con el NÚMERO. Sacando los ceros de la cadena entera
    quedaban 4700330825 y 4700033082, que difieren en cinco posiciones y no en
    una: el rescate no encontraba nada (backfill del 15/09, 0 rescatados).
    """
    assert _digitos('0047R00330825') == '330825'
    assert _digitos('R004700330825') == '330825'    # el mismo, formato ObServer
    assert _digitos('R0047-00330825') == '330825'   # y con guión
    assert _digitos(None) == ''


def test_el_caso_real_es_una_edicion():
    correcto = _digitos('0047R00330825')    # como lo tiene la factura
    tipeado = _digitos('R004700033082')     # como quedó en ObServer
    assert correcto == '330825' and tipeado == '33082'
    assert _una_edicion(correcto, tipeado)


def test_no_rescata_contra_otro_remito_del_mismo_dia():
    """El 31/08 habia varios remitos en el rango 3304xx-3305xx."""
    correcto = _digitos('0047R00330825')
    for otro in ('R004700330470', 'R004700330471', 'R004700330556',
                 'R004700330584', 'R004700327684'):
        assert not _una_edicion(correcto, _digitos(otro)), otro


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
