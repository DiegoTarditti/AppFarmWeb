# Flujo: Convertir un pedido en proceso de compra

> ⚠ STATUS: PENDIENTE

## Para qué sirve

Encadenar las etapas de compra (análisis → pedido → factura → cruce → reclamo) bajo un solo "proceso" con seguimiento de estado.

## Estados del proceso

`BORRADOR` → `ANALIZADO` → `PEDIDO` → `ENVIADO` → `FACTURADO` → `INGRESADO` → `CERRADO`

## Pasos

1. **Crear proceso** desde `/procesos` (nuevo) o desde un pedido guardado (botón "Enviar a Procesos").
2. **Vincular factura** — al subir la factura, podés asociarla al proceso.
3. **Cruzar y reclamar** — desde el proceso accedés al cruce.
4. **Cerrar** — cuando todo se resolvió.

## Términos importantes

- [Proceso de compra](../glosario.md#proceso-de-compra)

## Casos especiales

_(Pendiente: cómo manejar pedidos que se cancelan, procesos sin pedido previo, etc.)_
