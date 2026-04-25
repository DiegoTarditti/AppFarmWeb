# Pantalla: Pedidos guardados

Lista de todos los pedidos generados desde análisis de compra. Es el lugar central para revisar pedidos pasados, abrirlos para editar, generar indicadores, exportar o convertirlos en procesos de compra.

**Acceso**: `/orders`. Card "Pedidos guardados" en el home.

## Tabs por estado

Arriba aparecen 3 tabs:
- **Pendientes** — pedidos que todavía no se enviaron a procesos. Es el filtro default.
- **Procesados** — ya enviados a procesos (`estado='ENVIADO'`).
- **Todos** — sin filtro.

## Estructura de cada fila

Cada pedido es una `<details>` colapsable. Al colapsar:

| Sección | Qué muestra |
|---|---|
| Toggle ▶ | Click expande / colapsa el detalle |
| Laboratorio + canal + estado | Nombre del lab + badge de canal (DIRECTO violeta o → DROGUERÍA sky) + badge "💾 Análisis" si tiene wizard guardado |
| Periodo + días | Periodo del análisis + n_days |
| Stats | Productos, Unidades, Total est., Guardado, Procesado |
| **Acciones** | 5 botones (ver abajo) |

### Botones de acción

1. **📊 Indicadores** (violeta) — abre modal con 5 pestañas. Ver [Indicadores del pedido](./indicadores_pedido.md).
2. **Analizar** (ámbar) — abre el wizard `/order/<id>` con módulos, ofertas, resumen, canal.
3. **Enviar a Procesos** (sky) — convierte el pedido en `procesos_compra`. Si el pedido ya tiene canal definido en paso 4, lo usa directo. Si no, abre modal para preguntarte canal + droguería.
   - Si ya está enviado, muestra **"En Procesos"** (verde) en su lugar.
4. **XLSX** (verde) — exporta a Excel. Si el lab/droguería tiene plantilla, usa ese formato. Si no, genérico.
5. **PDF** (rojo) — exporta a PDF para imprimir.
6. **🗑** — eliminar pedido (con confirmación).

## Detalle expandido

Al expandir, ves la tabla completa de productos del pedido con:
- Producto, código de barras, P.PVP, cantidad, subtotal.
- Filtro de productos (searchbar arriba).

Botón **"+ Producto"** para agregar manualmente (raro, normalmente vienen del análisis).

## Layout responsive

- **Desktop ancho (≥1280px)**: todo en una línea — chevron + lab + stats (5 columnas) + 5 botones.
- **Desktop normal (1024-1280px)**: stats con menos columnas (oculta Guardado y Procesado), botones siguen al lado.
- **Tablet/mobile**: stats en grilla 3 columnas, botones en row separada abajo, fechas adicionales debajo.

## Búsqueda y filtros

Solo el filtro por estado (tabs). Búsqueda por nombre del lab no implementada todavía — para encontrar uno específico, expandís el correspondiente o usás Ctrl+F del browser.

## Términos importantes

- [Canal de compra](../glosario.md#canal-de-compra)
- [Pedido](../glosario.md#pedido)
- [Proceso de compra](../glosario.md#proceso-de-compra)
