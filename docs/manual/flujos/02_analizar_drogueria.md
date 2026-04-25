# Flujo: Análisis vía droguería

> ⚠ STATUS: PENDIENTE

## Para qué sirve

Cuando analizás históricos de un laboratorio pero la mercadería entra por una droguería (con su precio, plazo, plantilla de exportación distinta).

## Cuándo usarlo

Caso típico: analizás Roemmers, pero en vez de comprar directo a Roemmers, comprás a Kellerhoff que distribuye varios labs.

## Pasos

1. Misma análisis que [Análisis de laboratorio](./01_analizar_laboratorio.md).
2. En el **paso 4 (resumen)** elegir el canal de compra → "Vía droguería" → seleccionar la droguería.
3. La plantilla de exportación se filtra automáticamente a la de la droguería.
4. Al enviar a Procesos, el `tipo` queda como `drogueria` y el `partner_id` apunta al proveedor.

## Diferencias con el análisis directo

- El precio de costo lo pone la droguería, no el laboratorio.
- Los reclamos por diferencias se hacen contra la droguería.
- La plantilla de exportación es distinta.

## Términos importantes

- [Canal de compra](../glosario.md#canal-de-compra)
- [Droguería](../glosario.md#droguería)
