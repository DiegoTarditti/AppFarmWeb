# Pantalla: Obras Sociales (catálogo)

Catálogo de obras sociales con sus convenios y planes asociados. Read-only — espejo de las vistas `DW.ObrasSociales`, `DW.Convenios` y `DW.Planes` de ObServer.

**Acceso**: card teal "Obras Sociales" en el home, o `/obras-sociales/catalogo`.

## Vista principal

Tabla de las ~876 obras sociales con:

| Columna | Significa |
|---|---|
| Obra Social | Nombre |
| **Convenios** | Cuántos convenios tiene esta OS (si > 0, en negrita) |
| **Planes** | Cuántos planes activos hay en sus convenios (si > 0, en negrita) |

Filtro de búsqueda libre por nombre.

Click en cualquier fila → detalle.

## Detalle de OS (`/obras-sociales/catalogo/<id>`)

Vista jerárquica:
- Header con nombre de la OS.
- Listado de **convenios** asociados, cada uno como `<details>` colapsable. Si hay 3 o menos, expandidos por default.
- Dentro de cada convenio, tabla de **planes**:
  - Nombre del plan.
  - Habilitado (✓ verde si sí, gris si no).
  - ID del plan.

## Limitaciones actuales

- **Sin link a ventas o clientes**: hoy las tablas `obs_obras_sociales`, `obs_convenios`, `obs_planes` están **huérfanas** — no apuntan a clientes ni a ventas. Por lo tanto no se puede:
  - Ver "qué OS factura más en mi farmacia".
  - Listar los clientes de una OS.
  - Cruzar ventas por OS.
- Para habilitar ese cruce hace falta que ObServer exponga `IdPlan` o `IdObraSocial` en `DW.ProductosVendidos` o que sumemos una vista de dispensas. Ver [`docs/mejoras_pendientes.md`](../../mejoras_pendientes.md) sección "Cruce ventas vs Obras Sociales".

## Cuándo usarla

- Consulta del catálogo: ¿esta OS tiene tal plan? ¿qué convenios maneja?
- Verificar si una OS está cargada antes de generar una venta con descuento.

## Términos importantes

- Obra Social, Convenio, Plan — definiciones en ObServer.
