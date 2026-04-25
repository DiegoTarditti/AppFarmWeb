# Admin: Vincular productos a ObServer

> ⚠ STATUS: PENDIENTE

## Para qué sirve

Establecer el bridge `productos.observer_id` ↔ `obs_productos.observer_id` para que análisis, indicadores y comparaciones puedan cruzar datos entre el catálogo local (con EAN) y ObServer (que indexa por código alfabeta).

## Estrategia de matching

1. **Atajo numérico** — pedidos generados desde ObServer usan `IdProducto` como string en `codigo_barra`, se matchea directo.
2. **Bridge por alfabeta** — si `productos.codigo_alfabeta` existe → match contra `obs_productos.codigo_alfabeta`.
3. **Fallback por descripción + laboratorio** — fuzzy match dentro del catálogo del lab.

## Cómo correr

### Por pedido (UI)

`/orders` → Indicadores → botón "🔗 Vincular ahora" en pestaña Riesgos o "🔗 Vincular N items" en el resumen. Procesa solo el pedido actual.

### Masivo (CLI)

```
docker-compose exec web python scripts/vincular_pedido_observer.py            # todos
docker-compose exec web python scripts/vincular_pedido_observer.py 12         # uno
docker-compose exec web python scripts/vincular_pedido_observer.py --dry      # sin escribir
```

## Resultados típicos

Para un pedido Roemmers (~200 items): 199 linkeados, 2 ambiguos, 0 no encontrados.

Casos típicos sin link: nutricionales (Sancor Bebe, Glucerna) que ObServer indexa bajo otro lab.

## Idempotencia

El script salta items que ya tienen `observer_id` seteado. Se puede correr cuantas veces se quiera sin riesgo.
