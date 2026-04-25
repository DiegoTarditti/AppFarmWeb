# Flujo: Cruce de factura vs ERP y generar reclamo

> ⚠ STATUS: PENDIENTE

## Para qué sirve

Identificar diferencias entre lo que la droguería facturó y lo que llegó físicamente (vía Excel del ERP o recepción de ObServer), y armar un reclamo formal con PDF.

## Pasos

1. **Cruzar factura vs ERP** — `/invoice/<id>/compare`. Tabla doble: factura a la izquierda, ERP a la derecha.
2. **Match automático** en 3 pasos: barcode exacto → descripción normalizada → tabla `barcode_mappings` del proveedor.
3. **Match manual** para los que faltan: ingresar números para correlacionar.
4. **Aplicar mapping** — guarda equivalencias para reuso futuro.
5. **Ver diferencias** — `/results/<invoice_id>`. Seleccionar las que querés reclamar con checkboxes.
6. **Generar reclamo** → renderiza el reclamo + descarga PDF automáticamente.
7. **Marcar completado** cuando la droguería resuelve.

## Términos importantes

- [Alfabeta](../glosario.md#alfabeta)
- [EAN](../glosario.md#ean)

## Atajos

- Auto-descarga PDF al llegar desde "Generar reclamo".
