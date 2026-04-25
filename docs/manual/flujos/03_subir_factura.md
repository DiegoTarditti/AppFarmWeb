# Flujo: Subir una factura (control de ingreso)

> ⚠ STATUS: PENDIENTE — flujo crítico

## Para qué sirve

Procesar una factura recibida de droguería, parsearla automáticamente, y dejarla lista para cruzar contra el ERP / ObServer.

## Cuándo usarlo

Cuando llega mercadería con su factura física (PDF). Antes de cargar la mercadería en stock.

## Pasos

1. **Subir** — `/ingresos` → arrastar PDF + Excel ERP (o solo PDF).
2. **Parser automático** — el sistema detecta el proveedor por CUIT y lo parsea.
3. **Cruce manual** — si hay items sin match, pantalla `/invoice/<id>/compare` para correlacionar.
4. **Confirmar** → guarda diferencias en `stock_differences`.

## Parsers disponibles

- Kellerhoff
- Pharmos
- 20 de Junio

## Si tu droguería no tiene parser

Usar el **converter** (`/converter/upload`) — flujo de aprendizaje sin código que arma el parser desde una factura ejemplo.

## Términos importantes

- [EAN](../glosario.md#ean)
- [Alfabeta](../glosario.md#alfabeta)

## Errores comunes

_(Pendiente: PDFs con OCR sucio, descripciones que no matchean, etc.)_
