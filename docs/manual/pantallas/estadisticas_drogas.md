# Pantalla: Estadísticas por monodroga

Análisis macro del catálogo entero por monodroga. Sirve para responder: ¿qué drogas se venden más? ¿quién las vende? ¿conviene cambiar de laboratorio para alguna droga clave?

**Acceso**: card violeta **"Estadísticas por droga"** en el home, o `/estadisticas/drogas`.

## Vista principal

Tabla agregada por droga con estas columnas:

| Columna | Significa |
|---|---|
| Monodroga | Nombre de la droga |
| **Labs** | Cuántos laboratorios la fabrican (en el catálogo de tu farmacia) |
| **Productos** | Cuántas presentaciones distintas hay |
| **Uni 3m** | Unidades vendidas en últimos 3 meses (verde) |
| **Uni 12m** | Unidades vendidas en últimos 12 meses |
| **$ 12m** | Monto facturado en 12 meses |

Default ordenado por **Uni 12m descendente** (las drogas que más se venden arriba).

## Banner de frescura de datos

En el header arriba a la derecha, un badge muestra cuán frescos son los datos:

- 🟢 **"● Datos al [fecha] (Xh)"** — actualizados en las últimas 24 horas.
- 🟡 **"⚠ Datos del [fecha] (Xh atrás)"** — entre 24-72h, conviene refrescar.
- 🔴 **"⚠ Datos del [fecha] (Xh atrás)"** — > 72h, datos viejos.
- ⚪ **"🔄 Calculado en vivo"** — la vista materializada nunca se refrescó, se hace el JOIN al vuelo.

Si sos `admin` o `dev`: botón **"🔄 Refrescar ahora"** que recalcula todo en ~1 segundo.

## Filtros

- **Búsqueda**: por nombre de droga (parcial, case-insensitive).
- Paginación de 40 por página.

## Drill-down (click en chevron ▶)

Click en el ícono al lado del nombre de la droga → expande la fila mostrando:

- **Mini doughnut** arriba con share de mercado (% de unidades 12m por lab).
- **Cards por laboratorio** debajo, cada una con:
  - Checkbox para selección.
  - Lista de productos (descripción, stock, uni 3m, uni 12m).
- Botón **"Comparar seleccionados (N)"** que aparece cuando hay 2+ labs tildados.

## Modal de comparación de labs (5 pestañas)

Click en el botón gráfico 📊 de una droga, o tildá 2+ labs en el drill-down y apretá Comparar.

### Resumen
- Cards por lab con todos los números clave (productos, stock, uni 3m/12m, $ 12m, $/envase, $/unidad de contenido).
- **Doughnut de share de mercado** — % de unidades 12m de cada lab dentro de la droga.

### Tendencia
- **Momentum**: gráfico mostrando uni 12m real vs anualizado (uni_3m × 4). Verde si supera, rojo si cae. Chips abajo con ▲/▼ y % por lab.
- **Evolución mensual**: line chart con las unidades mes a mes de cada lab.
- **Estacionalidad heatmap**: tabla 12 meses × labs con intensidad de color.

### Stock
- **Días de stock** por lab (bar horizontal con colores rojo/ámbar/verde/sky).
- **Stock total + nº productos** por lab.

### Ventas
- Bar chart de unidades 3m vs 12m.
- Bar chart de monto 3m vs 12m.
- Bar chart de precio promedio (envase + unidad de contenido).
- **Scatter precio vs volumen**: cada lab un punto. Detecta estrategia premium/commodity/nicho.

### Productos
- **Top 5 por lab** con $ 12m, $/envase, $/uni de contenido y badge ▼ verde / ▲ rojo comparando contra promedio de los otros labs.

## Performance

La pantalla lee de la **vista materializada `mv_stats_drogas`** que pre-calcula los agregados → respuesta < 50ms aunque haya cientos de miles de filas en `obs_ventas_mensuales`.

La vista se refresca automáticamente después de cada `push_obs_to_render.py` (cuando el DockerPanel cron termina el sync). También podés forzar el refresh manualmente con el botón "🔄 Refrescar ahora" (admin/dev).

## Atajos

- Click en monodroga desde **`/obs/productos`** (columna Monodroga) → te lleva a `/estadisticas/drogas?q=<nombre>` con filtro pre-aplicado.
- Productos marcados **"BAJA"** en ObServer aparecen igual con badge gris + opacidad reducida.

## Términos importantes

- [Monodroga](../glosario.md#monodroga)
- [Stock dormido](../glosario.md#stock-dormido)
