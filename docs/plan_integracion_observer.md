# Plan: Integración con DB de ObServer (vistas SQL)

**Estado**: pendiente de ejecución. Esperando parámetros de conexión (DSN/credenciales) y el set final de vistas SQL que se van a exponer.

Retomar este archivo cuando lleguen esos datos — tiene el contexto completo para arrancar sin preguntar.

---

## Contexto

- ObServer = ERP local de la farmacia. Tiene las tablas "de verdad" (ventas, ingresos de mercadería, stock, estadísticos).
- El administrador de ObServer va a exponer **vistas SQL** de solo lectura sobre su DB para que AppFarmWeb consulte directo, sin tener que importar Excels.
- **La app corre el 85–90% del tiempo dentro de la misma red que ObServer**. El 10–15% restante es acceso remoto (Render / fuera de la LAN).
- Hoy la app ya tiene un cache local de lo importado: `product_analytics` (modelo `ProductAnalytics` en [database.py](../database.py)) con columnas como `ventas_json` (array de 12 valores mensuales), `avg_monthly`, `rotacion`, `slope`, `forecast_next`, `sin_mov_60d`, `start_month`, `n_days`. Se puebla hoy desde `parsers/sales_history*.py` más `_snapshot_product_analytics()` en [routes/purchase.py](../routes/purchase.py).

## Decisión de arquitectura

**Híbrido: cache local + consulta on-demand a las vistas.**

- **Cache local (`product_analytics`) para lecturas bulk**: dashboard, tabla de productos, análisis de compra (`purchase.py`), órdenes. Todo lo que hoy ya lee de `product_analytics` sigue igual. Evita pegarle N queries a ObServer al renderizar una pantalla con miles de productos y cubre el caso offline (10–15% del tiempo).
- **Consulta on-demand a las vistas para lecturas puntuales**: detalle de ventas de un EAN específico, stock actual fresco, drill-down desde un claim, etc. Latencia LAN → aceptable porque es baja.
- **Refresh del cache**: reemplaza el import manual de Excels. Un job o botón "Actualizar desde ObServer" que corre las vistas, agrega por EAN y upsertea `product_analytics` (mismo schema que hoy, así no toca `purchase.py` ni `dashboard.py` ni los templates).
- **Fallback transparente**: si la vista no responde, las rutas on-demand leen del cache. Si el cache está vacío, el refresh corre una vez al iniciar y puebla.

**Por qué no full on-demand**: pegarle a ObServer en cada render del dashboard/productos tira latencia (aunque sea LAN) y acopla la web al uptime del ERP. El cache ya existe y ya funciona — no vale romperlo.

**Por qué no solo cache**: desaprovecha que la app está en la LAN. Queries puntuales de drill-down conviene pedirlas frescas.

## Lo que cambia y lo que NO cambia

### NO cambia (sigue funcionando igual)
- `product_analytics` schema y uso actual en `purchase.py`, `dashboard.py`, `order_detail.html`.
- Templates, sidebar, rutas web existentes.
- Los parsers de sales_history quedan como fallback manual (para cuando no hay ObServer disponible, ej. ambiente de dev).

### Cambia
- Agregar `services/observer.py`: cliente de conexión a ObServer + funciones de lectura de vistas + función `refresh_analytics()`.
- Agregar `routes/observer.py` (o meter endpoints en `dashboard.py`): 
  - `POST /observer/refresh` → dispara refresh de `product_analytics`.
  - `GET /api/observer/ventas/<ean>` → on-demand, devuelve ventas detalladas.
  - `GET /api/observer/stock/<ean>` → stock actual fresco.
  - `GET /observer/status` → health-check (última sync, latencia, disponibilidad).
- Variables de entorno: `OBSERVER_DSN` (o host/db/user/pass). Se pasa por `docker-compose.yml` y por Render.
- UI: botón "Actualizar desde ObServer" en dashboard con timestamp de última sync + indicador verde/rojo de disponibilidad.

## Tareas concretas (cuando llegue la conexión)

1. **Recibir del administrador de ObServer**:
   - DSN / host / port / base / usuario / password (read-only).
   - Lista de vistas disponibles y su schema (columnas, tipos).
   - Ejemplos: `vw_ventas_mensuales`, `vw_stock_actual`, `vw_ingresos_mercaderia`, etc.
   - Confirmar driver (SQL Server → `pyodbc` o `pymssql`; PostgreSQL → psycopg; etc.).
2. **Crear `services/observer.py`**:
   - Función `get_engine()` que lee `OBSERVER_DSN` y cachea el engine.
   - Helper `query(sql, **params)` con manejo de errores y timeout.
   - Funciones tipadas por vista: `ventas_mensuales(ean, desde, hasta)`, `stock_actual(ean)`, etc.
   - Flag `is_available()` → intenta una query trivial, devuelve bool. Usar en fallbacks.
3. **Crear `services/sync_observer.py`** (o función dentro de `observer.py`):
   - `refresh_analytics()` → consulta `vw_ventas_mensuales` agregando por EAN, calcula `avg_monthly`, `slope`, `forecast_next`, etc. (reusar la lógica de `_snapshot_product_analytics()` de `purchase.py`), upsert en `product_analytics`.
   - Idempotente. Registra última ejecución en una fila de `Config` o tabla nueva `sync_log`.
4. **Crear `routes/observer.py`** con los endpoints de arriba y registrarlo en [routes/__init__.py](../routes/__init__.py).
5. **Integrar botón de refresh en `dashboard.html`** — POST a `/observer/refresh`, muestra spinner, al terminar refresca datos.
6. **Variables de entorno**:
   - Local/Docker: agregar a `docker-compose.yml`.
   - Render: agregar en secrets del dashboard.
   - Si corre remoto (10–15%), usar Tailscale (ya configurado según [docs/tailscale_setup.md](tailscale_setup.md) si existe) para llegar al host de ObServer.
7. **Tests**: mockear `services/observer.py` en tests. No conectar a ObServer real en CI.
8. **Deprecar imports manuales de sales_history**: dejarlos activos por ahora, eliminar cuando la sync con ObServer esté probada en prod.

## Riesgos y consideraciones

- **Schema-drift**: si las vistas cambian nombres/tipos, se rompe. Mitigación: encapsular toda lectura en `services/observer.py` (un solo archivo a tocar).
- **Permisos**: asegurar que el usuario de la DB sea read-only sobre las vistas. Pedirlo explícito.
- **Performance de las vistas**: vistas mal indexadas pueden ser lentas. Si `refresh_analytics()` tarda >30s, paginar o hacerlo async.
- **Concurrencia del refresh**: si dos usuarios le dan al botón a la vez, lockear con una bandera en `Config` o un archivo lock.
- **Render → ObServer**: cuando la app corre en Render necesita llegar a la IP/host de ObServer. Tailscale o VPN. Confirmar con el admin.

## Punto de partida para la próxima sesión

Al retomar:

1. Leer este archivo.
2. Leer [docs/estrategia_db_observer.md](estrategia_db_observer.md) (análisis inicial de opciones).
3. Pedir al usuario los parámetros de conexión y la lista de vistas.
4. Arrancar por el paso 2 (`services/observer.py`) y bajar.
