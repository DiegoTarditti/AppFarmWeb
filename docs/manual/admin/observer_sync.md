# Admin: Sync con ObServer

Mantener actualizadas las tablas locales `obs_*` (productos, stock, ventas mensuales, clientes, OS, etc.) que son el espejo de la DB real de ObServer (SQL Server 2014). Sin esto, la app web no tiene datos de la farmacia.

**Acceso**: `/admin/observer-sync`. Card "Sincronización con ObServer" en el admin.

## Arquitectura del sync

```
ObServer (SQL Server, en farmacia)
  ↓ pymssql
Sync local (corre en farmacia o en otra máquina con acceso TCP a SQL Server)
  ↓ INSERT/UPSERT
Postgres local (en la farmacia)
  ↓ push_obs_to_render.py
Postgres de Render (la app web lee de acá)
  ↓
Browser de cualquier user (Lisandro, vos, remoto)
```

Hay dos modos: **manual** desde la pantalla `/admin/observer-sync`, y **automático** vía cron del DockerPanel.

## Pantalla `/admin/observer-sync`

Vista con:

### Estado de cada entidad
Para cada una de las 13 entidades sincronizables, muestra:
- **Filas locales**: cuántas hay en la tabla `obs_*` correspondiente.
- **Última sync**: timestamp + duración + cuántas filas se procesaron.
- **Estado**: indicador visual.

### Botones de sync
- Botón **"Sync"** por cada entidad individual.
- Botón **"Sync TODO"** que las corre todas en orden respetando FKs.
- Botón **"Auto-match productos"** corre el matcher EAN ↔ IdProducto.
- Botón **"Push a Render"** replica las tablas obs_* a Render.

### Config
- **Meses de ventas a sincronizar** (`Config.observer_ventas_meses`, default 16): cuántos meses hacia atrás trae el sync de `ventas_mensuales`. Si subís a 24, trae 24 meses; baja la velocidad pero te da más historia.

## Entidades sincronizables (orden recomendado)

El orden importa por las FKs. El botón "Sync TODO" lo respeta automáticamente:

1. **laboratorios** (`obs_laboratorios`) — fabricantes.
2. **rubros** (`obs_rubros`) y **subrubros** — clasificación de productos.
3. **nombres_drogas** (`obs_nombres_drogas`) — monodrogas.
4. **productos** (`obs_productos`) — el catálogo (~122k filas). FK a labs/rubros/drogas.
5. **stock** (`obs_stock`) — stock actual por farmacia.
6. **ventas_mensuales** (`obs_ventas_mensuales`) — agregado por (farmacia, producto, año, mes). El más pesado, ~80k filas.
7. **grupos_clientes** (`obs_grupos_clientes`).
8. **categorias_clientes** (`obs_categorias_clientes`).
9. **obras_sociales** (`obs_obras_sociales`) — 876 OSs.
10. **convenios** (`obs_convenios`) — 596, FK a OS.
11. **planes** (`obs_planes`) — 2510, FK a convenios.
12. **clientes** (`obs_clientes`) — 84k clientes, FKs a grupos/categorías. El más lento, commit parcial cada 5000.

Después de los syncs:
- **Auto-match productos** — vincula `productos.observer_id` con `obs_productos.observer_id` por descripción + lab.
- **Push a Render** — replica las tablas obs_* a la DB de producción.

## Modo automático (DockerPanel cron)

El DockerPanel local corre un **cron embebido** que dispara `/api/auto-sync` en horarios configurados.

Por default:
- Cada 3 horas en horario diurno (8-18hs).
- Cada 6 horas en horario nocturno.
- Lock para evitar overlapping (si todavía corre el anterior, el siguiente espera).

El endpoint `/api/auto-sync` ejecuta en cascada:
1. Verifica que ObServer esté disponible.
2. Sync de las 13 entidades en orden.
3. Auto-match productos.
4. Push a Render.

Cada paso queda registrado en **`/admin/cron-log`** con su estado (ok/error). Si un paso falla, los siguientes no corren.

## Pull desde Render (para máquinas remotas)

Si trabajás desde casa (sin acceso al SQL Server de la farmacia), no podés correr el sync directo. En cambio, **traés un snapshot** de Render:

- DockerPanel → botón **"🔄 Traer DB de Render"** → corre `scripts/pull_from_render.py`.
- Reemplaza tu Postgres local por una copia fresca de Render.
- Toma ~1 minuto. Después reinicia el contenedor web automáticamente.

**Caso típico**: vos en casa modificás código y querés probar con datos reales sin ir a la farmacia. Hacés pull → trabajás. Cuando termines, Lisandro corre el sync desde la farmacia y los datos quedan en Render.

## Banner de alerta de sync atrasado

En el header de la app, si el sync de `ventas_mensuales` o `stock` tiene > 24h, aparece un banner ámbar/rojo arriba de la pantalla con timestamp del último sync. Click "Ver detalle" → `/admin/observer-sync`.

## Términos importantes

- [ObServer](../glosario.md#observer)
- [DockerPanel](../glosario.md#dockerpanel)

## Errores comunes

**"ObServer no disponible"**
La connection a SQL Server falló. Causas:
- Faltan vars de entorno (`OBSERVER_HOST`, `OBSERVER_USER`, etc.) en el `.env`.
- SQL Server apagado o sin red.
- Firewall bloqueando el puerto.

**"Sync de productos falló pero el resto OK"**
Probablemente un product de SQL Server tiene caracteres raros que rompen el upsert. Buscar el error específico en `/admin/cron-log`.

**"Datos en Render no actualizados"**
El push a Render falló. Chequear `/admin/cron-log` filtrando por `proceso=push_render`.
