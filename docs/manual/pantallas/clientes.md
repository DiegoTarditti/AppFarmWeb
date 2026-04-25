# Pantalla: Clientes

Listado y ABM de los clientes de la farmacia. Es un espejo de la tabla `DW.Clientes` de ObServer (~84k clientes) más una **extensión local editable** para datos que ObServer no maneja (notas, WhatsApp, email, tags, fecha de nacimiento).

**Acceso**: card sky "Clientes" en el home, o `/clientes`.

## Vista principal

Tabla con paginación. Columnas:

| Columna | Significa |
|---|---|
| Apellido y Nombre | Como viene de ObServer |
| Documento | Tipo + número |
| Teléfono | El que carga ObServer |
| Localidad | Domicilio |
| Dirección | Calle + número |
| Grupo | Grupo de cliente (de `DW.GruposClientes`) |
| **Bullet** | • verde si tiene extensión local cargada (notas / whatsapp / etc.) |

## Filtros

- **Búsqueda**: por apellido/nombre, DNI, teléfono o dirección (parcial).
- **Grupo de cliente**: dropdown.
- **Localidad**: texto libre, parcial.

Click en cualquier fila → `/clientes/<observer_id>` para ver detalle.

## Detalle de cliente (`/clientes/<id>`)

Dos secciones:

### Datos de ObServer (read-only)
- Apellido y nombre.
- Tipo + número de documento.
- Domicilio (calle, CP, localidad, provincia).
- Grupo + categoría (si están cargados).
- Teléfono.
- Id de farmacia.

### Extensión local (editable)
Form con:
- **Notas** (textarea) — cualquier observación.
- **Tags** (texto separado por coma) — para clasificación rápida.
- **WhatsApp** — número con formato libre.
- **Email**.
- **Fecha de nacimiento**.

Botones:
- **Guardar** — crea o actualiza la `Cliente` (extensión).
- **Borrar extensión** — elimina los datos locales sin tocar el cliente de ObServer.

## Demográficos

Botón **"Demográficos"** en el header del listado → `/clientes/stats`.

Pantalla con cards y charts:
- **Card de extensión local**: cuántos clientes tienen WhatsApp, email, notas, tags cargados.
- **Por grupo** (bar chart) — distribución por grupo de cliente.
- **Por categoría** (bar chart) — distribución por categoría.
- **Top 15 localidades** (bar horizontal) — concentración geográfica.
- **Por provincia** (tabla) — todas las provincias.

Si no hay clientes sincronizados, muestra banner ámbar con instrucciones.

## Limitaciones actuales

- **Sin link a Obras Sociales**: la vista `DW.Clientes` no expone `IdObraSocial` en el sync actual. No se puede cruzar clientes con OS desde acá. Pendiente que ObServer exponga el campo o que armemos el link via dispensas. Ver [`docs/mejoras_pendientes.md`](../../mejoras_pendientes.md).
- **Sin link a ventas**: `obs_ventas_mensuales` está agregado por producto, no por cliente. No se puede ver "qué compra cada cliente" hoy.

## Datos que SÍ se pueden ver

- Información demográfica completa (grupo, categoría, localidad, provincia).
- Datos de contacto editables localmente (la extensión).
- Búsqueda libre por nombre / DNI / dirección / teléfono.

## Términos importantes

- [Cliente](../glosario.md) — tabla local extensión.
- [ObsCliente](../glosario.md) — espejo de DW.Clientes.
