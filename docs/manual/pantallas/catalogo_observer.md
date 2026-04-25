# Pantalla: Catálogo ObServer

Lista completa de los productos del catálogo ObServer (~122k filas). Read-only — es un espejo de las tablas internas de ObServer, sirve para consulta, no para edición.

**Acceso**: card violeta "Catálogo ObServer" en el home, o `/obs/productos`.

## Vista principal

Tabla con paginación de 50 productos por página. Columnas:

| Columna | Significa |
|---|---|
| Descripción | Nombre del producto (con badge **"BAJA"** si está dado de baja en ObServer) |
| Laboratorio | Fabricante (de `obs_laboratorios`) |
| **Monodroga** | Principio activo. **Clickeable** → te lleva a `/estadisticas/drogas?q=<droga>` con filtro pre-aplicado |
| Stock | Stock actual de tu farmacia (de `obs_stock`). Verde negrita si >0, gris guion si 0 |
| Prom 3m | Promedio mensual de últimos 3 meses |
| Prom 12m | Promedio mensual de últimos 12 meses |
| Alfabeta | Código alfabeta de Argentina (índice de ObServer) |
| EAN local | Código EAN del producto si está vinculado vía bridge `productos.observer_id`. Verde si linkeado, gris "sin vincular" si no |

## Filtros

- **Búsqueda**: por nombre o código alfabeta (parcial, case-insensitive).
- **Laboratorio**: dropdown con todos los labs.
- **"Solo activos"** (checkbox): si marcado, oculta los productos con `fecha_baja` set. Default: **muestra todos** (incluso bajas).

Botón **"Filtrar"** aplica + **"Limpiar"** resetea.

## Productos baja

Por default la pantalla muestra **todos los productos**, incluso los marcados con `fecha_baja` en ObServer. Estos aparecen:
- Con badge **"BAJA"** (gris).
- Con opacidad reducida en la fila.

¿Por qué mostramos las bajas?
- Muchos productos marcados como "baja" en ObServer **siguen vendiéndose** efectivamente. La fecha_baja a veces es histórica.
- Buscar Taural y no encontrarlo (cuando sí lo manejás) genera confusión. Mejor mostrar y aclarar.

Si querés filtrar solo activos, marcás el checkbox "Solo activos".

## Bridge con tabla local

La columna **"EAN local"** indica si ese producto de ObServer está bridgeado con un `Producto` de la tabla local (vía `productos.observer_id`):
- **Verde con EAN** — linkeado, podemos cruzar con facturas, pedidos, etc.
- **"sin vincular"** (gris) — todavía no aparecimos un EAN para esto. Cuando llegue una factura con el producto, el match lo va a linkeear.

Para forzar el link (ej. después de agregar productos al catálogo local), correr el script o usar el bridge desde Indicadores. Ver [Vincular productos](../admin/vincular_productos.md).

## Cuándo usarla

- **Antes de armar un pedido**: ¿qué presentaciones existen de tal droga en este lab?
- **Drill-down desde estadísticas**: click en una droga → catálogo filtrado.
- **Diagnóstico**: cuando un producto no aparece en una análisis, chequear si está bridgeado.

## Términos importantes

- [Alfabeta](../glosario.md#alfabeta)
- [EAN](../glosario.md#ean)
- [Monodroga](../glosario.md#monodroga)
- [IdProducto](../glosario.md#idproducto)
