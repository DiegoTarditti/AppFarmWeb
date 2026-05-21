# Plan — Unificar métricas de producto (stock/mín/prom/rotación/cobertura)

_Creado: 2026-05-20. Estado: PROPUESTA, sin implementar._

## Problema

El mismo producto muestra valores distintos según la pantalla porque hay
**dos endpoints paralelos** que calculan lo mismo de forma diferente:

| Métrica | `/api/product/<ean>/chart` (docs_pendientes.py:150) | `/api/observer-product/<id>/chart` (informes.py:1474) |
|---|---|---|
| stock | ObsStock filtrado por `id_farmacia` (1 farmacia) | `SUM(ObsStock)` todas las farmacias |
| mínimo | ObsStock 1 farmacia | `SUM` todas las farmacias |
| avg_monthly | `PA.avg_monthly` (precalc, stale) o `sum(11m)/11` | `sum(meses>0)/cant_meses>0` |
| rotación | '' en fallback observer | A/M/B inline (≥20/≥5) |
| slope | 0 en fallback | regresión lineal |

Visible en `/consulta-producto/<ean>`: las KPI cards (arriba) usan el primero
y el panel de gráficos (abajo) usa el segundo → STOCK 3 vs 1, prom 15 vs 13, etc.

## Decisiones tomadas (usuario, 2026-05-20)

1. **Stock/mínimo = una sola farmacia operativa** (`id_farmacia` del env
   `OBSERVER_ID_FARMACIA`, default 10525). El `SUM(todas)` es el bug.
2. **Promedio: exponer u3m Y u12m**. Reposición táctica usa 3m, planificación
   usa 12m. No colapsar a uno.
3. **Unificar con cuidado**: source of truth único + implementación incremental
   verificando pantalla por pantalla.

## Diseño

### Nuevo módulo `services/producto_metrics.py` (source of truth ÚNICO)

```python
def metricas_producto(session, observer_id, id_farmacia=None):
    """Métricas canónicas de un producto ObServer. UNA sola cuenta para toda la app.

    Returns dict:
      ventas12: list[float]   # 12 meses, [0]=más antiguo, [11]=mes actual parcial
      stock: int              # 1 farmacia
      minimo: int             # 1 farmacia
      avg_3m: float           # sum(últimos 3 meses completos) / 3
      avg_12m: float          # sum(11 meses completos, excluye actual) / 11
      rotacion: str           # rotation_index(avg_12m) reusando purchase_engine
      slope: float            # regresión lineal sobre los 12
      sin_historial: bool
    """
```

- `id_farmacia` default desde `OBSERVER_ID_FARMACIA`.
- stock/minimo/ventas SIEMPRE filtrados por esa farmacia.
- rotación vía `purchase_engine.rotation_index(avg_12m)` (no más inline).
- cobertura NO se calcula acá (depende de qué avg use cada pantalla); se deja
  al consumidor con un helper único de divisor.

### Endpoints (delegan al módulo)

- `/api/observer-product/<id>/chart`: cambia `SUM(todas)` → `metricas_producto`
  (1 farmacia). Agrega `avg_3m` y `avg_12m` a la respuesta. `avg_monthly` queda
  como alias de `avg_12m` para backcompat.
- `/api/product/<ean>/chart`: resuelve barcode→observer_id y delega a
  `metricas_producto`. **Decisión pendiente**: ¿seguir usando ProductAnalytics
  (PA) como fuente, o ignorarlo y calcular siempre en vivo? PA puede estar stale
  → recomiendo calcular en vivo siempre y usar PA solo para `descripcion`.
  (Confirmar — cambia comportamiento de pantallas que hoy muestran PA.)

### Frontend

- `_grafico_historico.html` y `_grafico_dual_panel.html`: leer `avg_3m`/`avg_12m`
  y mostrar el que corresponda al contexto (definir por pantalla).
- Cobertura: estandarizar divisor — hoy unos usan `/30` y otros `/30.4`.
  Unificar a un helper `cobertura_dias(stock, avg, divisor=30)`.
- `consulta_producto_resultado.html`: las cards y el panel dual deben terminar
  consumiendo la MISMA fuente → desaparece la divergencia visible.

## Pasos (incremental, verificable)

1. Crear `services/producto_metrics.py` + tests (`tests/test_producto_metrics.py`).
2. Migrar `/api/observer-product/<id>/chart` a usar el módulo (1 farmacia + avg_3m/12m).
3. Migrar `/api/product/<ean>/chart` a usar el módulo.
4. Ajustar front (avg correcto por pantalla + cobertura unificada).
5. Verificar en `/consulta-producto/<ean>`: cards == panel dual.
6. Repasar las otras pantallas de la tabla de inventario (dashboard, orders,
   compras_dia_armar, repo-alertas) — confirmar que siguen consistentes.

## Riesgos

- Cambiar PA→live puede mover números que el usuario ya conocía (pero los
  "correctos" son los live de 1 farmacia).
- `id_farmacia` hardcodeado por env: si algún día hay multi-farmacia real,
  el módulo ya recibe el parámetro para extender.
