# Boceto — Ficha 360 del producto (centro de comando)

> Estado: **diseño**, sin implementar. Para discutir antes de codear.
> Idea: el buscador multi-token de `/productos/presentaciones` es excelente.
> En vez de una pantalla por cosa, lo convertimos en el **punto de entrada único**
> a todo lo configurable de un producto: un buscador arriba → ficha del producto
> elegido → **pestañas** para cada acción.

## UX

```
🔍 [ buscar producto por nombre... ]        ← un solo buscador (el que ya existe)

┌─ ALIKAL sobre x12 · 7790… · Roemmers ───────────────── [×] ┐
│  [ 📦 Presentación ] [ 🎁 Oferta ] [ 🔁 Repo fija ] [ 📦 Pack ] │
│  ───────────────────────────────────────────────────────────│
│  (panel de la pestaña activa)                                 │
└────────────────────────────────────────────────────────────────┘
```

- **Mismo buscador y misma ficha** para las 4 pestañas (no se repite nada).
- La pantalla deja de llamarse "Presentaciones" → **"Configurar producto"** (o "Ficha de producto").

## Pestañas y backend (lo que se reutiliza)

| Tab | Qué configura | Storage | Endpoint |
|---|---|---|---|
| **A · Presentación** | fraccionado + cantidad por envase + equivalencia Kellerhoff | `Producto.fraccionado`, `ProductoAtributo.cantidad_envase` | `/api/producto/presentacion` GET/POST — **ya existe** |
| **B · Oferta** | % descuento + cantidad mínima (1=simple, >1=con mín) — lab derivado del producto | `OfertaMinimo` | **nuevo** `/api/producto/oferta` (upsert de UNA por EAN; lab del producto) |
| **C · Repo fija** | cantidad de reposición fija (override del motor de pedidos) | `Producto.cantidad_reposicion_fija` | `/producto/<id>/field` (field=`cantidad_reposicion_fija`) — **ya existe** |
| **D · Pack** | marcar como pack (+ opcional: unidades por pack) | `Producto.es_pack` (+ `PackEquivalencia`) | `/producto/<id>/field` (field=`es_pack`) — **ya existe**; equivalencia = a definir |

→ Solo **B** necesita endpoint nuevo. A/C/D reutilizan lo que hay.

## Detalles / decisiones

1. **Producto en master:** C y D viven en `Producto` (master). Si el producto solo está en ObServer (no catalogado), hay que catalogarlo primero — la pantalla **ya tiene** ese flujo ("Catalogar y configurar"). Lo reusamos para las 4 pestañas.
2. **id vs ean:** las pestañas trabajan por EAN (lo que da el buscador). C/D usan `/producto/<id>/field` (por id) → al elegir el producto resolvemos su `producto.id` una vez (el GET de presentación ya trae los datos; sumamos el id) y lo usamos para C/D.
3. **B · Oferta — laboratorio:** se **deriva del producto** (un producto = un solo lab, invariante del proyecto). No hay picker: se muestra el lab del producto como dato. Al abrir la pestaña, si ya existe oferta de ese producto, se precarga para editar.
4. **D · Pack — alcance:** ¿solo el toggle `es_pack`, o también "cuántas unidades trae el pack" (`PackEquivalencia`)? Empezar con el toggle; la equivalencia se suma si hace falta.
5. **Fix del modo lote:** de paso, arreglar el desplegable del buscador — al tildar varios (lote), la barra "Aplicar a los tildados" queda tapada por la lista. Solución: pie de acción **dentro** del desplegable ("N seleccionados → [Aplicar]"), sin tener que clickear afuera.

## Lista de abajo

Hoy lista "productos con presentación". Se puede dejar igual, o más adelante hacerla
filtrable por qué tienen configurado (presentación / oferta / repo fija / pack).
Fuera de alcance ahora.

## Plan incremental

1. Barra de pestañas en `#pr-config` + toggle JS. Lo actual entra en la pestaña Presentación.
2. Pestaña **Repo fija** (C) — input + guardar vía `/producto/<id>/field`. (La más fácil, valida el patrón.)
3. Pestaña **Pack** (D) — toggle `es_pack` vía `/producto/<id>/field`.
4. Pestaña **Oferta** (B) — picker lab + %desc + cant mín + endpoint nuevo `/api/producto/oferta`.
5. Fix del modo lote (pie de acción en el desplegable).
6. Rename de la pantalla a "Configurar producto".
