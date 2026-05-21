"""Tests del motor de cálculo de cantidad (services/calculo_pedido.py).

`calcular_a_pedir` es función pura (dict in → dict out). Cubre:
- Redondeo ceil (default, histórico) vs round (configurable por tipo).
- Piso min_efectivo vs cobertura.
- Target factor_h vs cubrir_dias.
- Override cantidad_reposicion_fija.
- Guard sin rotación.

El redondeo 'round' se agregó el 2026-05-21 como opción configurable; los
tipos existentes (REPOSICION/COMPRA_LAB) usan 'ceil' → resultado idéntico.
"""
from services.calculo_pedido import calcular_a_pedir


class TestRedondeo:
    """ceil (default) vs round. Mismo input, distinto redondeo."""

    def _ctx(self):
        # daily_rate 2.3 × 4 días = 9.2 → ceil 10, round 9.
        return {'daily_rate': 2.3, 'min_efectivo': 0, 'cubrir_dias': 4,
                'stock_actual': 0, 'u12m': 100, 'sin_mov': False}

    def test_ceil_default(self):
        cfg = {'piso_ideal': 'daily_rate_x_cubrir_dias', 'target_horizonte': 'none',
               'redondeo': 'ceil'}
        r = calcular_a_pedir(cfg, self._ctx())
        assert r['a_pedir'] == 10  # ceil(9.2)

    def test_ceil_es_el_default_sin_campo(self):
        # Sin 'redondeo' en cfg → ceil (retrocompat).
        cfg = {'piso_ideal': 'daily_rate_x_cubrir_dias', 'target_horizonte': 'none'}
        r = calcular_a_pedir(cfg, self._ctx())
        assert r['a_pedir'] == 10

    def test_round_al_mas_cercano(self):
        cfg = {'piso_ideal': 'daily_rate_x_cubrir_dias', 'target_horizonte': 'none',
               'redondeo': 'round'}
        r = calcular_a_pedir(cfg, self._ctx())
        assert r['a_pedir'] == 9  # round(9.2)

    def test_round_redondea_arriba_sobre_medio(self):
        # daily_rate 2.4 × 4 = 9.6 → round 10, ceil 10 (mismo acá).
        ctx = dict(self._ctx(), daily_rate=2.4)
        cfg_r = {'piso_ideal': 'daily_rate_x_cubrir_dias', 'target_horizonte': 'none',
                 'redondeo': 'round'}
        assert calcular_a_pedir(cfg_r, ctx)['a_pedir'] == 10


class TestPisoYTarget:

    def test_piso_min_efectivo(self):
        # piso=min(20), target=none → ideal=20, a_pedir=20-stock(5)=15.
        cfg = {'piso_ideal': 'min_efectivo', 'target_horizonte': 'none'}
        r = calcular_a_pedir(cfg, {'daily_rate': 1, 'min_efectivo': 20,
                                   'stock_actual': 5, 'u12m': 50, 'sin_mov': False})
        assert r['a_pedir'] == 15

    def test_ideal_es_max_piso_target(self):
        # piso=min(10), target=cubrir(daily 1×30=30) → ideal=max(10,30)=30.
        cfg = {'piso_ideal': 'min_efectivo', 'target_horizonte': 'cubrir_dias_config'}
        r = calcular_a_pedir(cfg, {'daily_rate': 1, 'min_efectivo': 10,
                                   'cubrir_dias': 30, 'stock_actual': 0,
                                   'u12m': 365, 'sin_mov': False})
        assert r['ideal'] == 30
        assert r['a_pedir'] == 30


class TestOverrideYGuards:

    def test_sin_rotacion_devuelve_cero(self):
        cfg = {'piso_ideal': 'daily_rate_x_cubrir_dias'}
        r = calcular_a_pedir(cfg, {'daily_rate': 5, 'u12m': 0, 'sin_mov': False})
        assert r['a_pedir'] == 0
        assert r['regla_usada'] == 'sin_rotacion'

    def test_sin_mov_devuelve_cero(self):
        cfg = {'piso_ideal': 'daily_rate_x_cubrir_dias'}
        r = calcular_a_pedir(cfg, {'daily_rate': 5, 'u12m': 100, 'sin_mov': True})
        assert r['a_pedir'] == 0

    def test_override_cant_fija_cuando_stock_bajo_minimo(self):
        cfg = {'override_producto': 'cantidad_reposicion_fija',
               'piso_ideal': 'min_efectivo'}
        r = calcular_a_pedir(cfg, {'daily_rate': 1, 'min_efectivo': 20,
                                   'stock_actual': 5, 'cantidad_reposicion_fija': 30,
                                   'u12m': 100, 'sin_mov': False})
        assert r['a_pedir'] == 30
        assert r['override_aplicado'] is True

    def test_override_no_aplica_si_stock_arriba_minimo(self):
        cfg = {'override_producto': 'cantidad_reposicion_fija',
               'piso_ideal': 'min_efectivo', 'target_horizonte': 'none'}
        r = calcular_a_pedir(cfg, {'daily_rate': 1, 'min_efectivo': 20,
                                   'stock_actual': 50, 'cantidad_reposicion_fija': 30,
                                   'u12m': 100, 'sin_mov': False})
        assert r['override_aplicado'] is False
