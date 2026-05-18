"""Tests del cálculo de métricas del pedido auto.

`calcular_metricas_pedido_auto` (en helpers.py) es función pura.
Testea: cantidad sugerida, pérdida en u/mes, pérdida en $, diagnóstico
del mínimo (BAJO/OK/ALTO/sin ventas).

Cada caso vino de los datos reales de Roemmers que probamos el 2026-04-26.
"""
import pytest

from helpers import calcular_metricas_pedido_auto


class TestCantidadSugerida:

    def test_con_maximo_definido_usa_max_minus_stock(self):
        m = calcular_metricas_pedido_auto(stock=11, minimo=20, maximo=50, u12m=120, m12m=12000)
        assert m['sugerido'] == 39  # 50 - 11
        assert m['base_sugerido'] == 'max-stock'

    def test_sin_maximo_usa_min_minus_stock(self):
        m = calcular_metricas_pedido_auto(stock=8, minimo=20, maximo=None, u12m=120, m12m=12000)
        assert m['sugerido'] == 12  # 20 - 8
        assert m['base_sugerido'] == 'min-stock'

    def test_maximo_menor_que_stock_cae_a_min_stock(self):
        # Si max=10 y stock=15 (no debería pasar pero por las dudas).
        m = calcular_metricas_pedido_auto(stock=15, minimo=20, maximo=10, u12m=12, m12m=1200)
        assert m['base_sugerido'] == 'min-stock'
        assert m['sugerido'] == 5  # 20 - 15

    def test_stock_negativo_resta_correcta(self):
        # Caso real de Roemmers: TERMOFREN tenía stock=-7.
        m = calcular_metricas_pedido_auto(stock=-7, minimo=5, maximo=None, u12m=72, m12m=72000)
        assert m['sugerido'] == 12  # 5 - (-7)

    def test_sugerido_minimo_1(self):
        # Si stock=minimo (caso borde), sugerimos al menos 1.
        m = calcular_metricas_pedido_auto(stock=20, minimo=20, maximo=None, u12m=12, m12m=1200)
        assert m['sugerido'] == 1


class TestPerdidaMensual:

    def test_sin_ventas_perdida_cero(self):
        m = calcular_metricas_pedido_auto(stock=0, minimo=10, maximo=None, u12m=0, m12m=0)
        assert m['perdida_mensual'] == 0.0
        assert m['perdida_pesos'] == 0.0

    def test_stock_cero_factor_completo(self):
        # u12m=120 → avg=10. stock=0 → factor=1 → perdida=10.
        m = calcular_metricas_pedido_auto(stock=0, minimo=10, maximo=None, u12m=120, m12m=12000)
        assert m['perdida_mensual'] == 10.0

    def test_factor_parcial(self):
        # min=20, stock=10 → factor=(20-10)/20=0.5. avg=10. perdida=5.
        m = calcular_metricas_pedido_auto(stock=10, minimo=20, maximo=None, u12m=120, m12m=12000)
        assert m['perdida_mensual'] == 5.0

    def test_factor_capeado_a_1(self):
        # stock=-5, min=10 → factor cruda = (10-(-5))/10 = 1.5, capea a 1.
        m = calcular_metricas_pedido_auto(stock=-5, minimo=10, maximo=None, u12m=120, m12m=12000)
        assert m['perdida_mensual'] == 10.0  # avg=10 * factor=1

    def test_minimo_cero_no_divide_por_cero(self):
        m = calcular_metricas_pedido_auto(stock=0, minimo=0, maximo=None, u12m=120, m12m=12000)
        assert m['perdida_mensual'] == 0.0

    def test_perdida_pesos_calc(self):
        # avg=10, factor=1 → perdida_mensual=10. Precio = 12000/120 = 100. → $1000/mes.
        m = calcular_metricas_pedido_auto(stock=0, minimo=10, maximo=None, u12m=120, m12m=12000)
        assert m['perdida_pesos'] == 1000.0

    def test_caso_real_vimax_100mg(self):
        # Caso real: VIMAX 100 mg COM x 2 — u12m=500, min=9, stock=0.
        # avg=41.67, factor=1, perdida=41.7 (redondeo a 1 decimal).
        m = calcular_metricas_pedido_auto(stock=0, minimo=9, maximo=None, u12m=500, m12m=50000)
        assert m['perdida_mensual'] == 41.7  # round(500/12, 1)


class TestDiagnosticoMinimo:

    def test_sin_ventas_diag_correcto(self):
        m = calcular_metricas_pedido_auto(stock=0, minimo=10, maximo=None, u12m=0, m12m=0)
        assert m['min_diag'] == 'sin_ventas'
        assert 'Sin ventas' in m['min_diag_label']

    def test_minimo_bajo(self):
        # avg=120/mes. min=10 → ratio=10/120=0.083. Bajo.
        m = calcular_metricas_pedido_auto(stock=0, minimo=10, maximo=None, u12m=1440, m12m=144000)
        assert m['min_diag'] == 'bajo'
        # Label sugiere subir a ~avg=120.
        assert '120' in m['min_diag_label']

    def test_minimo_ok(self):
        # avg=10/mes. min=10 → ratio=1. Cubre ~30 días. OK.
        m = calcular_metricas_pedido_auto(stock=0, minimo=10, maximo=None, u12m=120, m12m=12000)
        assert m['min_diag'] == 'ok'

    def test_minimo_alto(self):
        # avg=10/mes. min=30 → ratio=3 (>2). Alto.
        m = calcular_metricas_pedido_auto(stock=0, minimo=30, maximo=None, u12m=120, m12m=12000)
        assert m['min_diag'] == 'alto'
        # Sugerido baja a ~avg*1.5=15.
        assert '15' in m['min_diag_label']

    def test_borde_ratio_05_es_ok(self):
        # ratio=0.5 → ok (no bajo).
        m = calcular_metricas_pedido_auto(stock=0, minimo=5, maximo=None, u12m=120, m12m=12000)
        # avg=10, min=5, ratio=0.5 → ok
        assert m['min_diag'] == 'ok'

    def test_borde_ratio_2_es_ok(self):
        # ratio=2 → ok (no alto).
        m = calcular_metricas_pedido_auto(stock=0, minimo=20, maximo=None, u12m=120, m12m=12000)
        # avg=10, min=20, ratio=2 → ok
        assert m['min_diag'] == 'ok'


class TestPrecioUnitario:

    def test_precio_calc_promedio(self):
        # 12000 / 120 = 100.
        m = calcular_metricas_pedido_auto(stock=0, minimo=10, maximo=None, u12m=120, m12m=12000)
        assert m['precio_unit'] == 100.0

    def test_precio_cero_si_sin_ventas(self):
        m = calcular_metricas_pedido_auto(stock=0, minimo=10, maximo=None, u12m=0, m12m=0)
        assert m['precio_unit'] == 0.0

    def test_precio_redondeado_a_2_decimales(self):
        m = calcular_metricas_pedido_auto(stock=0, minimo=10, maximo=None, u12m=7, m12m=1234.5678)
        assert m['precio_unit'] == 176.37  # round(1234.5678/7, 2)


class TestEdgeCases:

    def test_inputs_none_no_revientan(self):
        m = calcular_metricas_pedido_auto(stock=None, minimo=None, maximo=None, u12m=None, m12m=None)
        # Sin ventas: no se propone compra.
        assert m['sugerido'] == 0
        assert m['base_sugerido'] == 'sin_ventas'
        assert m['perdida_mensual'] == 0.0
        assert m['min_diag'] == 'sin_ventas'

    def test_sin_ventas_sugerido_cero(self):
        # Aunque haya stock<minimo, si no hay ventas en 12m no proponemos compra.
        m = calcular_metricas_pedido_auto(stock=0, minimo=10, maximo=None, u12m=0, m12m=0)
        assert m['sugerido'] == 0
        assert m['base_sugerido'] == 'sin_ventas'

    def test_sin_ventas_con_maximo_sugerido_cero(self):
        # Idem aunque haya máximo configurado.
        m = calcular_metricas_pedido_auto(stock=0, minimo=10, maximo=50, u12m=0, m12m=0)
        assert m['sugerido'] == 0
        assert m['base_sugerido'] == 'sin_ventas'

    def test_sugerido_es_int(self):
        m = calcular_metricas_pedido_auto(stock=0, minimo=10, maximo=None, u12m=120, m12m=12000)
        assert isinstance(m['sugerido'], int)

    def test_returns_all_keys(self):
        m = calcular_metricas_pedido_auto(stock=0, minimo=10, maximo=None, u12m=120, m12m=12000)
        for k in ['sugerido', 'base_sugerido', 'avg_mensual', 'precio_unit',
                  'perdida_mensual', 'perdida_pesos', 'min_diag', 'min_diag_label']:
            assert k in m
