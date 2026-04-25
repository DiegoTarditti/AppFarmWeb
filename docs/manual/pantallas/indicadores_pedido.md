# Pantalla: Indicadores del pedido

Mini-dashboard para tomar decisiones rápidas sobre un pedido **antes** de enviarlo. Responde en segundos: ¿este pedido cubre lo que necesito? ¿hay algo que no debería estar comprando? ¿hay alternativas mejores en otros labs?

**Acceso**: en `/orders`, botón violeta **📊 Indicadores** en cada pedido.

## Por qué importa

Los pedidos suelen ser largos (100-300 items). Sin indicadores, tendrías que ir producto por producto chequeando stock, ventas históricas y precio. La pantalla agrega y resalta lo importante.

## Las 5 pestañas

### 1. Cobertura — ¿voy a tener stock?

Tabla con todos los productos del pedido y, por cada uno:

| Columna | Qué dice |
|---|---|
| Producto | Descripción + monodroga |
| Pedido | Cantidad que estás pidiendo |
| Stock | Stock actual en farmacia |
| Uni 3m | Unidades vendidas en últimos 3 meses |
| **Días pre** | Días que dura el stock actual al ritmo de los últimos 3m |
| **Días post** | Días que va a durar tras la compra |
| Δ días | Cuánto suma la compra |
| Alternativas | Botón "⇄ Comparar otros labs" si tiene monodroga |

**Color de los días post-compra:**
- 🔴 **rojo < 15** — vas a quedar corto, considerá subir cantidad.
- 🟡 **ámbar 15–60** — ajustado, OK para muchos casos.
- 🟢 **verde 60–180** — cómodo.
- 🔵 **sky > 180** — sobre-comprando, considerá bajar.

Las filas se ordenan por **días post-compra ascendente** → los más críticos arriba.

### 2. Riesgos — ¿qué merece una segunda mirada?

Lista de productos con flags problemáticos:

- **Sin ventas en 12m** — comprás algo que nunca vendiste.
- **Sin ventas en 3m** (con ventas en 12m) — el producto venía vendiéndose pero se frenó.
- **Sobre-pedido** — la cantidad solicitada es >3x el promedio mensual de venta.
- **Stock previo > 180 días** — ya tenés un montón, ¿por qué pedís más?
- **Aún corto post-compra** — días post < 15.
- **Sin link a ObServer** — no se pudo bridgear, no hay datos para evaluar.

Si hay items "sin link a ObServer", aparece un banner violeta con botón **"🔗 Vincular ahora"** que los linkea automáticamente por descripción + laboratorio. Ver [Vincular productos](../admin/vincular_productos.md).

### 3. Top productos — los caballos del pedido

Bar chart horizontal con los **10 productos más vendidos** del pedido (por unidades 12m). Tabla debajo con:

- Posición #
- Producto
- Pedido (cantidad solicitada)
- Uni 12m
- **Tendencia** (▲ verde / ▼ rojo + %): proyección anualizada (uni_3m × 4) vs uni_12m. Detecta productos que se están acelerando o frenando.

Si la tendencia ▼ del producto líder es muy fuerte (-30% o más) → revisá si vale la pena la cantidad solicitada.

### 4. Mix — ¿dónde está concentrado el riesgo?

Dos doughnuts:
- **Por monodroga** (top 10) — % de unidades del pedido por droga. Si el 60% del pedido es Paracetamol, sabés que un faltante de Paracetamol te corta la mitad de tu disponibilidad.
- **Por laboratorio** — útil cuando el pedido es multi-lab (vía droguería con varios fabricantes).

### 5. Estacionalidad — ¿estoy comprando en el momento correcto?

Bar chart con la suma de unidades vendidas mes a mes (últimos 12) de **todos los productos del pedido**. Te dice si la mezcla del pedido es estacional:
- Picos en mayo–julio → drogas de invierno (gripe, antialérgicos, vitamina C).
- Picos en verano → fotoprotectores, antidiarreicos.
- Plano → productos de uso continuo.

Si comprás en un valle pronunciado, considerá comprar menos.

## Sub-modal: Comparar otros labs

En la pestaña Cobertura o Top productos, click en el botón violeta **"⇄ Comparar otros labs"** de cualquier producto con monodroga.

Abre un sub-modal mostrando **todos los productos de la misma droga**, agrupados por laboratorio:

- La fila del producto que estás pidiendo aparece **resaltada en amber** con badge **"👉 PEDIDO"**.
- El resto de las opciones (otros labs) abajo, ordenadas por cantidad de productos del lab.
- Por cada producto: stock, días, uni 3m, uni 12m, $ 12m, $/envase, tendencia.
- Items con badge **"BAJA"** (descontinuados en ObServer) atenuados.

Te permite responder al toque: ¿este Tafirol de Genomma que pedí, hay un Geniol de Elea más barato y con más stock?

## Card de resumen (arriba del modal)

Antes de las pestañas, ves 4 mini-cards:
- **Items**: cantidad de productos del pedido.
- **Con datos ObServer**: cuántos están bridgeados + %. Si hay pendientes, botón **"🔗 Vincular N items"**.
- **Unidades pedidas**: total.
- **Críticos / Sobre-stock**: cuántos pasan los umbrales rojos.

## Términos importantes

- [Stock dormido](../glosario.md#stock-dormido)
- [Monodroga](../glosario.md#monodroga)
- [Rotación](../glosario.md#rotación)
- [EAN](../glosario.md#ean)

## Atajos

- Click en cualquier fila de Cobertura con monodroga → abre alternativas.
- El modal se reabre conservando la pestaña activa entre sesiones (mientras no cierres el browser).
