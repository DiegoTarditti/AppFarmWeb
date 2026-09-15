"""Elegir el código de barra que la droguería entiende.

Caso real (pedido a Kellerhoff del 15/09/2026): de 139 renglones, **33
volvieron como "REGISTRO ERRONEO" con $0,00** — su sistema no reconoció el
código y esos renglones no se pidieron.

ObServer conocía 32 de esos 33 productos. El problema es que varios tienen más
de un EAN (packs, presentaciones, orígenes distintos) y el pedido mandaba el de
menor `orden`. De los 33, **10 tenían un alternativo que Kellerhoff usa en sus
propias facturas** — o sea que se pedían mal por elegir el código equivocado.
"""
from services.ean_proveedor import elegir_ean


def test_prefiere_el_que_el_proveedor_factura():
    """El caso del BACTRIM: le mandábamos 7792371004123 y ellos usan 7798129417115."""
    candidatos = ['7792371004123', '7798129417115', '7795380043381']
    conocidos = {'7798129417115'}
    assert elegir_ean(candidatos, conocidos) == '7798129417115'


def test_si_el_primero_ya_sirve_no_cambia_nada():
    candidatos = ['7790000000001', '7790000000002']
    assert elegir_ean(candidatos, {'7790000000001'}) == '7790000000001'


def test_sin_informacion_respeta_el_orden_de_observer():
    """Cuando el proveedor no factura ninguno, se mantiene el criterio anterior.

    No hay nada mejor que elegir, y cambiar el comportamiento donde no hay
    información nueva solo mueve el problema de lugar.
    """
    candidatos = ['7790000000001', '7790000000002']
    assert elegir_ean(candidatos, set()) == '7790000000001'


def test_el_caso_sertal_con_tres_codigos():
    """SERTAL Gotas: el (PACK) tiene su propio EAN y es el que se mandaba."""
    candidatos = ['7795345120577', '7795345002828']   # (PACK) primero
    assert elegir_ean(candidatos, {'7795345002828'}) == '7795345002828'


def test_sin_candidatos_devuelve_none():
    assert elegir_ean([], {'7790000000001'}) is None
