# Reconciliación de ventas contra ObServer — lecciones

> Escrito el 2026-07-20 al armar el informe **Ventas — comparación anual**
> (`/informes/ventas-comparativa`) y validarlo contra el reporte real de
> ObServer *"Analítico de ventas por día"* (Farmacia BADIA, junio 2026).
> Fuente local: `obs_ventas_detalle` (espejo de `DW.ProductosVendidos`).

Si volvés a tocar cualquier informe que cuente ventas/tickets/importe, leé
esto **primero**. Hay dos trampas que ya nos costaron un susto.

---

## ⚠ Trampa 1 — El sync de ventas congelaba días (watermark incremental)

**Síntoma:** junio 2026 mostraba $406M cuando ObServer decía $638M (64%).
Varios días enteros aparecían al 3-5% de su valor real (día 4: $1M vs $20,7M).

**Causa:** `sync_ventas_detalle()` arrancaba en `MAX(fecha_estadistica) + 1 día`.
Pero ObServer **backfillea `FechaEstadistica` con retraso**: registros de un día
llegan *después* de que el sync ya avanzó el watermark más allá de esa fecha →
esos días quedan congelados incompletos para siempre.

**Fix (ya aplicado):** el watermark ahora re-sincroniza una **ventana de solape**
de 20 días hacia atrás (`resync_window_dias=20` en `observer_source.py`). El
upsert `ON CONFLICT` lo hace idempotente. Los días backfilleados se completan
solos en la próxima corrida. **No revertir a `MAX+1`.**

**Detectar días congelados** (0 filas = sano):
```sql
WITH dia_imp AS (
  SELECT anio, mes, dia, sum(case when tipo_operacion='V' then importe else 0 end) AS imp
  FROM obs_ventas_detalle WHERE anio IN (2025,2026) GROUP BY anio,mes,dia),
med AS (SELECT anio, mes, percentile_cont(0.5) WITHIN GROUP (ORDER BY imp) AS mediana
        FROM dia_imp GROUP BY anio,mes)
SELECT d.anio, d.mes, count(*) FILTER (WHERE d.imp < m.mediana*0.3) AS dias_congelados
FROM dia_imp d JOIN med m USING(anio,mes)
GROUP BY d.anio,d.mes HAVING count(*) FILTER (WHERE d.imp < m.mediana*0.3) > 0;
```

**Backfill de un período** (idempotente):
```python
import observer_source as obs, database
with database.get_db() as s:
    obs.sync_ventas_detalle(s, desde_fecha=date(2026,5,1))   # re-trae desde esa fecha
```

---

## ⚠ Trampa 2 — El "Cant. Oper." de ObServer NO son operaciones, son renglones

El reporte *Analítico de ventas por día* tiene una columna **"Cant. Oper."**.
El label engaña: **no cuenta operaciones/ventas, cuenta líneas de producto**
(renglones). Comprobado en junio 2026:

| Métrica | Valor | Definición |
|---|---|---|
| `COUNT(DISTINCT IdOperacion)` where V | **20.036** | Ventas/tickets reales (transacciones) |
| `COUNT(*)` where V (renglones) | **34.803** | Líneas vendidas |
| `COUNT(*)` where V+D | **35.385** | ≈ el "Cant. Oper." del PDF (**35.367**, −18 por bordes) |

O sea: **"Cant. Oper." ≈ renglones V+D**, no operaciones distintas. Una venta
real agrupa ~1,73 renglones. Ningún `DISTINCT` (IdOperacion, IdGrupoOperaciones,
Comprobante, EsVentaParticular) da 35k — solo el conteo de filas.

**En el informe** exponemos ambas, etiquetadas claro:
- **Transacciones** = `distinct IdOperacion` (la métrica honesta de "ventas").
- **Ítems vendidos** = renglones (= "Cant. Oper." de ObServer, para que cuadre
  con el PDF que conoce la farmacia).

---

## Importe — bruto vs neto

- `obs_ventas_detalle.importe` es **bruto de descuento** pero se puede netear de
  **devoluciones**: las filas `D` tienen `importe` y `cantidad` **negativos**, así
  que `SUM(importe)` sobre V+D descuenta la devolución solo.
- El **"Total Ventas"** del PDF de ObServer es **neto de descuentos**
  (Ventas − Descuentos). Por eso nuestro importe da ~5,8% más alto: el PDF marca
  `Inciden. Descto. −5,79%`, y `bruto × (1 − 0,0579) ≈ neto` cierra exacto.
- Para una comparación **año contra año interna** da igual (es consistente).
  Si algún día hay que **cuadrar al peso con ObServer**, restar los descuentos.

---

## Reconciliación final — junio 2026

| Métrica | Local (post-fix) | ObServer (PDF) | Estado |
|---|---|---|---|
| Ítems vendidos (renglones) | 34.803 | 35.367 | ✅ (−18, bordes) |
| Importe | $673,7M bruto → ~$634,7M neto | $638,5M | ✅ (dif. descuento) |
| Transacciones reales | 20.036 | (ObServer no lo expone) | — |

---

## Ojo al comparar YoY con mes en curso

El informe compara **solo meses cerrados** (excluye el mes en curso del acumulado
YTD). Un mes parcial contra el mismo mes ya cerrado del año anterior te da una
caída falsa. El mes en curso se muestra igual en el gráfico, marcado con `*`.

*Ver también: `services/ventas_comparativa.py`, `routes/informes.py`
(`informe_ventas_comparativa`), `observer_source.py` (`sync_ventas_detalle`).*
