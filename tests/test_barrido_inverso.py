"""Barrido inverso del cruce factura ↔ ingreso.

El cruce iba en un solo sentido y era ciego a lo que entra y la factura no
explica. Caso real (factura 00046-00316136): el DACTILUS figuraba como
«Artículo no encontrado en ERP» y se iba a reclamar, pero había entrado — lo
que pasa es que Kellerhoff lo factura con un EAN y ObServer lo tiene con otro.

Lo resuelve el descarte: de 112 renglones cruzaron 111 y sobró exactamente uno
de cada lado con la misma cantidad.
"""
from types import SimpleNamespace as NS

from data_extract import _barrido_inverso


def _erp(codigo, desc, cantidad):
    return NS(codigo_barra=codigo, descripcion=desc, cantidad=cantidad)


def _dif(codigo, desc, cant, obs='Artículo no encontrado en ERP'):
    return {'codigo_barra': codigo, 'descripcion': desc, 'cantidad_factura': cant,
            'cantidad_erp': 0, 'diferencia': cant, 'observaciones': obs}


def test_el_caso_real_deja_de_ser_un_faltante():
    """Uno de cada lado y las cantidades iguales: es el mismo producto."""
    cruzado = _erp('111', 'ALGO QUE SI CRUZO', 5)
    sobrante = _erp('5000456063647', 'DACTILUS 10 mg Rec.', 1)
    grupos = {'k': {'erp': cruzado}}
    difs = [_dif('7796285287405', 'DACTILUS 10 MG CPR X 28', 1)]

    _barrido_inverso(difs, grupos, ['k'], [cruzado, sobrante])

    assert len(difs) == 1                      # no se agrega un faltante nuevo
    d = difs[0]
    assert d['diferencia'] == 0                # deja de ser diferencia
    assert d['cantidad_erp'] == 1
    assert 'otro código' in d['observaciones']
    assert '5000456063647' in d['observaciones']


def test_si_las_cantidades_no_coinciden_no_empareja():
    """Mismo producto pero 1 contra 2 es un faltante de verdad, no un alias."""
    sobrante = _erp('5000456063647', 'DACTILUS 10 mg Rec.', 1)
    difs = [_dif('7796285287405', 'DACTILUS 10 MG CPR X 28', 2)]

    _barrido_inverso(difs, grupos={}, orden=[], all_erp=[sobrante])

    assert difs[0]['observaciones'] == 'Artículo no encontrado en ERP'
    assert difs[0]['diferencia'] == 2
    # Y el sobrante se reporta aparte, para que se vea.
    assert any('Entró pero no está en la factura' in d['observaciones'] for d in difs)


def test_con_dos_sobrantes_de_cada_lado_no_decide():
    """Ahí hay que elegir, y elegir mal esconde un faltante real."""
    s1 = _erp('AAA', 'UNO', 1)
    s2 = _erp('BBB', 'OTRO', 1)
    difs = [_dif('111', 'UNO FACTURADO', 1), _dif('222', 'OTRO FACTURADO', 1)]

    _barrido_inverso(difs, grupos={}, orden=[], all_erp=[s1, s2])

    faltantes = [d for d in difs if d['observaciones'] == 'Artículo no encontrado en ERP']
    assert len(faltantes) == 2                 # ninguno se dio por resuelto
    sobrantes = [d for d in difs if 'Entró pero no está' in d['observaciones']]
    assert len(sobrantes) == 2
    assert 'mismo producto con otro código' in sobrantes[0]['observaciones']


def test_lo_que_entra_de_mas_se_reporta():
    """Antes era invisible: puede ser mercadería de otra factura."""
    sobrante = _erp('999', 'ALGO QUE NADIE FACTURO', 3)
    difs = []

    _barrido_inverso(difs, grupos={}, orden=[], all_erp=[sobrante])

    assert len(difs) == 1
    assert difs[0]['cantidad_factura'] == 0
    assert difs[0]['cantidad_erp'] == 3
    assert difs[0]['diferencia'] == -3
    assert 'Entró pero no está en la factura' in difs[0]['observaciones']


def test_no_toca_nada_si_cruzo_todo():
    cruzado = _erp('111', 'TODO BIEN', 5)
    difs = []
    _barrido_inverso(difs, grupos={'k': {'erp': cruzado}}, orden=['k'], all_erp=[cruzado])
    assert difs == []
