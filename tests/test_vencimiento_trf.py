"""Detección de vencimiento de pago + TRF desde el texto de la factura Kellerhoff.

Texto real de 0013-17582775 (la muestra de Diego): el header trae
'COND. DE PAGO: 180 días FF Vto.: 28/07/2026' y el renglón 'TRF 0032915501Y'.
El PDF tiene otros dos 'Vto.' que NO son el de pago (el del renglón TRF y el
'Fecha de Vto' del CAEA de AFIP) — el detector ancla en COND. DE PAGO.
"""
from datetime import date

from helpers import detectar_vencimiento_trf

_FACTURA = """
DROGUERÍA KELLERHOFF S.A.
FACTURA Nº: 0013-17582775  FECHA: 29/01/2026 13:33
COND I.V.A.: IVA Responsable Inscripto
COND. DE PAGO: 180 días FF Vto.: 28/07/2026
Condición de Venta: 180 días FF Nº de OP: X000103556324
Código Barra Cant. Descripción Precio Público % Dto. Precio Unitario Importe
7798084684133 800 PARACETAMOL RAFFO GRIP NF CPR X 20 TL 12.918,24 55,17 5.791,31 4.633.049,20
TRF 0032915501Y
Vto. 28/07/2026
CAEA: 36020256311666 Fecha de Vto: 31.01.2026
"""


def test_detecta_vencimiento_condicion_y_trf():
    r = detectar_vencimiento_trf(_FACTURA)
    assert r['vencimiento'] == date(2026, 7, 28)
    assert r['condicion_pago'] == '180 días FF'
    assert r['trf'] == '0032915501Y'


def test_no_agarra_el_vto_del_caea():
    # El 'Fecha de Vto: 31.01.2026' del CAEA no debe ganar (formato con puntos y
    # fuera de COND. DE PAGO). El vencimiento correcto es el 28/07.
    r = detectar_vencimiento_trf(_FACTURA)
    assert r['vencimiento'] != date(2026, 1, 31)


def test_contado_o_sin_condicion_devuelve_none():
    r = detectar_vencimiento_trf('FACTURA CONTADO\n7790000000001 2 ALGO 100,00')
    assert r == {'vencimiento': None, 'condicion_pago': None, 'trf': None}


def test_fallback_calcula_por_dias_sin_vto_explicito():
    txt = 'COND. DE PAGO: 30 días\nalgo mas'
    r = detectar_vencimiento_trf(txt, fecha_factura=date(2026, 1, 1))
    assert r['vencimiento'] == date(2026, 1, 31)


def test_varios_trf_se_juntan_dedup():
    txt = 'TRF 0032915501Y\nTRF 0044000111\nTRF 0032915501Y'
    r = detectar_vencimiento_trf(txt)
    assert r['trf'] == '0032915501Y, 0044000111'


# ── filtrar_por_fecha (extracto de cuenta corriente) ────────────────────────

def _movs():
    return [
        {'comprobante': 'A', 'fecha': date(2026, 1, 5), 'saldo': 100},
        {'comprobante': 'B', 'fecha': date(2026, 2, 10), 'saldo': 250},
        {'comprobante': 'C', 'fecha': date(2026, 3, 20), 'saldo': 400},
    ]


def test_filtro_fecha_rango_inclusive():
    from services.cuenta_corriente import filtrar_por_fecha
    r = filtrar_por_fecha(_movs(), '2026-02-01', '2026-03-20')
    assert [m['comprobante'] for m in r] == ['B', 'C']   # 'C' entra (inclusive)


def test_filtro_fecha_sin_rango_devuelve_todo():
    from services.cuenta_corriente import filtrar_por_fecha
    assert len(filtrar_por_fecha(_movs(), None, None)) == 3


def test_filtro_fecha_solo_desde():
    from services.cuenta_corriente import filtrar_por_fecha
    r = filtrar_por_fecha(_movs(), '2026-02-10', None)
    assert [m['comprobante'] for m in r] == ['B', 'C']


def test_filtro_fecha_no_toca_saldo():
    # El saldo de cada fila queda como venía (acumulado): filtrar no recalcula.
    from services.cuenta_corriente import filtrar_por_fecha
    r = filtrar_por_fecha(_movs(), '2026-03-01', None)
    assert r[0]['saldo'] == 400
