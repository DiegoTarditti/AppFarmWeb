# Pantalla: Procesos de compra

Listado central de todos los procesos de compra (análisis → pedido → factura → cruce → reclamo) con su estado actual.

**Acceso**: `/procesos`. Card "Procesos de compra" en el home.

## Vista principal

Tabla con:

| Columna | Significa |
|---|---|
| Estado | Badge color-coded (BORRADOR gris, PEDIDO sky, FACTURADO ámbar, INGRESADO verde, CERRADO gris claro) |
| Tipo | laboratorio / drogueria / proveedor / otro |
| Partner | Nombre del lab/droguería/proveedor |
| Pedido vinculado | Link al `/order/<id>` si está vinculado |
| Factura vinculada | Link al `/invoice/<id>/compare` si está vinculada |
| Reclamo vinculado | Link al `/claim/<id>` si existe |
| Período | Etiqueta libre del análisis original |
| Creado / Actualizado | Timestamps |

## Filtros y búsqueda

- **Por estado**: dropdown con los 7 estados + "todos".
- **Por tipo**: laboratorio / drogueria / proveedor / otro / todos.
- **Búsqueda libre**: por nombre del partner.
- **Counts arriba**: cuántos procesos hay en cada estado activo (no cerrados).

## Crear nuevo proceso

Botón **"+ Nuevo proceso de compra"** abre modal:
- **Tipo** (radios): Laboratorio / Droguería / Proveedor / Otro.
- **Partner**: selector que cambia según tipo (dropdown de labs o de proveedores).
- **Período / etiqueta** (opcional): texto libre.

Al crear:
- Si `tipo='laboratorio'` y ObServer está disponible → te redirige a `/observer/analizar` con el lab pre-cargado para arrancar el análisis.
- Si no → te lleva al detail del proceso (`/proceso/<id>`).

Ver el flujo completo en [Análisis de laboratorio](../flujos/01_analizar_laboratorio.md).

## Banner de estado de ventas

Arriba de la tabla, un banner según el estado de la sync de ventas:

- ✓ Verde — datos al día.
- ⚠ Ámbar — sync atrasado, conviene refrescar.
- 🔴 Rojo — sin datos de ventas. Sin esto el análisis no funciona.

Click en el banner → `/admin/observer-sync`.

## Vista de detalle (`/proceso/<id>`)

Página individual del proceso con:
- Header con estado actual + botón para cerrar.
- Card de pedido vinculado (link + datos básicos).
- Card de factura vinculada (link + cruce + reclamo).
- Card de sesión de análisis (link a `AnalisisSesion` original).
- Card de pasos guardados (JSON del wizard: módulos, ofertas, canal).
- Sección de notas (texto libre editable).
- "Pedidos libres" / "Facturas libres" del partner para asociar manualmente si todavía no hay vínculos.

## Términos importantes

- [Proceso de compra](../glosario.md#proceso-de-compra)
- [Canal de compra](../glosario.md#canal-de-compra)
- [Pedido](../glosario.md#pedido)
