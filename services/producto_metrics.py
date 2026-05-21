"""Source of truth UNICO de las metricas de venta/stock de un producto.

Antes habia dos endpoints (`/api/product/<ean>/chart` en docs_pendientes.py y
`/api/observer-product/<id>/chart` en informes.py) que calculaban stock, minimo,
promedio mensual, rotacion y tendencia de forma DISTINTA. Resultado: el mismo
producto mostraba numeros diferentes segun la pantalla.

Este modulo centraliza el calculo. Reglas (decididas 2026-05-20):

- **Una sola farmacia operativa** (`farmacia_operativa()`, env OBSERVER_ID_FARMACIA,
  default 10525). NUNCA se suma entre farmacias (multi-tenant deshabilitado por
  ahora). El parametro `id_farmacia` queda disponible para reactivar multi-farmacia
  en el futuro sin reescribir.
- **Calculo en vivo** desde obs_stock + obs_ventas_mensuales. No se usa
  ProductAnalytics (queda stale ~1 mes).
- **Dos promedios**: `avg_3m` (reposicion tactica) y `avg_12m` (planificacion).
  Cada pantalla elige cual mostrar.
- **Rotacion** via `purchase_engine.rotation_index` (A>=20, M>=5, B resto) sobre
  `avg_12m`. Una sola definicion.
"""
import os

from sqlalchemy import func

from database import ObsStock, ObsVentaMensual
from purchase_engine import rotation_index


def farmacia_operativa():
    """ID de la unica farmacia operativa. Centraliza el env var que estaba
    repetido inline en ~10 rutas."""
    return int(os.environ.get('OBSERVER_ID_FARMACIA', '10525'))


def _ventas_12m(session, observer_id, id_farmacia, hoy):
    """Array de 12 floats: [0]=mes mas antiguo ... [11]=mes actual (parcial).
    Filtrado por la farmacia operativa."""
    meses = []
    y, m = hoy.year, hoy.month
    for _ in range(12):
        meses.append((y, m))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    meses.reverse()
    rows = (session.query(ObsVentaMensual.anio,
                          ObsVentaMensual.mes,
                          ObsVentaMensual.unidades)
            .filter(ObsVentaMensual.id_farmacia == id_farmacia,
                    ObsVentaMensual.producto_observer == observer_id)
            .all())
    ventas_map = {(int(r[0]), int(r[1])): float(r[2] or 0) for r in rows}
    return [round(ventas_map.get((yy, mm), 0.0), 2) for (yy, mm) in meses], meses


def _slope(ventas):
    """Pendiente de regresion lineal simple sobre la serie (tendencia)."""
    n = len(ventas)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(ventas) / n
    num = sum((xs[i] - mean_x) * (ventas[i] - mean_y) for i in range(n))
    den = sum((x - mean_x) ** 2 for x in xs)
    return num / den if den else 0.0


def metricas_producto(session, observer_id, id_farmacia=None, hoy=None):
    """Metricas canonicas de un producto ObServer. UNA sola cuenta para toda la app.

    Args:
        session: SQLAlchemy session abierta.
        observer_id: IdProducto de ObServer.
        id_farmacia: override de farmacia (default: la operativa). Multi-tenant
            queda preparado pero por ahora siempre se usa la operativa.
        hoy: date para testear (default: date.today()).

    Returns dict:
        ventas12: list[float]   # 12 meses, [0]=mas antiguo, [11]=mes actual parcial
        stock: int              # 1 farmacia
        minimo: int             # 1 farmacia
        maximo: int|None        # 1 farmacia
        avg_3m: float           # sum(ultimos 3 meses completos) / 3
        avg_12m: float          # sum(11 meses completos, excluye actual) / 11
        avg_monthly: float      # alias de avg_12m (backcompat con consumidores viejos)
        rotacion: str           # 'A'|'M'|'B' via rotation_index(avg_12m)
        slope: float            # tendencia (regresion lineal sobre los 12)
        sin_historial: bool     # True si no hubo ninguna venta en 12m
        start_month: int        # mes (1-12) del primer slot del array
    """
    from datetime import date as _date
    if hoy is None:
        hoy = _date.today()
    if id_farmacia is None:
        id_farmacia = farmacia_operativa()

    ventas12, meses = _ventas_12m(session, observer_id, id_farmacia, hoy)

    # Stock + minimo + maximo de la farmacia operativa (sin sumar entre farmacias).
    stock_row = (session.query(ObsStock.stock_actual,
                               ObsStock.minimo,
                               ObsStock.maximo)
                 .filter(ObsStock.id_farmacia == id_farmacia,
                         ObsStock.producto_observer == observer_id)
                 .first())
    stock = int(stock_row[0]) if stock_row and stock_row[0] is not None else 0
    minimo = int(stock_row[1]) if stock_row and stock_row[1] is not None else 0
    maximo = int(stock_row[2]) if stock_row and stock_row[2] is not None else None

    # Promedios. [11] es el mes actual parcial → se excluye de ambos.
    completos = ventas12[:11]                      # 11 meses cerrados
    avg_12m = sum(completos) / 11 if completos else 0.0
    ultimos_3 = ventas12[8:11]                     # meses -3, -2, -1 (cerrados)
    avg_3m = sum(ultimos_3) / 3 if ultimos_3 else 0.0

    return {
        'ventas12': ventas12,
        'stock': stock,
        'minimo': minimo,
        'maximo': maximo,
        'avg_3m': round(avg_3m, 2),
        'avg_12m': round(avg_12m, 2),
        'avg_monthly': round(avg_12m, 2),
        'rotacion': rotation_index(avg_12m),
        'slope': round(_slope(ventas12), 4),
        'sin_historial': sum(ventas12) == 0,
        'start_month': meses[0][1],
    }


def cobertura_dias(stock, avg_mensual, divisor=30.4):
    """Dias de cobertura = stock / (avg_mensual / divisor). Helper unico para
    estandarizar el divisor: 30.4 = 365.25/12 (mismo que usa el front en
    _grafico_dual_panel / _grafico_historico / consulta_producto).

    Devuelve None si no hay demanda (avg<=0) — el consumidor decide como mostrarlo.
    """
    if not avg_mensual or avg_mensual <= 0:
        return None
    diario = avg_mensual / divisor
    if diario <= 0:
        return None
    return stock / diario
