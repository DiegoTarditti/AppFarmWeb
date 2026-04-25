# Flujo: Análisis de compra de un laboratorio

El ciclo más usado del sistema. Lo recorrés cada vez que hay que decidir cuánto comprar de un laboratorio dado, basándote en ventas históricas, stock actual, módulos vigentes y ofertas con mínimo.

## Cuándo usarlo

- Te llega un módulo del laboratorio para evaluar.
- Hay que armar un pedido recurrente (mensual / quincenal).
- Querés analizar qué se está vendiendo de un lab antes de cerrar la compra.

## El ciclo completo

```
Procesos → Nuevo proceso de compra
   ↓
Pantalla "Cuántos días" (observer_analizar)
   ↓
Resultado del análisis (lista de productos sugeridos)
   ↓
Wizard /order/<id>: módulos → ofertas → resumen → canal
   ↓
Pedido guardado
   ↓
Indicadores (chequeo de cobertura, riesgos, alternativas)
   ↓
Enviar a Procesos (o XLSX/PDF)
```

## Paso a paso

### 1. Crear el proceso

Entrá a **`/procesos`** → botón **"+ Nuevo proceso de compra"**.

En el modal:
- **Tipo**: Laboratorio.
- **Nombre del lab**: usar el partner selector (autocompleta los laboratorios cargados).
- **Período / etiqueta** (opcional): texto libre tipo "Abril 2026" o "Campaña invierno".

Click en **"Crear"**. El sistema:
- Crea un `procesos_compra` con `estado='BORRADOR'` y `tipo='laboratorio'`.
- Si ObServer tiene datos disponibles → te redirige a `/observer/analizar` con el lab pre-seleccionado.
- Si no → te lleva a `/proceso/<id>` (detail) y desde ahí podés subir un archivo de ventas históricas.

### 2. Pantalla "Cuántos días"

`/observer/analizar` con el lab pre-cargado.

Configurás:
- **Días de cobertura** (`n_days`): cuántos días querés que dure el pedido. Default 35.
- **Año / Mes hasta**: hasta cuándo considerar las ventas históricas. Default mes actual.

Click en **"Analizar"**. El sistema:
- Lee 12 meses de ventas históricas de ObServer (`obs_ventas_mensuales`).
- Calcula promedio mensual, rotación (Alta/Media/Baja), pico, tendencia.
- Aplica los umbrales configurados en `Config` para sugerir cantidades.

### 3. Resultado del análisis

Te lleva a `/purchase/results/<uid>` con la lista de productos del lab sorteada por relevancia. Para cada uno:
- **Stock actual** (de ObServer).
- **Promedio mensual** (últimos 3 / 12 meses).
- **Rotación** (A / M / B).
- **Cantidad sugerida** según `n_days` y rotación.

Podés:
- Filtrar por nombre.
- Ajustar cantidades manualmente si la sugerencia no te convence.
- **Guardar como pedido** → vas al wizard `/order/<id>`.

### 4. Wizard de análisis (`/order/<id>`)

Pantalla con varios pasos en card-style ("step-card"):

#### Paso 1 — Módulos

Subís el Excel del módulo de descuento del laboratorio (formato **modulo_packs** o módulo libre).

El sistema:
- Parsea el Excel.
- Matchea cada ítem del módulo contra los productos del pedido por **EAN**.
- Si un EAN del módulo no aparece en el pedido pero sí está en el catálogo, te muestra **panel de match manual** (dos columnas estilo `compare.html`) para que correlaciones.
- Aplica las cantidades del módulo a las propuestas del pedido.

Guardar → confirma cantidades.

#### Paso 2 — Confirmar cantidades

Tabla resumen con módulo aplicado vs cantidad final. Si algo no cierra, ajustás.

#### Paso 3 — Ofertas con mínimo

Carga el Excel de ofertas con mínimo (formato Bernabó típicamente).

El sistema:
- Muestra **todos los grupos** (no solo los que tienen saldo).
- Marca con ✓ verde los que llegan al mínimo.
- Botón **"Completar"** por producto individual para sumar la cantidad faltante.
- Auto-carga de ofertas guardadas si el lab tiene `OfertaMinimo` cargadas en DB.

#### Paso 4 — Resumen + Canal de compra

- Tabla final con todos los productos, cantidad, precio, subtotal.
- Card **"Canal de compra"** con radios:
  - **Laboratorio (directo)**: el pedido entra directo del fabricante.
  - **Droguería (vía X)**: elegir droguería del select. La plantilla de exportación se filtra a la del proveedor elegido. Ver [Análisis vía droguería](./02_analizar_drogueria.md).
- Badge en el header del paso 4 mostrando el canal elegido.
- Auto-save: cualquier cambio se persiste en `pedido.canal` + `partner_id`.

#### Botones del wizard
- **💾 Guardar análisis** (top bar): persiste el estado actual en DB (`pedido.analisis_json`).
- **Restaurar** (banner violeta): si hay análisis guardado previo, te ofrece volver a él.
- **Exportar XLSX/PDF** por paso.

### 5. Indicadores (chequeo previo)

Volvé a `/orders` y apretá el botón violeta **📊 Indicadores** en el pedido. Modal con 5 tabs:
- **Cobertura**: días post-compra por producto. Ver alertas rojas si quedás corto.
- **Riesgos**: items sin movimiento, sobre-pedidos, sin link a ObServer.
- **Top productos**: los 10 más vendidos del pedido + tendencia ▲▼.
- **Mix**: por monodroga + por laboratorio.
- **Estacionalidad**: heatmap mensual del pedido agregado.

Si hay items "sin link a ObServer", apretás **"🔗 Vincular ahora"** y cierra el bridge automáticamente. Ver [Vincular productos](../admin/vincular_productos.md).

### 6. Enviar a procesos / Exportar

Decidís el destino:
- **Enviar a Procesos**: el pedido pasa a estado `ENVIADO` y queda asociado al proceso. Hereda el canal de compra.
- **XLSX**: exporta con la plantilla de exportación del laboratorio (`ExportTemplate`) si está configurada, sino formato genérico.
- **PDF**: imprime una propuesta de pedido.

## Errores comunes

**"No hay estadísticas de ventas"**
La tabla `obs_ventas_mensuales` está vacía o desactualizada. Corré el sync desde DockerPanel o pulleá de Render. Ver [Observer sync](../admin/observer_sync.md).

**"Items sin link a ObServer" en Indicadores**
El pedido se generó desde Excel y los EANs no están bridgeados. Click en **"🔗 Vincular ahora"** dentro del modal, matchea por descripción + lab.

**El módulo no cierra contra el pedido**
Faltó match por EAN. Usá el panel de match manual (paso 1 del wizard) para ingresar correspondencias. Quedan guardadas en la tabla `productos` para reuso futuro.

## Términos importantes

- [Modulo](../glosario.md#modulo)
- [Oferta con mínimo](../glosario.md#oferta-con-mínimo)
- [Canal de compra](../glosario.md#canal-de-compra)
- [Rotación](../glosario.md#rotación)
- [EAN](../glosario.md#ean)

## Atajos

- En el resultado del análisis, podés filtrar productos por nombre con la searchbar.
- En los pedidos guardados, click en la fila expande el detalle sin salir.
- Si el lab tiene plantilla de exportación configurada, el XLSX usa ese formato custom (columnas que el lab pide).
