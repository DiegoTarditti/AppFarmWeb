# Admin: Sync con ObServer

> ⚠ STATUS: PENDIENTE

## Para qué sirve

Mantener actualizadas las tablas locales `obs_*` (productos, stock, ventas mensuales, clientes, etc.) desde la DB real de ObServer (SQL Server 2014).

## Modos

### Sync automático (cron en DockerPanel)

DockerPanel corre cada X horas en horario configurado. Lock para evitar overlap.

### Sync manual

Ruta `/admin/observer-sync` — pantalla con checkboxes por entidad para correr sync selectivo.

## Entidades sincronizables

- Laboratorios
- Rubros / Subrubros
- Nombres de drogas
- Productos (cabeza)
- Stock por farmacia
- Ventas mensuales agregadas
- Grupos / Categorías de clientes
- Clientes
- Obras Sociales / Convenios / Planes

## Push a Render

Una vez sincronizado en local (farmacia), el script `scripts/push_obs_to_render.py` replica las tablas `obs_*` a Render para que la app web tenga datos.

## Pull desde Render (para máquinas remotas)

Botón "🔄 Traer DB de Render" en DockerPanel descarga snapshot. Útil para trabajar desde casa.
