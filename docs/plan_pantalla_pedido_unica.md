# Plan — Pantalla de pedido única, dirigida por config

_Creado: 2026-05-20. Estado: OBJETIVO (no urgente). Origen: boceto "Config pedido" del usuario._

## Idea

Hoy hay **4 pantallas** que arman/sugieren pedidos, con lógica solapada y
divergente:

| Pantalla | Ruta | Caso de uso | Base de demanda |
|---|---|---|---|
| Armar pedido día | `/compras/dia/armar` ([compras_dia.py:376]) | Reposición táctica | u3m (ritmo reciente) |
| Pedido auto por lab | `/informes/pedido-auto` ([informes.py:1542]) | Productos del lab bajo mínimo | u12m |
| Pedido prueba | `/pedido/prueba` ([pedido_prueba.py]) | Planificación grande estacional | u12m × índice |
| Selector lab | `/compras/laboratorio` ([compras_dia.py:257]) | Elegir lab → armar | — |

El objetivo: **UNA pantalla configurable** que las absorba como casos
particulares de una misma `TipoPedidoConfig`. El operador elige el "tipo de
pedido" y la pantalla se adapta (qué pide, qué filtra, qué cálculo aplica, qué
columnas muestra).

## Esquema de configuración (extender `TipoPedidoConfig`)

`TipoPedidoConfig` ya existe (categoria `pedido`, `config_json`). Sumar las
flags s/n del boceto:

```jsonc
{
  // Proveedor / canal
  "proveedor_tipo": "laboratorio" | "drogueria" | "ambos",
  "pide_canal": true,                // muestra selector de canal de compra
  "usa_canal_plantillas": true,      // el canal define qué plantilla de export
  // Cobertura / matrices
  "pide_dias_cobertura": true,       // slider "cubrir por N días"
  "usa_matriz_lab_drog": true,       // LaboratorioDrogueria para asignar drog
  "usa_tabla_horarios": true,        // cronograma de cierres → factor_h
  // Motor de cálculo (modificadores de a_pedir)
  "chequea_modulos": true,           // packs / módulos de descuento
  "chequea_oferta_min": true,        // OfertaMinimo.unidades_minima  ← GAP HOY
  "usa_estacionalidad_droga": false, // índice estacional por droga ← GAP HOY
  "usa_estacionalidad_producto": false,
  // (ya existentes) piso_ideal, target_horizonte, buffer_pct, universo,
  //                 override_producto, redondeo, dias_cobertura_fijo
}
```

## Anatomía de la pantalla (del boceto)

1. **Encabezado**: sync al principio · UX móvil (`hide_sidebar`) · indicador
   Local/Render. Igual al de `compras_dia_armar.html`.
2. **Filtros de cabecera**: laboratorio · droguería · producto (tokenizado) ·
   droga (monodroga) · rubro · solo venta libre · solo sugerencia (subir/bajar)
   · botones "Libres a <drog>". (Ya existen en `compras_dia_armar.html`.)
3. **Acciones**: "+ Agregar producto" · gráfico anual + gráfico mensual
   (panel dual) · export con plantillas.
4. **Columnas**: producto (+ chip de flag) · laboratorio · card droguería ·
   EAN · PVP · oferta · stock · mín · vtas ayer · vtas semana · pendientes ·
   a pedir · acciones. (Textualmente las de `compras_dia_armar`.)

## Dependencias críticas (NO arrancar sin esto)

1. **Source of truth de métricas** — `services/producto_metrics.py` (HECHO
   2026-05-20). Todas las columnas y gráficos de stock/mín/prom/cobertura
   DEBEN salir de ahí, sino vuelve la divergencia que arreglamos.
2. **Gap oferta-min + estacionalidad en el motor** — ver
   `docs/plan_unificar_metricas_producto.md` y la entrada de backlog
   "planificadores deben respetar unidades_minima y cantidad_reposicion_fija".
   La config `chequea_oferta_min` / `usa_estacionalidad_*` solo tiene sentido
   si el motor (`services/calculo_pedido.py` + `services/pedido_estacional.py`)
   las respeta. **Cerrar ese gap es prerequisito.**

## Enfoque de migración (incremental, sin big-bang)

1. Definir el set completo de flags en `TipoPedidoConfig` + seeds para los 4
   tipos actuales (REPOSICION, COMPRA_LAB, PEDIDO_AUTO, PRUEBA_ESTACIONAL) que
   reproduzcan EXACTO el comportamiento de cada pantalla hoy.
2. Construir la pantalla nueva (`/pedido/armar?tipo=<slug>`) que lee la config
   y arma el contexto. Reutiliza el template de `compras_dia_armar` como base
   (es el más completo).
3. Migrar UNA pantalla a la vez detrás de la config, verificando paridad de
   números contra la vieja antes de redirigir. Empezar por `/compras/laboratorio`
   (la más simple) o `/informes/pedido-auto`.
4. Cuando las 4 estén cubiertas y verificadas → las rutas viejas redirigen a la
   nueva con su `?tipo=`. Borrar templates/lógica duplicada.

## Riesgos

- Las 4 pantallas tienen diferencias sutiles (orden, agrupaciones, exports).
  La paridad hay que verificarla número por número, no a ojo.
- Es un refactor grande: hacerlo solo cuando el gap de cálculo esté cerrado y
  el equipo tenga margen. No urgente.

## Beneficio

- Una sola pantalla = un solo lugar donde arreglar bugs, agregar columnas,
  cambiar el motor. Hoy un fix hay que replicarlo en 4 lados (ej. el chip de
  flag lo tuvimos que poner en 3 pantallas distintas).
- La config explícita documenta las diferencias entre tipos de pedido en datos,
  no en código disperso.
