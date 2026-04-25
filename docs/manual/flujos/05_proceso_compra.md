# Flujo: Convertir un pedido en proceso de compra

Un **proceso de compra** es un wrapper que agrupa todas las etapas de una compra (análisis → pedido → factura → cruce → reclamo → cierre) bajo un mismo identificador. Sirve para hacer seguimiento end-to-end y no perder de vista en qué estado quedó cada compra.

## Cuándo usarlo

- Cuando querés trackear una compra **desde el análisis hasta el cierre** sin que se pierda la trazabilidad.
- Cuando querés que las distintas etapas (pedido, factura, reclamo) queden **vinculadas** entre sí.
- Cuando un mismo proceso pasa por varias personas o sesiones distintas.

## Estados del proceso

```
BORRADOR  →  ANALIZADO  →  PEDIDO  →  ENVIADO  →  FACTURADO  →  INGRESADO  →  CERRADO
```

Cada salto se dispara automáticamente al completar el paso correspondiente.

## Caminos para crear un proceso

### Camino A — desde cero (lo más común)

`/procesos` → **"+ Nuevo proceso de compra"**.

- Elegís Tipo + Partner (Laboratorio / Droguería / Proveedor / Otro).
- Si tipo = Laboratorio y ObServer está disponible → te redirige al análisis.
- Si tipo = Droguería / Proveedor → te lleva al detail del proceso, desde donde subís la factura.

Ver [Análisis de laboratorio](./01_analizar_laboratorio.md) para el camino completo desde análisis.

### Camino B — desde un pedido guardado

Si ya tenés un pedido analizado en `/orders` y querés convertirlo en proceso:

- Apretás **"Enviar a Procesos"** en la fila del pedido.
- Si el pedido tiene `canal` y `partner_id` seteados (en el paso 4 del wizard) → usa esos datos directo.
- Si no → modal preguntando canal + droguería.

El sistema crea el proceso con:
- `tipo` = laboratorio o drogueria según canal.
- `partner_id` apuntando al lab o al proveedor.
- `pedido_id` apuntando al pedido.
- Estado `PEDIDO` automáticamente (se saltó BORRADOR + ANALIZADO).
- En `/orders` el pedido aparece con badge **"En Procesos"** (verde).

### Camino C — desde una factura

Si subiste una factura (sin proceso previo) y querés vincularla a un proceso:

- En `/proceso/<id>` (detail), si todavía no hay factura asociada, el sistema te muestra **"Facturas libres del partner"** (facturas del mismo proveedor que aún no están en ningún proceso).
- Click en una → la asocia. El estado pasa a `FACTURADO`.

## Pantalla del proceso (`/proceso/<id>`)

Vista única con todas las etapas:
- **Pedido vinculado**: link al pedido + datos básicos.
- **Factura vinculada**: link a la factura + cruce + reclamo.
- **Sesión de análisis**: link a `AnalisisSesion` para ver el análisis original.
- **Pasos guardados**: JSON con módulos / ofertas / canal del wizard.
- **Notas**: texto libre.

Botones:
- **Avanzar de estado** (cuando aplica).
- **Cerrar**: marca como `CERRADO`.

## Listado de procesos (`/procesos`)

Tabla con todos los procesos. Filtros:
- Por **estado** (BORRADOR, PEDIDO, FACTURADO, etc.).
- Por **tipo** (laboratorio, drogueria, proveedor).
- Búsqueda libre por nombre del partner.

Counts arriba: cuántos procesos hay en cada estado.

## Cuándo NO usar procesos

- Si solo querés analizar pero **no comprometerte** a comprar → quedate en `/purchase` o en `/orders` sin "Enviar a Procesos".
- Si subís una factura puntual sin contexto de pedido previo → podés dejarla suelta en `/invoices`. Después la podés enganchar a un proceso si querés.

## Casos especiales

### Pedido cancelado
Si después de crear el proceso decidís cancelar la compra: en `/proceso/<id>`, agregá nota explicando + cerrá manualmente. El estado queda `CERRADO` con `cerrado_en` registrado.

### Múltiples facturas para un proceso
Hoy un proceso = una factura. Si una compra se factura en partes, abrí un proceso por factura.

### Proceso sin pedido previo
Cuando creás directamente desde una factura (sin análisis previo), el proceso arranca en estado `FACTURADO` y `pedido_id=NULL`.

## Términos importantes

- [Proceso de compra](../glosario.md#proceso-de-compra)
- [Canal de compra](../glosario.md#canal-de-compra)
