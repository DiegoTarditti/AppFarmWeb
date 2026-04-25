# Admin: Vincular productos a ObServer

El **bridge** entre `productos.observer_id` (tabla local con EAN) y `obs_productos.observer_id` (catálogo ObServer indexado por código alfabeta) es lo que permite que el sistema cruce datos entre el catálogo local y los datos de ventas/stock de ObServer.

Cuando este bridge falla, los pedidos aparecen "sin link a ObServer" y los Indicadores no pueden mostrar datos históricos.

## Por qué hace falta el bridge

| Tabla | Indexa por | Tiene |
|---|---|---|
| `productos` (local) | EAN (codigo_barra) | EAN, descripción, precio_pvp, lab |
| `obs_productos` (espejo) | IdProducto numérico | descripción, lab, monodroga, codigo_alfabeta (NO EAN) |

ObServer no maneja EAN. Solo código alfabeta. Para cruzar:
1. Subimos una factura → se crea `Producto` local con EAN del PDF.
2. Necesitamos asignarle `productos.observer_id` (= IdProducto de ObServer).
3. **Cómo encontramos ese mapeo**: por descripción + laboratorio.

Una vez bridgeado, podemos hacer queries tipo "cuántas unidades vendí de este EAN en los últimos 12m" que mezclan datos locales con `obs_ventas_mensuales`.

## Estrategia de matching

El sistema usa varias estrategias en cascada:

### 1. Atajo numérico (pedidos generados desde ObServer)
Si el pedido se generó desde `/observer/analizar`, el `codigo_barra` que se guarda en `PedidoItem` es directamente el `IdProducto` de ObServer convertido a string. El bridge se hace al toque sin necesidad de matching.

### 2. Bridge por código alfabeta
Si la tabla `productos` tiene `codigo_alfabeta` cargado (algunos parsers lo extraen) → match directo contra `obs_productos.codigo_alfabeta`.

### 3. Fallback por descripción + laboratorio (el más común)
Para cada `Producto` sin `observer_id`:
- Busca el laboratorio del producto en `obs_laboratorios` por nombre fuzzy.
- Filtra `obs_productos` a los del lab encontrado (~300-500 productos).
- Normaliza la descripción del producto local (lowercase, sin acentos, sin puntuación) y compara contra `obs_productos.descripcion` normalizado.
- **Match exacto** → bridge directo.
- **Match por superset** (todos los tokens del local están en el de ObServer).
- **Match por overlap ≥ 80%** de tokens.
- **Ambiguo** (varios candidatos con score similar) → no setea, queda sin link.

## Cómo ejecutarlo

### Desde el modal Indicadores (un pedido)
- Abrís Indicadores de un pedido en `/orders`.
- Si hay items "Sin link a ObServer" en la pestaña Riesgos → banner violeta con botón **"🔗 Vincular ahora"**.
- Click → matchea y devuelve stats: linkeados / ya linkeados / ambiguos / no encontrados.

### Desde el resumen del modal (cualquier pedido)
- Card "Con datos ObServer" tiene botón **"🔗 Vincular N items"** si hay pendientes, o **"🔗 Re-vincular"** siempre por si querés reprocesar.

### Endpoint API
- `POST /api/pedido/<id>/vincular-observer` — procesa solo ese pedido.

### CLI (todos los pedidos de una vez)
```bash
docker-compose exec web python scripts/vincular_pedido_observer.py        # todos
docker-compose exec web python scripts/vincular_pedido_observer.py 12     # uno solo
docker-compose exec web python scripts/vincular_pedido_observer.py --dry  # sin escribir
```

Reporta por cada pedido: linkeados, ya linkeados, ambiguos, no encontrados, errores.

### Auto-matcher general (no por pedido)
Desde `/admin/observer-sync`, botón **"Auto-match productos"**:
- Recorre TODOS los `Producto` locales sin `observer_id`.
- Aplica bridge por descripción + lab, threshold configurable (default 0.80).
- Devuelve stats: exact / fuzzy / sin_match / ambiguos / sin_lab.

## Idempotencia

Todos los métodos saltan items que **ya tienen** `observer_id` seteado. Podés correr cuantas veces quieras sin riesgo.

## Resultados típicos

Para un pedido Roemmers (~200 items):
- Linkeados: 195-200.
- Ambiguos: 0-5 (productos con nombre normalizado igual en obs_productos, distinguibles por presentación).
- No encontrados: 0-10 (típicamente nutricionales / específicos que ObServer indexa bajo otro lab).

## Casos donde no se puede linkear

- **Productos nutricionales** (Sancor Bebe, Glucerna, Pediasure) — ObServer los indexa bajo "Sancor", "Abbott", etc., no bajo el lab que figura en tu pedido.
- **Productos descontinuados** en ObServer pero todavía en tu catálogo local — están con `fecha_baja` set.
- **Productos sin lab cargado en local** — el bridge necesita el lab para filtrar.

Para los ambiguos, hay una pantalla manual `/productos/sin-vincular` con candidatos y botón para confirmar uno a uno.

## Términos importantes

- [EAN](../glosario.md#ean)
- [Alfabeta](../glosario.md#alfabeta)
- [IdProducto](../glosario.md#idproducto)
