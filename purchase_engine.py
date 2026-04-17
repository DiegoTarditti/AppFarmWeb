"""
Motor de análisis de compras.
Aplica promedio ponderado + estacionalidad + tendencia lineal
para pronosticar el pedido óptimo por producto.
Unidad de pedido: días (en lugar de meses).
"""
import math
import datetime
import calendar

# Los 11 meses completos (el 12° es el mes parcial en el informe)
FULL_MONTHS = 11

# Días por mes calendario (índice 0 = Enero)
_DAYS_JAN = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
# Nombres de meses en español (índice 0 = Enero)
_MONTH_ES_JAN = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']

# Promedio de días por mes (365/12)
AVG_DAYS_PER_MONTH = sum(_DAYS_JAN) / 12  # 30.42

# Backward-compat: Apr-based (índice 0 = Abr), para código que los importe directamente
MONTH_ES = [_MONTH_ES_JAN[(3 + i) % 12] for i in range(12)]
MONTH_DAYS = [_DAYS_JAN[(3 + i) % 12] for i in range(12)]


def _prorate_partial(ventas, end_month):
    """
    Si el mes 12 (índice 11) coincide con el mes actual, devuelve su valor
    prorateado a mes completo. Si no o si ventas[11]==0, devuelve None.
    """
    if len(ventas) < 12 or ventas[11] <= 0:
        return None
    today = datetime.date.today()
    if today.month != end_month:
        return None
    days_elapsed = today.day
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    if days_elapsed <= 0:
        return None
    return ventas[11] * days_in_month / days_elapsed


def _linear_trend(vals):
    """Regresión lineal sobre la lista vals (longitud variable)."""
    n = len(vals)
    if n < 2:
        return 0.0
    mean_x = (n - 1) / 2.0
    mean_y = sum(vals) / n
    numer = sum((i - mean_x) * (vals[i] - mean_y) for i in range(n))
    denom = sum((i - mean_x) ** 2 for i in range(n))
    return numer / denom if denom else 0.0


def _weighted_average(values):
    vals = values[:FULL_MONTHS]
    weights = list(range(1, FULL_MONTHS + 1))
    denom = sum(weights)
    return sum(w * v for w, v in zip(weights, vals)) / denom if denom else 0.0


def _seasonality_index(ventas, past_idx, avg):
    if avg <= 0:
        return 1.0
    if past_idx < FULL_MONTHS:
        return ventas[past_idx] / avg if ventas[past_idx] > 0 else 1.0
    if past_idx == 11 and ventas[11] > 0:
        prorated = ventas[11] * 31.0 / 6.0
        return min(prorated / avg, 3.0)
    return 1.0


def analyze_product(ventas, stock, n_days, start_month_idx, data_start_month=4,
                    umbral_pico=1.30, umbral_baja=0.70, end_month=None,
                    tipo=None):
    """
    data_start_month: número de mes (1-12) correspondiente a ventas[0].
    end_month: mes de cierre del período (para prorratear el mes parcial).
    """
    full_sales = ventas[:FULL_MONTHS]
    total_sold = sum(full_sales)

    # Sin movimiento en los últimos ~60 días (índices 9, 10 = últimos 2 meses completos + 11 = parcial)
    sin_mov_60d = sum(ventas[9:]) == 0

    if total_sold == 0:
        return 0, 0.0, 0.0, '', '', '', True

    # Prorratear mes parcial e incluirlo en tendencia y promedio si corresponde
    prorated = _prorate_partial(ventas, end_month) if end_month else None
    if prorated is not None:
        trend_vals = list(full_sales) + [prorated]
        n_months = FULL_MONTHS + 1
    else:
        trend_vals = list(full_sales)
        n_months = FULL_MONTHS

    avg   = sum(trend_vals) / n_months
    slope = _linear_trend(trend_vals)
    # Crónicos: amortiguar tendencia (usar solo 20% del slope)
    effective_slope = slope * 0.2 if tipo == 'C' else slope
    hist_center = (n_months - 1) / 2.0

    # Base 0-indexada (0=Ene) para el primer mes de datos
    _base = data_start_month - 1

    forecast_total = 0.0
    remaining = n_days
    month_offset = 0

    while remaining > 0:
        month_idx = (start_month_idx + month_offset) % 12
        cal_idx = (_base + month_idx) % 12
        days_in_month = _DAYS_JAN[cal_idx]
        days_here = min(remaining, days_in_month)

        si = _seasonality_index(ventas, month_idx, avg)

        future_pos = n_months + month_offset
        trend_horizon = min(future_pos - hist_center, 2.0)
        trend_adj = effective_slope * trend_horizon
        monthly_fcst = max(0.0, avg * si + trend_adj)
        forecast_total += monthly_fcst / days_in_month * days_here

        remaining -= days_here
        month_offset += 1

    order_qty = max(0, math.ceil(forecast_total - max(0, stock)))

    # Sin movimiento en últimos 60 días → proponer 0
    if sin_mov_60d:
        order_qty = 0

    # Pico/Baja sobre meses históricos completos
    peak_month = ''
    low_month = ''
    if avg > 0:
        hist_si = []
        for i in range(FULL_MONTHS):
            cal_idx = (_base + i) % 12
            si = ventas[i] / avg
            hist_si.append((cal_idx, si))
        max_cal, max_si = max(hist_si, key=lambda x: x[1])
        min_cal, min_si = min(hist_si, key=lambda x: x[1])
        if max_si >= umbral_pico:
            peak_month = _MONTH_ES_JAN[max_cal]
        if min_si <= umbral_baja:
            low_month = _MONTH_ES_JAN[min_cal]

    daily_avg = avg / AVG_DAYS_PER_MONTH
    if stock > 0 and daily_avg > 0:
        cov_days = round(stock / daily_avg)
        comment = f"cubre {cov_days}d"
    else:
        comment = ''

    return order_qty, round(forecast_total, 1), round(slope, 1), peak_month, low_month, comment, sin_mov_60d


def start_month_idx_from_period(start_month, end_month):
    next_month = (end_month % 12) + 1
    return (next_month - start_month) % 12


def rotation_index(avg_monthly, rot_alta_min=20.0, rot_media_min=5.0):
    """Devuelve 'A', 'M' o 'B' según el promedio mensual de ventas."""
    if avg_monthly >= rot_alta_min:
        return 'A'
    if avg_monthly >= rot_media_min:
        return 'M'
    return 'B'


def _coef_variacion(ventas):
    """Coeficiente de variación sobre meses completos con ventas > 0."""
    vals = [v for v in ventas[:FULL_MONTHS] if v > 0]
    if len(vals) < 3:
        return 999.0  # insuficientes datos
    avg = sum(vals) / len(vals)
    if avg <= 0:
        return 999.0
    variance = sum((v - avg) ** 2 for v in vals) / len(vals)
    return (variance ** 0.5) / avg


def tipo_producto(ventas, cv_umbral=0.30):
    """Devuelve 'C' (crónico) o 'N' (normal) basado en coeficiente de variación.
    Crónico: CV < cv_umbral y al menos 8 de 11 meses con ventas."""
    meses_con_venta = sum(1 for v in ventas[:FULL_MONTHS] if v > 0)
    if meses_con_venta < 8:
        return 'N'
    cv = _coef_variacion(ventas)
    return 'C' if cv < cv_umbral else 'N'


def analyze_purchase(products, n_days, start_month, end_month,
                     umbral_pico=1.30, umbral_baja=0.70, umbral_tendencia=0.20,
                     rot_alta_min=20.0, rot_media_min=5.0):
    sidx = start_month_idx_from_period(start_month, end_month)
    results = []
    for p in products:
        tipo = tipo_producto(p['ventas'])
        qty, forecast, slope, peak_month, low_month, comment, sin_mov_60d = analyze_product(
            p['ventas'], p['stock'], n_days, sidx,
            data_start_month=start_month,
            umbral_pico=umbral_pico, umbral_baja=umbral_baja,
            end_month=end_month,
            tipo=tipo,
        )
        # avg_monthly con prorated si aplica
        prorated = _prorate_partial(p['ventas'], end_month)
        if prorated is not None:
            avg_m = (sum(p['ventas'][:FULL_MONTHS]) + prorated) / (FULL_MONTHS + 1)
        else:
            avg_m = sum(p['ventas'][:FULL_MONTHS]) / FULL_MONTHS
        results.append({
            **p,
            'avg_monthly': round(avg_m, 1),
            'rotacion': rotation_index(avg_m, rot_alta_min, rot_media_min),
            'tipo': tipo,
            'forecast': forecast,
            'order_qty': qty,
            'subtotal': round(qty * p['precio_pvp'], 2),
            'slope': slope,
            'peak_month': peak_month,
            'low_month': low_month,
            'comment': comment,
            'sin_mov_60d': sin_mov_60d,
        })
    return results
