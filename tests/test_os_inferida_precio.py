"""Cuánto paga el paciente según su obra social, desde el histórico de ventas.

El bug que motiva estos tests: la cobertura se calculaba con
`importe_efectivo / importe` — sólo efectivo. Pero el 42% de los pacientes con
obra social paga con tarjeta, y ahí `importe_efectivo` queda en 0 y la cuenta
concluía que el convenio cubrió el 100%.
"""
from datetime import date, timedelta

import database
from database import ObsVentaDetalle
from services.os_inferida import get_precio_os

FARMACIA = 10525
PRODUCTO = 555
OS = 7
_ID = [0]


def _venta(session, importe, efectivo=0, tarjeta=0, a_cargo_os=0, dias_atras=10,
           cheque=0, ctacte=0):
    _ID[0] += 1
    session.add(ObsVentaDetalle(
        id_producto_vendido=_ID[0], producto_observer=PRODUCTO,
        obra_social_observer=OS, es_venta_particular=False,
        cantidad=1, importe=importe,
        importe_efectivo=efectivo, importe_tarjeta=tarjeta,
        importe_cheque=cheque, importe_cuenta_corriente=ctacte,
        importe_a_cargo_os=a_cargo_os,
        fecha_estadistica=date.today() - timedelta(days=dias_atras),
        tipo_operacion='V', id_farmacia=FARMACIA))


def test_el_paciente_que_paga_con_tarjeta_no_es_cobertura_del_100():
    """El bug: con tarjeta, importe_efectivo=0 y decia que la OS cubria todo."""
    s = database.SessionLocal()
    try:
        for _ in range(5):
            _venta(s, importe=10000, tarjeta=3800, a_cargo_os=5400)
        s.commit()
        r = get_precio_os(s, PRODUCTO, OS, farmacia_id=FARMACIA)
        assert r['pct_paga_paciente'] == 38.0       # antes daba 0
        assert r['pct_cobertura_os'] == 54.0
        assert r['n_ventas'] == 5
    finally:
        s.close()


def test_suma_todos_los_medios_de_pago():
    s = database.SessionLocal()
    try:
        for _ in range(3):
            _venta(s, importe=10000, efectivo=1000, tarjeta=1000,
                   cheque=500, ctacte=500, a_cargo_os=7000)
        s.commit()
        assert get_precio_os(s, PRODUCTO, OS, farmacia_id=FARMACIA)['pct_paga_paciente'] == 30.0
    finally:
        s.close()


def test_el_descuento_comercial_explica_la_brecha():
    """`importe` es bruto: paciente + OS + descuento. Medido en OSDE el paciente
    paga 38% y la OS cubre 54% — el 8% que falta es descuento."""
    s = database.SessionLocal()
    try:
        for _ in range(4):
            _venta(s, importe=10000, tarjeta=3800, a_cargo_os=5400)
        s.commit()
        r = get_precio_os(s, PRODUCTO, OS, farmacia_id=FARMACIA)
        descuento = 100 - r['pct_paga_paciente'] - r['pct_cobertura_os']
        assert abs(descuento - 8.0) < 0.1
    finally:
        s.close()


def test_ignora_los_renglones_sin_dato_financiero():
    """El pago quedó asentado en otra línea de la operación: contarlo diría
    'cubierto al 100%'. Son 14.236 de 200.846 renglones en produccion."""
    s = database.SessionLocal()
    try:
        for _ in range(3):
            _venta(s, importe=10000, tarjeta=4000, a_cargo_os=6000)
        for _ in range(10):
            _venta(s, importe=10000)          # sin pago ni cobertura
        s.commit()
        r = get_precio_os(s, PRODUCTO, OS, farmacia_id=FARMACIA)
        assert r['n_ventas'] == 3
        assert r['pct_paga_paciente'] == 40.0
    finally:
        s.close()


def test_sin_datos_suficientes_no_afirma_nada():
    s = database.SessionLocal()
    try:
        _venta(s, importe=10000, tarjeta=4000, a_cargo_os=6000)
        _venta(s, importe=10000, tarjeta=4000, a_cargo_os=6000)
        s.commit()
        assert get_precio_os(s, PRODUCTO, OS, farmacia_id=FARMACIA) is None
    finally:
        s.close()


def test_ignora_ventas_de_otra_farmacia_o_de_otra_os():
    s = database.SessionLocal()
    try:
        for _ in range(3):
            _venta(s, importe=10000, tarjeta=4000, a_cargo_os=6000)
        s.commit()
        assert get_precio_os(s, PRODUCTO, OS, farmacia_id=99999) is None
        assert get_precio_os(s, PRODUCTO, 999, farmacia_id=FARMACIA) is None
    finally:
        s.close()
