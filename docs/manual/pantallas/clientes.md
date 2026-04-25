# Pantalla: Clientes

> ⚠ STATUS: PENDIENTE

**Ruta**: `/clientes`

## Para qué sirve

Listado y ABM de clientes. Espejo de DW.Clientes de ObServer (~84k filas) + extensión local editable (notas, WhatsApp, email, tags, fecha de nacimiento).

## Filtros

- Búsqueda por nombre, DNI, teléfono o dirección.
- Filtro por grupo.
- Filtro por localidad.

## Demográficos

Botón "Demográficos" → `/clientes/stats` con distribución por grupo, categoría, localidad, provincia + conteo de extensiones cargadas.

## Detalle de cliente

Click en cliente → `/clientes/<id>`:
- Datos read-only de ObServer.
- Form editable para datos locales (extensión).

## Limitación actual

ObServer no expone `IdObraSocial` en `DW.Clientes`, por lo que no se puede cruzar ventas con OS desde clientes. _(Pendiente: vista DW de dispensas o ventas con IdPlan.)_
