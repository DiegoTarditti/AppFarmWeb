# Flujo: Subir una factura (control de ingreso)

Cuando llega mercadería con su factura PDF, este flujo la procesa automáticamente, la cruza con lo que llegó realmente (vía ERP o ObServer), y deja todo listo para generar reclamo si hay diferencias.

## Cuándo usarlo

- Llegó mercadería con factura física en PDF.
- Querés controlar lo facturado vs lo recibido **antes** de cargar al stock.

## Pasos

### 1. Subir el PDF (y opcionalmente Excel del ERP)

Entrá a **`/ingresos`**. Pantalla con dos secciones:

- **Izquierda**: form para subir el PDF de la factura + Excel del ERP (opcional).
  - **PDF de factura**: obligatorio. Drag & drop o click para seleccionar.
  - **Excel ERP**: opcional. Si lo tenés, el cruce arranca con datos. Si no, lo cargás después o usás recepciones de ObServer.
  - **Tipo**: FAC (factura) o NCR (nota de crédito). FAC es default.

Click en **"Procesar"**. El sistema:
1. Detecta el proveedor por **CUIT extraído del PDF** y el `parser_file` configurado en `proveedores`.
2. Llama al parser correspondiente (`parsers/<slug>.py`).
3. Valida los ítems extraídos.
4. Si la cantidad de ítems es 0 → flash de error y vuelve a `/ingresos` (no guarda nada).
5. Si extrae OK, crea `Invoice` + `InvoiceItem`s en DB.
6. Si subiste Excel ERP, lo parsea y guarda `ErpStock`.
7. Te lleva a `/invoice/<id>/compare` para el cruce.

### 2. Si tu proveedor no tiene parser

Hay 3 parsers activos hoy: **Kellerhoff**, **Pharmos**, **20 de Junio**. Si tu proveedor no es ninguno:

- Usá el **Conversor** (`/converter/upload`). Subís un PDF ejemplo, el sistema te guía paso a paso para identificar dónde está cada campo (cantidad, descripción, EAN, importe). Genera un parser custom sin escribir código. Ver [docs/converter_flow.md](../../converter_flow.md) para detalle.

### 3. Pantalla de cruce

`/invoice/<id>/compare`. Dos tablas lado a lado:

- **Izquierda**: ítems de la factura (numerados, ordenados por descripción).
- **Derecha**: ítems del ERP / ObServer.

El sistema **ya hizo cruce automático** en 3 pasos:
1. **EAN exacto**: matchea ítems por `codigo_barra` igual.
2. **Descripción normalizada**: si no hay EAN match, intenta por descripción limpia.
3. **`barcode_mappings`**: equivalencias manuales guardadas para este proveedor (de cruces anteriores).

Los matchados aparecen con **✓ verde** en la columna izquierda.

### 4. Match manual

Para los que no matchearon, escribís el número de la fila ERP correspondiente al lado del item de factura. El sistema:
- Te muestra la columna **Ratio %** (precio factura vs precio ERP) para detectar errores.
- Cuando confirmás, guarda la equivalencia en `barcode_mappings(proveedor_id, codigo_barra_factura, codigo_barra_erp)` para que la próxima vez el match sea automático.

### 5. Sync con ObServer (opcional)

Si tenés ObServer disponible y NO subiste Excel ERP, podés traer las recepciones reales:

- Botón **"Sincronizar con ObServer"** en la pantalla de cruce.
- Te pide el **número de comprobante de recepción** de ObServer (manual por ahora).
- Trae las recepciones, las guarda como `ErpStock`, hace el cruce automático.

### 6. Confirmar el cruce

Apretás **"Aplicar cruce"**. El sistema:
- Actualiza `stock_differences` con las diferencias encontradas (cantidad facturada vs recibida).
- Guarda los mappings nuevos en `barcode_mappings`.
- Te lleva a `/results/<invoice_id>` para revisar diferencias y armar reclamo.

A partir de acá → ver [Cruce y reclamo](./04_cruce_y_reclamo.md).

## Validaciones automáticas

- Si el parser devuelve 0 ítems → no se guarda la factura, te avisa con flash.
- El parser usa `_normalize_quadrupled` (en `helpers.py`) que limpia 3 artefactos comunes de PDF: caracteres cuadruplicados (negrita), letter-spacing, rellenos de puntos.
- Si el PDF es escaneado (sin capa de texto), corre OCR (pytesseract).

## Errores comunes

**"Parser devolvió 0 ítems"**
El PDF no matchea con el regex del parser. Posibles causas:
- Layout cambió en una versión nueva.
- PDF escaneado de mala calidad.
Fix: usar el conversor para reaprenderlo, o ajustar el parser manualmente.

**"Proveedor desconocido"**
El CUIT extraído no matchea ningún `proveedores.cuit`. Cargá el proveedor en `/providers` con su CUIT correcto.

**"PDFs con OCR sucio"**
Productos con descripciones tipo "G I e n i o I" en lugar de "Geniol". El normalizador maneja la mayoría, pero a veces queda algún caso. El cruce manual lo resuelve.

## Términos importantes

- [EAN](../glosario.md#ean)
- [Alfabeta](../glosario.md#alfabeta)
- [Droguería](../glosario.md#droguería)

## Tabla de parsers

| Proveedor | Slug del parser | Match |
|---|---|---|
| Droguería Kellerhoff | `droguer_a_kellerhoff_s_a.py` | EAN |
| Pharmos | `pharmos.py` | descripción (refs internas como EAN) |
| 20 de Junio | `20_de_junio.py` | EAN |
