# Flujo: Análisis de compra de un laboratorio

> ⚠ STATUS: PENDIENTE — flujo de oro, prioridad #1

## Para qué sirve

Decidir cuántas unidades de cada producto comprar de un laboratorio dado, basado en ventas históricas, stock actual, módulos vigentes y ofertas con mínimo.

## Cuándo usarlo

Cada vez que un laboratorio entra en venta o tenés que armar un pedido recurrente.

## Pasos

1. **Crear proceso** — `/procesos` → "+ Nuevo" → Tipo Laboratorio → elegir lab.
2. **Cuántos días** — pantalla `/observer/analizar` pre-cargada con el lab.
3. **Resultado** — revisar productos sugeridos.
4. **Wizard de análisis** — `/order/<id>`:
   - Paso 1: importar módulos (Excel).
   - Paso 2: confirmar cantidades.
   - Paso 3: cargar ofertas con mínimo.
   - Paso 4: resumen + canal de compra.
5. **Guardar pedido**.
6. **Indicadores** del pedido (botón violeta) — chequear cobertura, riesgos, alternativas.
7. **Enviar a Procesos** o exportar XLSX/PDF.

## Términos importantes

- [Modulo](../glosario.md#modulo)
- [Oferta con mínimo](../glosario.md#oferta-con-mínimo)
- [Canal de compra](../glosario.md#canal-de-compra)

## Casos especiales

_(Pendiente: cómo hacer cuando el lab tiene módulos múltiples, cómo manejar productos sin precio_pvp, etc.)_
