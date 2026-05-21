"""Tests del source-of-truth de metricas de producto (services/producto_metrics.py).

Cubre las reglas decididas 2026-05-20:
- Una sola farmacia operativa: NO se suma entre farmacias.
- avg_3m y avg_12m con formulas correctas (mes actual parcial excluido).
- rotacion via rotation_index (A/M/B).
- sin_historial.
- cobertura_dias helper.
"""
from datetime import date

import pytest

import database
from database import ObsProducto, ObsStock, ObsVentaMensual
from services.producto_metrics import (
    cobertura_dias,
    farmacia_operativa,
    metricas_producto,
)

FARM = 10525          # operativa (default OBSERVER_ID_FARMACIA)
OTRA = 88888          # otra farmacia: debe ser ignorada
HOY = date(2026, 5, 15)


@pytest.fixture
def session_demo():
    s = database.SessionLocal()
    try:
        s.add(ObsProducto(observer_id=700, descripcion='ProdDemo', fecha_baja=None))
        s.commit()
        yield s
    finally:
        s.close()


def _venta(s, obs_id, anio, mes, unidades, farmacia=FARM):
    s.add(ObsVentaMensual(id_farmacia=farmacia, producto_observer=obs_id,
                          anio=anio, mes=mes, unidades=unidades, monto=0,
                          transacciones=0))


def test_stock_minimo_una_sola_farmacia(session_demo):
    """Stock/minimo deben venir SOLO de la farmacia operativa, no sumar la otra."""
    s = session_demo
    s.add(ObsStock(id_farmacia=FARM, producto_observer=700, stock_actual=3, minimo=1, maximo=5))
    s.add(ObsStock(id_farmacia=OTRA, producto_observer=700, stock_actual=100, minimo=50, maximo=200))
    s.commit()
    m = metricas_producto(s, 700, hoy=HOY)
    assert m['stock'] == 3        # NO 103
    assert m['minimo'] == 1       # NO 51
    assert m['maximo'] == 5


def test_avg_12m_y_3m(session_demo):
    """avg_12m = sum(11 meses cerrados)/11; avg_3m = ultimos 3 cerrados/3.
    El mes actual parcial ([11]) se excluye de ambos."""
    s = session_demo
    # 11 meses cerrados (2025-06 .. 2026-04) con 10 u c/u.
    meses = [(2025, 6), (2025, 7), (2025, 8), (2025, 9), (2025, 10), (2025, 11),
             (2025, 12), (2026, 1), (2026, 2), (2026, 3), (2026, 4)]
    for (y, mm) in meses:
        _venta(s, 700, y, mm, 10)
    # Mes actual parcial (2026-05) con un valor enorme → debe ignorarse.
    _venta(s, 700, 2026, 5, 999)
    s.commit()
    m = metricas_producto(s, 700, hoy=HOY)
    assert m['avg_12m'] == 10.0           # 110/11
    assert m['avg_3m'] == 10.0            # (feb+mar+abr)/3
    assert m['avg_monthly'] == m['avg_12m']  # alias backcompat
    assert m['ventas12'][11] == 999.0     # el actual se reporta pero no promedia


def test_avg_3m_distinto_de_12m(session_demo):
    """Producto con venta reciente alta y pasado bajo: avg_3m > avg_12m."""
    s = session_demo
    # 8 meses viejos en 0, ultimos 3 cerrados (feb,mar,abr 2026) con 30 c/u.
    for (y, mm) in [(2026, 2), (2026, 3), (2026, 4)]:
        _venta(s, 700, y, mm, 30)
    s.commit()
    m = metricas_producto(s, 700, hoy=HOY)
    assert m['avg_3m'] == 30.0            # 90/3
    assert m['avg_12m'] == round(90 / 11, 2)  # 8.18
    assert m['avg_3m'] > m['avg_12m']


def test_rotacion_thresholds(session_demo):
    """rotation_index sobre avg_12m: A>=20, M>=5, B resto."""
    s = session_demo
    # avg_12m = 22 → A. 11 meses x 22 = 242, /11 = 22.
    for mm_off in range(11):
        y, mm = (2025, 6 + mm_off) if 6 + mm_off <= 12 else (2026, 6 + mm_off - 12)
        _venta(s, 700, y, mm, 22)
    s.commit()
    m = metricas_producto(s, 700, hoy=HOY)
    assert m['avg_12m'] == 22.0
    assert m['rotacion'] == 'A'


def test_sin_historial(session_demo):
    """Sin ventas → sin_historial=True, avg=0, rotacion=B."""
    s = session_demo
    s.add(ObsStock(id_farmacia=FARM, producto_observer=700, stock_actual=0, minimo=0))
    s.commit()
    m = metricas_producto(s, 700, hoy=HOY)
    assert m['sin_historial'] is True
    assert m['avg_12m'] == 0.0
    assert m['avg_3m'] == 0.0
    assert m['rotacion'] == 'B'


def test_sin_stock_row(session_demo):
    """Producto sin fila en obs_stock → stock=0, minimo=0, maximo=None (no rompe)."""
    s = session_demo
    m = metricas_producto(s, 700, hoy=HOY)
    assert m['stock'] == 0
    assert m['minimo'] == 0
    assert m['maximo'] is None


def test_cobertura_dias():
    """stock / (avg/divisor). divisor unico estandarizado."""
    assert cobertura_dias(30, 30, divisor=30) == 30.0   # 30 / (30/30=1)
    assert cobertura_dias(15, 30, divisor=30) == 15.0
    assert cobertura_dias(10, 0) is None                # sin demanda
    assert cobertura_dias(10, None) is None


def test_farmacia_operativa_default():
    assert farmacia_operativa() == 10525
