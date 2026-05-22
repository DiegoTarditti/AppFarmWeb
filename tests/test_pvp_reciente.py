"""Tests de pvp_reciente (purchase_helpers): PVP del último mes con ventas.

Reemplaza al promedio 12m (monto12m/unidades12m), que bajo inflación
subestima el precio actual. Acá se toma el último mes que vendió.
"""
from purchase_helpers import pvp_reciente


def test_usa_el_ultimo_mes_con_ventas():
    # slot 11 = más reciente: vendió 2 u por $120.000 → PVP 60.000.
    u = [0]*11 + [2]
    m = [0.0]*11 + [120000.0]
    assert pvp_reciente(u, m) == 60000.0


def test_salta_meses_sin_ventas_hacia_atras():
    # último mes (11) sin ventas; el 9 vendió 1 u a $50.000 → toma ese.
    u = [0]*9 + [1, 0, 0]
    m = [0.0]*9 + [50000.0, 0.0, 0.0]
    assert pvp_reciente(u, m) == 50000.0


def test_prefiere_mas_reciente_aunque_haya_viejos():
    # vendió en el mes 3 (a 100) y en el 11 (a 200) → usa el 11.
    u = [0, 0, 0, 5] + [0]*7 + [4]
    m = [0, 0, 0, 500.0] + [0.0]*7 + [800.0]
    assert pvp_reciente(u, m) == 200.0


def test_nunca_vendio_da_cero():
    assert pvp_reciente([0]*12, [0.0]*12) == 0.0


def test_unidades_sin_monto_da_cero():
    # caso raro: unidades>0 pero monto 0 → no inventa precio.
    u = [0]*11 + [3]
    m = [0.0]*12
    assert pvp_reciente(u, m) == 0.0
