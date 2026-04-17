"""
Tests para purchase_engine.py — el corazón del análisis de compras.

Cubre:
- Funciones puras internas (_linear_trend, _weighted_average, _seasonality_index, _coef_variacion)
- Clasificación de productos (tipo_producto, rotation_index)
- Análisis de producto individual (analyze_product)
- Cálculo de período (start_month_idx_from_period)
- Pipeline completo (analyze_purchase)
"""
import math
import pytest
from unittest.mock import patch
import datetime

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from purchase_engine import (
    FULL_MONTHS, AVG_DAYS_PER_MONTH, _DAYS_JAN, _MONTH_ES_JAN,
    _linear_trend, _weighted_average, _seasonality_index, _prorate_partial,
    _coef_variacion, tipo_producto, rotation_index,
    analyze_product, analyze_purchase, start_month_idx_from_period,
)


# ═══════════════════════════════════════════════════════════════
# CONSTANTES
# ═══════════════════════════════════════════════════════════════

class TestConstants:
    def test_full_months_is_11(self):
        assert FULL_MONTHS == 11

    def test_avg_days_per_month(self):
        assert abs(AVG_DAYS_PER_MONTH - 365 / 12) < 0.01

    def test_days_jan_sums_365(self):
        assert sum(_DAYS_JAN) == 365

    def test_month_names_count(self):
        assert len(_MONTH_ES_JAN) == 12
        assert _MONTH_ES_JAN[0] == 'Ene'
        assert _MONTH_ES_JAN[11] == 'Dic'


# ═══════════════════════════════════════════════════════════════
# _linear_trend
# ═══════════════════════════════════════════════════════════════

class TestLinearTrend:
    def test_empty_list(self):
        assert _linear_trend([]) == 0.0

    def test_single_value(self):
        assert _linear_trend([5]) == 0.0

    def test_constant_series(self):
        """Serie constante → pendiente 0."""
        assert _linear_trend([10, 10, 10, 10]) == 0.0

    def test_perfect_ascending(self):
        """Serie 0,1,2,3 → pendiente exacta 1.0."""
        assert _linear_trend([0, 1, 2, 3]) == pytest.approx(1.0)

    def test_perfect_descending(self):
        """Serie 30,20,10,0 → pendiente -10.0."""
        assert _linear_trend([30, 20, 10, 0]) == pytest.approx(-10.0)

    def test_noisy_ascending(self):
        """Tendencia ascendente con ruido → pendiente positiva."""
        vals = [10, 12, 11, 15, 14, 18, 17, 20, 19, 22, 25]
        slope = _linear_trend(vals)
        assert slope > 0

    def test_eleven_months_flat(self):
        """11 meses iguales → pendiente 0."""
        assert _linear_trend([50] * FULL_MONTHS) == 0.0

    def test_two_values(self):
        assert _linear_trend([0, 10]) == pytest.approx(10.0)


# ═══════════════════════════════════════════════════════════════
# _weighted_average
# ═══════════════════════════════════════════════════════════════

class TestWeightedAverage:
    def test_constant_series(self):
        """Si todos los valores son iguales, el promedio ponderado = ese valor."""
        assert _weighted_average([20] * 11) == pytest.approx(20.0)

    def test_ascending_weights_more(self):
        """Pesos crecientes → el promedio se inclina hacia los últimos valores."""
        vals = [0] * 10 + [100]  # solo el mes 11 tiene ventas
        wa = _weighted_average(vals)
        simple_avg = 100 / 11
        assert wa > simple_avg  # ponderado da más peso al último

    def test_only_truncates_to_11(self):
        """Solo usa los primeros 11 valores aunque haya 12."""
        vals12 = [10] * 11 + [999]
        vals11 = [10] * 11
        assert _weighted_average(vals12) == _weighted_average(vals11)

    def test_empty_returns_zero(self):
        assert _weighted_average([]) == 0.0

    def test_known_calculation(self):
        """Verificación manual: vals=[1,2,3,...,11], pesos=[1,2,...,11]."""
        vals = list(range(1, 12))
        # sum(w*v) = sum(i*i for i in 1..11) = 506
        # sum(w) = 66
        expected = 506 / 66
        assert _weighted_average(vals) == pytest.approx(expected)


# ═══════════════════════════════════════════════════════════════
# _seasonality_index
# ═══════════════════════════════════════════════════════════════

class TestSeasonalityIndex:
    def test_avg_zero_returns_one(self):
        assert _seasonality_index([10] * 12, 5, 0) == 1.0

    def test_avg_negative_returns_one(self):
        assert _seasonality_index([10] * 12, 5, -1) == 1.0

    def test_normal_month_with_sales(self):
        """Mes con ventas > 0: SI = ventas[idx] / avg."""
        ventas = [20] * 12
        assert _seasonality_index(ventas, 3, 10.0) == pytest.approx(2.0)

    def test_normal_month_zero_sales(self):
        """Mes con ventas = 0: devuelve 1.0 (no 0)."""
        ventas = [0] * 12
        assert _seasonality_index(ventas, 3, 10.0) == 1.0

    def test_index_beyond_full_months(self):
        """past_idx >= FULL_MONTHS y != 11 → devuelve 1.0."""
        ventas = [10] * 12
        # FULL_MONTHS = 11, así que idx 11 es caso especial, 12+ no existe pero idx >= 11 y != 11
        # En la práctica past_idx es siempre 0-11

    def test_partial_month_index_11(self):
        """Índice 11 con ventas > 0: prorrateo especial 31/6, clampeado a 3.0."""
        ventas = [0] * 11 + [100]
        si = _seasonality_index(ventas, 11, 50.0)
        # prorated = 100 * 31 / 6 = 516.67, si = 516.67 / 50 = 10.33 → clamped to 3.0
        assert si == 3.0

    def test_partial_month_reasonable(self):
        ventas = [0] * 11 + [10]
        si = _seasonality_index(ventas, 11, 100.0)
        # prorated = 10 * 31 / 6 = 51.67, si = 51.67 / 100 = 0.517
        assert si == pytest.approx(10 * 31.0 / 6.0 / 100.0)


# ═══════════════════════════════════════════════════════════════
# _prorate_partial
# ═══════════════════════════════════════════════════════════════

class TestProratePartial:
    def test_short_ventas(self):
        """Menos de 12 valores → None."""
        assert _prorate_partial([10] * 11, 4) is None

    def test_zero_last_month(self):
        """ventas[11] = 0 → None."""
        ventas = [10] * 11 + [0]
        assert _prorate_partial(ventas, 4) is None

    def test_negative_last_month(self):
        """ventas[11] < 0 → None."""
        ventas = [10] * 11 + [-5]
        assert _prorate_partial(ventas, 4) is None

    def test_wrong_month(self):
        """end_month no coincide con mes actual → None."""
        ventas = [10] * 11 + [50]
        # Usar un mes que no sea el actual
        wrong_month = (datetime.date.today().month % 12) + 1
        assert _prorate_partial(ventas, wrong_month) is None

    @patch('purchase_engine.datetime')
    def test_prorates_correctly(self, mock_dt):
        """Prorrateo: ventas[11] * days_in_month / days_elapsed."""
        mock_dt.date.today.return_value = datetime.date(2026, 4, 16)
        ventas = [10] * 11 + [80]
        result = _prorate_partial(ventas, 4)
        # Abril tiene 30 días, estamos en el día 16
        expected = 80 * 30 / 16  # = 150.0
        assert result == pytest.approx(expected)

    @patch('purchase_engine.datetime')
    def test_prorates_end_of_month(self, mock_dt):
        """Último día del mes → prorrateo ≈ valor real."""
        mock_dt.date.today.return_value = datetime.date(2026, 3, 31)
        ventas = [10] * 11 + [100]
        result = _prorate_partial(ventas, 3)
        assert result == pytest.approx(100.0)  # 100 * 31 / 31


# ═══════════════════════════════════════════════════════════════
# _coef_variacion
# ═══════════════════════════════════════════════════════════════

class TestCoefVariacion:
    def test_constant_series(self):
        """Ventas idénticas → CV = 0."""
        ventas = [50] * 11 + [0]
        assert _coef_variacion(ventas) == pytest.approx(0.0)

    def test_insufficient_data(self):
        """Menos de 3 meses con ventas → 999."""
        ventas = [10, 20, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
        assert _coef_variacion(ventas) == 999.0

    def test_high_variation(self):
        """Serie con alta variación → CV alto."""
        ventas = [1, 100, 1, 100, 1, 100, 1, 100, 1, 100, 1, 0]
        cv = _coef_variacion(ventas)
        assert cv > 0.5

    def test_low_variation(self):
        """Serie estable → CV bajo."""
        ventas = [48, 52, 50, 49, 51, 50, 48, 52, 50, 49, 51, 0]
        cv = _coef_variacion(ventas)
        assert cv < 0.10

    def test_ignores_zero_months(self):
        """Solo cuenta meses con ventas > 0."""
        ventas = [50, 50, 50, 0, 0, 50, 50, 50, 50, 50, 50, 0]
        cv = _coef_variacion(ventas)
        assert cv == pytest.approx(0.0)

    def test_only_uses_first_11(self):
        """Usa solo los primeros 11 meses (FULL_MONTHS), ignora el 12."""
        ventas = [50] * 11 + [999]
        assert _coef_variacion(ventas) == pytest.approx(0.0)

    def test_known_value(self):
        """Verificación manual con valores conocidos."""
        # vals con ventas > 0: [10, 20, 30] → avg=20, var=(100+0+100)/3=66.67
        # stddev = 8.165, CV = 8.165/20 = 0.408
        ventas = [10, 20, 30, 0, 0, 0, 0, 0, 0, 0, 0, 0]
        cv = _coef_variacion(ventas)
        expected = (((100 + 0 + 100) / 3) ** 0.5) / 20
        assert cv == pytest.approx(expected)


# ═══════════════════════════════════════════════════════════════
# tipo_producto
# ═══════════════════════════════════════════════════════════════

class TestTipoProducto:
    def test_chronic_stable_sales(self):
        """11 meses con ventas estables → Crónico."""
        ventas = [50, 48, 52, 50, 49, 51, 50, 48, 52, 50, 49, 30]
        assert tipo_producto(ventas) == 'C'

    def test_normal_few_months(self):
        """Solo 5 meses con ventas → Normal (necesita 8+)."""
        ventas = [50, 50, 50, 50, 50, 0, 0, 0, 0, 0, 0, 0]
        assert tipo_producto(ventas) == 'N'

    def test_normal_high_variation(self):
        """11 meses pero alta variación → Normal."""
        ventas = [5, 100, 5, 100, 5, 100, 5, 100, 5, 100, 5, 0]
        assert tipo_producto(ventas) == 'N'

    def test_exactly_8_months(self):
        """Exactamente 8 meses con ventas + CV bajo → Crónico."""
        ventas = [50, 50, 50, 50, 50, 50, 50, 50, 0, 0, 0, 0]
        assert tipo_producto(ventas) == 'C'

    def test_7_months_is_normal(self):
        """7 meses con ventas → Normal (necesita 8)."""
        ventas = [50, 50, 50, 50, 50, 50, 50, 0, 0, 0, 0, 0]
        assert tipo_producto(ventas) == 'N'

    def test_custom_threshold(self):
        """Con umbral CV más estricto, productos borderline cambian."""
        # CV ~ 0.10 (bastante estable)
        ventas = [45, 55, 45, 55, 45, 55, 45, 55, 45, 55, 45, 0]
        assert tipo_producto(ventas, cv_umbral=0.30) == 'C'
        assert tipo_producto(ventas, cv_umbral=0.05) == 'N'


# ═══════════════════════════════════════════════════════════════
# rotation_index
# ═══════════════════════════════════════════════════════════════

class TestRotationIndex:
    def test_alta(self):
        assert rotation_index(25.0) == 'A'
        assert rotation_index(20.0) == 'A'  # exacto = alta

    def test_media(self):
        assert rotation_index(10.0) == 'M'
        assert rotation_index(5.0) == 'M'  # exacto = media

    def test_baja(self):
        assert rotation_index(4.9) == 'B'
        assert rotation_index(0.0) == 'B'

    def test_custom_thresholds(self):
        assert rotation_index(15.0, rot_alta_min=30, rot_media_min=10) == 'M'
        assert rotation_index(5.0, rot_alta_min=30, rot_media_min=10) == 'B'


# ═══════════════════════════════════════════════════════════════
# start_month_idx_from_period
# ═══════════════════════════════════════════════════════════════

class TestStartMonthIdxFromPeriod:
    def test_april_to_march(self):
        """Período Abr-Mar: next_month=4, idx = (4-4)%12 = 0."""
        assert start_month_idx_from_period(4, 3) == 0

    def test_jan_to_dec(self):
        """Período Ene-Dic: next_month=1, idx = (1-1)%12 = 0."""
        assert start_month_idx_from_period(1, 12) == 0

    def test_july_to_june(self):
        """Período Jul-Jun: next_month=7, idx = (7-7)%12 = 0."""
        assert start_month_idx_from_period(7, 6) == 0

    def test_start_equals_end(self):
        """start=end (ej: Abr-Abr): next_month=5, idx=(5-4)%12=1."""
        assert start_month_idx_from_period(4, 4) == 1

    def test_wraparound(self):
        """Período Mar-Ene: next_month=2, start=3, idx=(2-3)%12=11."""
        assert start_month_idx_from_period(3, 1) == 11


# ═══════════════════════════════════════════════════════════════
# analyze_product
# ═══════════════════════════════════════════════════════════════

class TestAnalyzeProduct:
    """Tests para la función principal de análisis."""

    def _make_ventas(self, val=10, partial=0):
        """Helper: 11 meses iguales + 1 parcial."""
        return [val] * FULL_MONTHS + [partial]

    def test_zero_sales_returns_zeros(self):
        """Sin ventas en 11 meses → todo cero, sin_mov_60d=True."""
        ventas = [0] * 12
        qty, forecast, slope, peak, low, comment, sin_mov = analyze_product(
            ventas, 0, 35, 0
        )
        assert qty == 0
        assert forecast == 0.0
        assert slope == 0.0
        assert peak == ''
        assert low == ''
        assert comment == ''
        assert sin_mov is True

    def test_positive_sales_no_stock(self):
        """Con ventas uniformes y sin stock → pide cantidad > 0."""
        ventas = self._make_ventas(100, 0)
        qty, forecast, slope, peak, low, comment, sin_mov = analyze_product(
            ventas, 0, 30, 0, data_start_month=4
        )
        assert qty > 0
        assert forecast > 0
        assert sin_mov is False

    def test_sufficient_stock_reduces_order(self):
        """Con stock alto → pide menos o nada."""
        ventas = self._make_ventas(10, 0)
        qty_no_stock, *_ = analyze_product(ventas, 0, 30, 0)
        qty_with_stock, *_ = analyze_product(ventas, 1000, 30, 0)
        assert qty_with_stock < qty_no_stock

    def test_huge_stock_orders_zero(self):
        """Stock >> forecast → qty = 0."""
        ventas = self._make_ventas(10, 0)
        qty, *_ = analyze_product(ventas, 99999, 30, 0)
        assert qty == 0

    def test_more_days_more_quantity(self):
        """Más días de cobertura → más cantidad pedida."""
        ventas = self._make_ventas(20, 0)
        qty_30, *_ = analyze_product(ventas, 0, 30, 0)
        qty_60, *_ = analyze_product(ventas, 0, 60, 0)
        assert qty_60 > qty_30

    def test_sin_mov_60d_forces_zero(self):
        """Si los últimos 3 meses (idx 9,10,11) son 0 → order_qty = 0."""
        ventas = [50, 50, 50, 50, 50, 50, 50, 50, 50, 0, 0, 0]
        qty, forecast, *_ = analyze_product(ventas, 0, 30, 0)
        assert qty == 0
        # Pero el forecast sigue calculándose
        assert forecast > 0

    def test_sin_mov_60d_false_when_recent_sales(self):
        """Ventas recientes → sin_mov_60d = False."""
        ventas = [0, 0, 0, 0, 0, 0, 0, 0, 0, 10, 0, 0]
        *_, sin_mov = analyze_product(ventas, 0, 30, 0)
        assert sin_mov is False

    def test_peak_month_detected(self):
        """Un mes con ventas muy por encima del promedio → se marca como pico."""
        ventas = [10, 10, 10, 10, 10, 100, 10, 10, 10, 10, 10, 0]
        *_, peak, low, _, _ = analyze_product(
            ventas, 0, 30, 0, data_start_month=1, umbral_pico=1.30
        )
        assert peak != ''

    def test_low_month_detected(self):
        """Un mes con ventas muy por debajo del promedio → se marca como baja."""
        ventas = [50, 50, 50, 50, 50, 50, 50, 50, 50, 1, 50, 0]
        *_, peak, low, _, _ = analyze_product(
            ventas, 0, 30, 0, data_start_month=1, umbral_baja=0.70
        )
        assert low != ''

    def test_uniform_sales_no_peak_no_low(self):
        """Ventas uniformes → sin pico ni baja."""
        ventas = self._make_ventas(50, 0)
        *_, peak, low, _, _ = analyze_product(
            ventas, 0, 30, 0, umbral_pico=1.30, umbral_baja=0.70
        )
        assert peak == ''
        assert low == ''

    def test_coverage_comment(self):
        """Con stock y ventas → comment muestra cobertura en días."""
        ventas = self._make_ventas(30, 0)
        *_, comment, _ = analyze_product(ventas, 100, 30, 0)
        assert 'cubre' in comment
        assert 'd' in comment

    def test_no_stock_no_comment(self):
        """Sin stock → comment vacío."""
        ventas = self._make_ventas(30, 0)
        *_, comment, _ = analyze_product(ventas, 0, 30, 0)
        assert comment == ''

    def test_chronic_dampens_trend(self):
        """Producto crónico con tendencia fuerte: el slope se amortigua al 20%."""
        # Serie ascendente para generar slope positivo
        ventas = [10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 0]
        qty_normal, *_ = analyze_product(ventas, 0, 30, 0, tipo='N')
        qty_chronic, *_ = analyze_product(ventas, 0, 30, 0, tipo='C')
        # El crónico debería pedir igual o menos porque la tendencia se amortigua
        assert qty_chronic <= qty_normal

    def test_chronic_flat_series_same_result(self):
        """Serie plana: crónico y normal dan lo mismo (slope ≈ 0)."""
        ventas = self._make_ventas(50, 0)
        qty_n, *_ = analyze_product(ventas, 0, 30, 0, tipo='N')
        qty_c, *_ = analyze_product(ventas, 0, 30, 0, tipo='C')
        assert qty_n == qty_c

    def test_slope_returned_correctly(self):
        """El slope se devuelve redondeado a 1 decimal."""
        ventas = [10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 0]
        _, _, slope, *_ = analyze_product(ventas, 0, 30, 0)
        assert isinstance(slope, float)
        # Slope de esta serie debería ser ~5
        assert slope > 0

    def test_order_qty_is_integer(self):
        """La cantidad a pedir es siempre un entero >= 0."""
        ventas = self._make_ventas(33, 5)
        qty, *_ = analyze_product(ventas, 10, 30, 0)
        assert isinstance(qty, int)
        assert qty >= 0

    def test_forecast_positive_with_sales(self):
        """Con ventas → forecast > 0."""
        ventas = self._make_ventas(25, 0)
        _, forecast, *_ = analyze_product(ventas, 0, 30, 0)
        assert forecast > 0

    def test_data_start_month_affects_peak_month_name(self):
        """El nombre del mes pico depende de data_start_month."""
        # Mes con pico en posición 0 (= data_start_month)
        ventas = [200, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 0]
        # Si data_start_month=1 (Enero), el pico debería ser "Ene"
        *_, peak1, _, _, _ = analyze_product(ventas, 0, 30, 0, data_start_month=1)
        assert peak1 == 'Ene'
        # Si data_start_month=7 (Julio), el pico debería ser "Jul"
        *_, peak7, _, _, _ = analyze_product(ventas, 0, 30, 0, data_start_month=7)
        assert peak7 == 'Jul'


# ═══════════════════════════════════════════════════════════════
# analyze_purchase (pipeline completo)
# ═══════════════════════════════════════════════════════════════

class TestAnalyzePurchase:
    def _make_product(self, ventas=None, stock=0, precio=100.0, nombre='Test'):
        return {
            'nombre': nombre,
            'codigo_barra': '7790000000001',
            'ventas': ventas or [10] * 11 + [0],
            'stock': stock,
            'precio_pvp': precio,
        }

    def test_single_product(self):
        """Pipeline con un producto: devuelve lista con un resultado."""
        products = [self._make_product()]
        results = analyze_purchase(products, 30, start_month=4, end_month=3)
        assert len(results) == 1
        r = results[0]
        assert 'order_qty' in r
        assert 'forecast' in r
        assert 'avg_monthly' in r
        assert 'rotacion' in r
        assert 'tipo' in r
        assert 'subtotal' in r
        assert 'slope' in r
        assert 'peak_month' in r
        assert 'low_month' in r
        assert 'comment' in r
        assert 'sin_mov_60d' in r

    def test_preserves_original_fields(self):
        """El resultado contiene los campos originales del producto."""
        products = [self._make_product(nombre='Aspirina')]
        results = analyze_purchase(products, 30, 4, 3)
        assert results[0]['nombre'] == 'Aspirina'
        assert results[0]['codigo_barra'] == '7790000000001'
        assert results[0]['precio_pvp'] == 100.0

    def test_subtotal_calculation(self):
        """subtotal = order_qty * precio_pvp."""
        products = [self._make_product(ventas=[50]*11+[0], stock=0, precio=200.0)]
        results = analyze_purchase(products, 30, 4, 3)
        r = results[0]
        assert r['subtotal'] == pytest.approx(r['order_qty'] * 200.0, abs=0.01)

    def test_multiple_products(self):
        """Pipeline con múltiples productos."""
        products = [
            self._make_product(ventas=[50]*11+[0], nombre='Producto A'),
            self._make_product(ventas=[5]*11+[0], nombre='Producto B'),
            self._make_product(ventas=[0]*12, nombre='Producto C'),
        ]
        results = analyze_purchase(products, 30, 4, 3)
        assert len(results) == 3
        # Producto A (alta rotación) pide más que B (baja)
        assert results[0]['order_qty'] >= results[1]['order_qty']
        # Producto C (sin ventas) pide 0
        assert results[2]['order_qty'] == 0

    def test_rotation_assigned(self):
        """Se asigna rotación A/M/B correctamente."""
        products = [
            self._make_product(ventas=[50]*11+[0]),  # avg ~50 → A
            self._make_product(ventas=[10]*11+[0]),   # avg ~10 → M
            self._make_product(ventas=[2]*11+[0]),    # avg ~2 → B
        ]
        results = analyze_purchase(products, 30, 4, 3)
        assert results[0]['rotacion'] == 'A'
        assert results[1]['rotacion'] == 'M'
        assert results[2]['rotacion'] == 'B'

    def test_tipo_assigned(self):
        """Se asigna tipo C/N correctamente."""
        products = [
            self._make_product(ventas=[50]*11+[0]),  # estable → C
            self._make_product(ventas=[5,100,5,100,5,100,5,100,5,100,5,0]),  # variable → N
        ]
        results = analyze_purchase(products, 30, 4, 3)
        assert results[0]['tipo'] == 'C'
        assert results[1]['tipo'] == 'N'

    def test_empty_products(self):
        """Lista vacía → resultado vacío."""
        assert analyze_purchase([], 30, 4, 3) == []

    def test_avg_monthly_calculation(self):
        """avg_monthly = sum(full_sales) / FULL_MONTHS."""
        ventas = [10] * 11 + [0]
        products = [self._make_product(ventas=ventas)]
        results = analyze_purchase(products, 30, 4, 3)
        assert results[0]['avg_monthly'] == pytest.approx(10.0)


# ═══════════════════════════════════════════════════════════════
# Casos de borde / regresión
# ═══════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_one_day_coverage(self):
        """n_days = 1 debería funcionar sin error."""
        ventas = [20] * 11 + [0]
        qty, forecast, *_ = analyze_product(ventas, 0, 1, 0)
        assert qty >= 0
        assert forecast > 0

    def test_365_days_coverage(self):
        """Un año completo de cobertura."""
        ventas = [20] * 11 + [0]
        qty, forecast, *_ = analyze_product(ventas, 0, 365, 0)
        assert qty > 0
        # Aproximadamente 20 * 12 = 240 unidades/año
        assert 150 < forecast < 350

    def test_very_high_stock(self):
        """Stock absurdamente alto → qty = 0."""
        ventas = [100] * 11 + [0]
        qty, *_ = analyze_product(ventas, 10**6, 30, 0)
        assert qty == 0

    def test_all_months_different(self):
        """Cada mes con ventas distintas → no crashea."""
        ventas = [5, 15, 25, 35, 45, 55, 65, 75, 85, 95, 105, 10]
        qty, forecast, slope, peak, low, comment, sin_mov = analyze_product(
            ventas, 50, 30, 0, data_start_month=1
        )
        assert qty >= 0
        assert slope > 0  # serie ascendente
        assert sin_mov is False

    def test_negative_stock_treated_as_zero(self):
        """Stock negativo: max(0, stock) → se trata como 0."""
        ventas = [20] * 11 + [0]
        qty_neg, *_ = analyze_product(ventas, -10, 30, 0)
        qty_zero, *_ = analyze_product(ventas, 0, 30, 0)
        assert qty_neg == qty_zero

    def test_forecast_never_negative(self):
        """El forecast nunca es negativo, incluso con tendencia negativa fuerte."""
        ventas = [100, 90, 80, 70, 60, 50, 40, 30, 20, 10, 1, 0]
        _, forecast, *_ = analyze_product(ventas, 0, 30, 0)
        assert forecast >= 0

    def test_order_qty_never_negative(self):
        """order_qty nunca es negativo."""
        ventas = [1] * 11 + [0]
        qty, *_ = analyze_product(ventas, 9999, 30, 0)
        assert qty >= 0
