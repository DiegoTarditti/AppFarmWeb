# Plan — Motor de pantallas de pedido dirigido por config (fábrica)

_Creado: 2026-05-20. Reencuadrado 2026-05-20. Estado: OBJETIVO (no urgente)._
_Origen: boceto "Config pedido" del usuario._

## Idea (encuadre correcto)

**NO** es una sola pantalla gigante con mil toggles en runtime. Es un **motor
que genera pantallas**: definís una config (una fila en `TipoPedidoConfig`) y el
motor te arma una tabla/pantalla distinta con ese comportamiento. Una fábrica:
**un motor, N instancias configuradas**, cada una limpia y enfocada.

- Pantalla nueva = config nueva, **no código nuevo**.
- El motor (builder de filas, métricas, cálculo, filtros, gráficos) vive en UN
  lugar. Las pantallas son instancias parametrizadas.
- No tiene el problema del "god-screen": cada instancia es su propia vista
  acotada; no hay un `if` central decidiendo todo. Hay un *assembler* que arma
  LA pantalla pedida.

Hoy hay **4 pantallas** que arman/sugieren pedidos, con lógica solapada y
divergente — son el *ground truth* del que se extrae el motor:

| Pantalla | Ruta | Caso de uso | Base de demanda |
|---|---|---|---|
| Armar pedido día | `/compras/dia/armar` | Reposición táctica | u3m (ritmo reciente) |
| Pedido auto por lab | `/informes/pedido-auto` | Productos del lab bajo mínimo | u12m |
| Pedido prueba | `/pedido/prueba` | Planificación grande estacional | u12m × índice |
| Selector lab | `/compras/laboratorio` | Elegir lab → armar | — |

## Principio rector (la condición para que NO se vuelva en contra)

**Construir el motor DESDE las 4 pantallas reales, no antes.** El error clásico
de los sistemas config-driven es diseñar la abstracción en el vacío y que la
realidad no entre → terminás con "config + excepciones hardcodeadas" (lo peor de
los dos mundos).

Regla: si una pantalla tiene un quirk que la config no expresa, **es señal de que
falta un eje de config**, no de hardcodear la excepción.

## Esquema de configuración (extender `TipoPedidoConfig`)

`TipoPedidoConfig` ya existe (categoria `pedido`, `config_json`). Cada fila =
una pantalla. Ejes de config (derivados de lo que REALMENTE varía entre las 4):

```jsonc
{
  // Proveedor / canal
  "proveedor_tipo": "laboratorio" | "drogueria" | "ambos",
  "pide_canal": true,
  "usa_canal_plantillas": true,
  // Cobertura / matrices
  "pide_dias_cobertura": true,
  "usa_matriz_lab_drog": true,
  "usa_tabla_horarios": true,
  // Base de demanda (lo que más varía entre pantallas)
  "base_demanda": "u3m" | "u12m" | "u12m_estacional",
  // Motor de cálculo (modificadores de a_pedir)
  "chequea_modulos": true,
  "chequea_oferta_min": true,        // OfertaMinimo.unidades_minima  ← GAP HOY
  "usa_estacionalidad_droga": false, // ← GAP HOY
  "usa_estacionalidad_producto": false,
  // Columnas a mostrar (lista; el motor renderiza solo esas)
  "columnas": ["producto", "lab", "drog", "ean", "pvp", "oferta", "stock",
               "min", "vtas_ayer", "vtas_semana", "pendientes", "a_pedir"],
  // (ya existentes) piso_ideal, target_horizonte, buffer_pct, universo,
  //                 override_producto, redondeo, dias_cobertura_fijo
}
```

## Arquitectura del motor (componentes compartidos)

El motor se compone de piezas reutilizables — varias ya existen o están a medio
camino. La idea es extraer y consolidar, NO reescribir:

| Pieza | Estado | Dónde vive / debería vivir |
|---|---|---|
| Métricas (stock/min/prom/rotación) | ✅ HECHO | `services/producto_metrics.py` |
| Builder de filas (universo + `a_pedir`) | parcial, duplicado | extraer a `services/pedido_builder.py` |
| Cálculo de `a_pedir` (modificadores) | parcial | `services/calculo_pedido.py` (+ gap oferta-min/estacionalidad) |
| Chip de flag | duplicado en 3 templates | partial `_chip_flag.html` |
| Filtros de cabecera | en `compras_dia_armar.html` | partial `_filtros_pedido.html` |
| Panel de gráficos | ✅ partial | `_grafico_dual_panel.html` |
| Render de columnas | hardcodeado | tabla data-driven desde `config.columnas` |

## Enfoque de migración (incremental, sin big-bang)

1. **Extraer componentes** de las 4 pantallas reales a piezas compartidas
   (empezando por las más duplicadas: builder de filas + chip de flag + filtros).
   Las 4 pantallas pasan a ser wrappers finos que usan esas piezas. Ya se puede
   hacer SIN el motor todavía — y solo con esto se mata el 80% de la duplicación.
2. **Agregar la capa de assembler**: un endpoint `/pedido/armar?tipo=<slug>` que
   lee la `TipoPedidoConfig` y ensambla la pantalla con las piezas según los ejes
   de config (base_demanda, columnas, flags de motor, etc.).
3. **Seeds para los 4 tipos actuales** (REPOSICION, COMPRA_LAB, PEDIDO_AUTO,
   PRUEBA_ESTACIONAL) que reproduzcan EXACTO el comportamiento de hoy.
4. Migrar UNA pantalla a la vez detrás del motor, verificando **paridad de
   números** contra la vieja antes de redirigir. Empezar por la más simple.
5. Cuando las 4 estén cubiertas y verificadas → rutas viejas redirigen a
   `?tipo=`. A partir de ahí, pantalla nueva = config nueva.

## Dependencias críticas (NO arrancar el motor sin esto)

1. **Source of truth de métricas** — `services/producto_metrics.py` (HECHO
   2026-05-20). Todas las columnas/gráficos salen de ahí, sino vuelve la
   divergencia que arreglamos.
2. **Gap oferta-min + estacionalidad en el motor de cálculo** — los flags
   `chequea_oferta_min` / `usa_estacionalidad_*` solo tienen sentido si
   `services/calculo_pedido.py` + `services/pedido_estacional.py` los respetan.
   Ver entrada de backlog "planificadores deben respetar unidades_minima y
   cantidad_reposicion_fija". **Cerrar ese gap es prerequisito.**

## Riesgos

- **Abstracción prematura**: mitigado por el principio rector (extraer desde lo
  real, no diseñar en el vacío). Si no entra un caso → falta eje de config.
- **Paridad**: las 4 pantallas tienen diferencias sutiles (orden, agrupaciones,
  exports). Verificar número por número, no a ojo.
- Es trabajo grande: hacerlo cuando el gap de cálculo esté cerrado y haya margen.
  No urgente.

## Beneficio

- **Un solo lugar** donde arreglar bugs / agregar columnas / cambiar el motor.
  Hoy un fix se replica en 4 lados (el chip de flag lo pusimos en 3 pantallas; la
  divergencia de métricas fue "lo mismo calculado distinto en 2 lados").
- **Escala a más tipos de pedido sin codear** cada uno (ej. "pedido urgente
  finde", "compra trimestral por lab" = filas de config).
- Las diferencias entre tipos quedan **explícitas en datos**, no enterradas en
  código disperso.

## Camino corto (lo accionable ya)

Aunque el motor completo es a futuro, el **paso 1 (extraer componentes)** se
puede hacer cuando quieras y rinde solo: mata la duplicación actual sin construir
nada nuevo. El motor es la cereza encima.
