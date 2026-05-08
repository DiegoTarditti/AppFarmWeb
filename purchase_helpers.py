"""Helpers compartidos para cálculos de mínimos sugeridos y clasificación.

Centraliza el bloque repetido en compras_dia.py e informes.py:
  - tipo_producto + start_month_idx + analyze_product + ceil(fcst7) + avg_m
  - clasificación up/down/ok contra el mínimo actual
"""
import math

from purchase_engine import (
    FULL_MONTHS,
    _prorate_partial,
    analyze_product,
    start_month_idx_from_period,
    tipo_producto,
)


def calcular_min_sugerido(
    ventas_arr: list,
    stock_actual: int,
    start_month: int,
    end_month: int,
) -> tuple:
    """Calcula mínimo sugerido, promedio mensual, flag sin_mov y tipo de producto.

    Args:
        ventas_arr: lista de 12 ints con unidades por mes (idx 0 = más antiguo).
        stock_actual: stock actual del producto.
        start_month: mes de inicio del período (1-12).
        end_month: mes de fin del período (1-12).

    Returns:
        (min_sugerido, avg_m, sin_mov, tipo)
        - min_sugerido: int, ceil del forecast 7 días (0 si fcst7 <= 0).
        - avg_m: float, promedio mensual con prorrateo si aplica.
        - sin_mov: bool, True si sin movimiento en 60 días.
        - tipo: str, 'C' (crónico) o 'N' (normal).
    """
    tipo = tipo_producto(ventas_arr)
    sidx = start_month_idx_from_period(start_month, end_month)
    _qty, fcst7, _slope, _peak, _low, _comment, sin_mov = analyze_product(
        ventas_arr, stock_actual, n_days=7, start_month_idx=sidx,
        data_start_month=start_month, end_month=end_month, tipo=tipo,
    )
    min_sugerido = int(math.ceil(fcst7)) if fcst7 > 0 else 0

    _pp = _prorate_partial(ventas_arr, end_month)
    if _pp is not None:
        avg_m = (sum(ventas_arr[:FULL_MONTHS]) + _pp) / (FULL_MONTHS + 1)
    else:
        avg_m = sum(ventas_arr[:FULL_MONTHS]) / FULL_MONTHS if FULL_MONTHS else 0.0

    return min_sugerido, avg_m, sin_mov, tipo


def clasificar_min(min_actual: int, min_sugerido: int) -> str:
    """Clasifica el mínimo actual contra el sugerido.

    Args:
        min_actual: mínimo configurado actualmente.
        min_sugerido: mínimo sugerido por el forecast.

    Returns:
        'up'   — mínimo actual es 0 o está más de 40 % por debajo del sugerido.
        'down' — mínimo actual supera más del doble del sugerido.
        'ok'   — dentro de rango razonable.

    Precondición: llamar solo cuando hay ventas y sin_mov es False.
    """
    if min_actual == 0 or min_actual < min_sugerido * 0.6:
        return 'up'
    if min_sugerido > 0 and min_actual > min_sugerido * 2.0:
        return 'down'
    return 'ok'
