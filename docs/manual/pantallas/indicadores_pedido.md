# Pantalla: Indicadores del pedido

> ⚠ STATUS: PENDIENTE — pantalla principal de toma de decisión

**Acceso**: botón violeta "📊 Indicadores" en cada pedido de `/orders`.

## Para qué sirve

Mini-dashboard del pedido pensado para responder en segundos: "¿este pedido cubre lo que necesito? ¿estoy comprando algo que no debería? ¿hay alternativas?".

## Pestañas

### Cobertura
Por cada producto: días pre y post compra al ritmo de los últimos 3 meses. Color rojo (<15) / ámbar / verde / sky (>180). Sirve para ajustar cantidades.

### Riesgos
Items con flags: sin movimiento, sobre-pedido, stock dormido, sin link a ObServer. Si hay items sin link, aparece un botón "🔗 Vincular ahora".

### Top productos
Top 10 por unidades 12m con tendencia ▲/▼.

### Mix
Doughnut por monodroga + por laboratorio.

### Estacionalidad
Bar chart mensual del pedido agregado.

## Sub-modal de alternativas

Click en cualquier producto con monodroga → abre productos de la misma droga agrupados por laboratorio. La fila del producto del pedido aparece marcada con badge "👉 PEDIDO" en amber.

## Términos importantes

- [Stock dormido](../glosario.md#stock-dormido)
- [Monodroga](../glosario.md#monodroga)
- [IdProducto](../glosario.md#idproducto)
