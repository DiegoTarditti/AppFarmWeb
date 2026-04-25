# Roadmap de crecimiento — análisis 2026-04-25

Visión de cómo va a escalar AppFarmWeb con más datos y usuarios. Material para retomar y refinar.

## Datos que crecen y dónde va a doler

| Tabla | Hoy | A 2 años | Riesgo |
|---|---|---|---|
| `obs_ventas_mensuales` | 80k | ~150-200k | Bajo, pero el JOIN con `obs_productos` en `/estadisticas/drogas` se va a poner lento |
| `pedidos` + `pedido_items` | <1k | 50k+ | OK con índices |
| `facturas` + `factura_items` | bajo | 10-15k/año | OK |
| `home_card_clicks` | crece infinito si nadie limpia | Limpiar cada N meses |
| PDFs de facturas/reclamos | acumulan | Considerar S3/R2 a largo plazo |
| `obs_clientes`/`obs_obras_sociales` | estáticos | Sin problema |

## Performance — queries que se van a poner lentas

1. **`/estadisticas/drogas`** — agregado pesado. Solución cuando duela: **vista materializada** refrescada cada noche.
2. **`/obs/productos` búsqueda** — `ilike` sobre 122k rows hoy va bien. Cuando crezca: **trigram index (`pg_trgm`)** sobre `descripcion`.
3. **`/api/pedido/<id>/indicadores`** — muchos queries pequeños. Para pedidos > 500 items habrá que bulkear más.
4. **Bridge `productos.observer_id`** — los lookups por `codigo_barra` ya tienen índice, OK.

## Features que van a aparecer naturalmente

1. **Alertas proactivas** — "TAFIROL: 5 días de stock" en home, no esperar que el user entre a Indicadores.
2. **Forecast simple** — media móvil + tendencia para sugerir cantidad de compra automáticamente.
3. **Comparación temporal** — "tu pedido Roemmers de junio vs el de marzo, qué cambió".
4. **Sistema de reglas** — "si stock < 10 días Y momentum > 20% → alerta". Configurable por user.
5. **Cruce ventas vs OS** — cuando ObServer exponga `IdPlan` en `DW.ProductosVendidos` (pendiente que averigüe Lisandro).
6. **Mobile/PWA** — el responsive ya empezó, fluye solo.

## Operación y riesgos críticos

### 1. Sync silencioso fallido (riesgo más grande)
Si ObServer no syncea por 2 días y nadie nota, las decisiones de compra usan datos viejos.
**Acción urgente**: alerta automática (mail/slack/notif en home) cuando el último `obs_sync_log` tiene > N horas.

### 2. Falta de tests
Cualquier cambio puede romper un flujo crítico (cruce de factura, generación de reclamo). Vas a llegar a un punto donde no te animes a tocar nada.
**Cuando el equipo crezca**: `pytest` con tests de integración de los flujos de oro.

### 3. Documentación viva
El manual que armamos es bueno, pero si no se actualiza con cada PR pierde valor en 6 meses.
**Regla en CLAUDE.md**: "si tocás UI, actualizá el `.md` en el mismo commit".

### 4. Migraciones
Hoy son `ALTER TABLE IF NOT EXISTS` inline. Funciona hasta ~30 tablas. Después: **Alembic**.

## Hacia dónde se transforma

A medida que el sistema mature, deja de ser "control de compra" y se convierte en **sistema de inteligencia comercial**:

- **Hoy**: "qué comprar y cuánto".
- **6 meses**: "qué comprar, cuánto, cuándo, a quién, y qué precio conviene".
- **1 año+**: "esto es lo que vas a vender el mes que viene, este es tu margen estimado, esta es tu rotación por categoría".

El cuello de botella **no va a ser técnico** (PostgreSQL aguanta), va a ser **de feedback loops**: cuántos farmacéuticos usan la app y reportan bugs/ideas.

Si llegás a 5-10 farmacias, vas a necesitar:
- Multi-tenant (separación lógica de datos por farmacia).
- Roles más granulares.
- Pricing model.
- Onboarding self-service.

## Tres recomendaciones concretas para los próximos 3 meses

### 1. Alerta de sync fallido — TODO
Mail/notif en home si el último sync de `ventas_mensuales` tiene > 24h. **30 minutos de trabajo, salva el sistema.**

### 2. Vista materializada para estadísticas por droga — TODO
La query agregada por monodroga es la que más rápido se va a degradar. Refresh nocturno con `REFRESH MATERIALIZED VIEW CONCURRENTLY`.

### 3. CI mínimo — TODO
Workflow de GitHub Actions que corra:
- `python -m py_compile` sobre todos los archivos.
- `pytest` (aunque empieces con 5 tests cubriendo los flujos de oro).

Te avisa si rompiste algo antes de pushear.

## Conclusión

El proyecto tiene buena base. Lo que va a definir si escala bien:
- **Qué tan rápido detectás regresiones** → CI + tests.
- **Qué tan al día está la documentación** → regla en CLAUDE.md.
- **Qué tan rápido detectás syncs fallidos** → alerta proactiva.

El stack (Flask + PostgreSQL + Docker + Render) es el correcto para esta escala y aguanta lo que viene en los próximos 2-3 años.
