# Alembic — review del baseline (tracking por lotes)

Doc vivo para trackear progreso de la revisión del baseline migration
(paso 2 de la adopción de Alembic).

Plan general: ver `docs/mejoras_pendientes.md` → "Adoptar Alembic para migraciones".

## Estado global

- **Total tablas a revisar**: 93
- **Lotes**: 10 (de ~10 tablas cada uno)
- **Revisadas**: 93 / 93 ✅ COMPLETO (Lotes 1-3 ✅ 2026-05-29 · Lotes 4-10 ✅ 2026-06-02)

> ✅ **Merge de `main` HECHO** (2026-06-02, commit de merge en esta branch). Resolvió
> la staleness de `pack_equivalencias` (`laboratorio_id`+`fuente`+FK ya en el modelo).
> Post-merge se **re-revisó `pack_equivalencias`** (main la había tocado tras el lote 3):
> `fuente` → `server_default='aprendido'`; `laboratorio_id` → índice **parcial**
> `idx_pack_equiv_lab` (`postgresql_where=text('laboratorio_id IS NOT NULL')`), quitado
> `index=True`. **Resultado: el diff de autogenerate quedó 100% en drops de `ix_*`
> redundantes (39 ops, todas cleanup) — 0 ops estructurales.**

## Convención por tabla

Por cada tabla, en cada lote revisamos:

1. ✅ **Columnas matchean**: nombre, tipo, nullable, default (server_default).
2. ✅ **Primary key correcta**.
3. ✅ **Foreign keys con `name=` explícito** (sino Alembic les pone nombre auto frágil).
4. ✅ **UniqueConstraints con `name=`** (idem).
5. ✅ **CheckConstraints**: si hay, con `name=`.
6. ✅ **Indexes**: que correspondan (compuestos, parciales, índice por FK, etc).
7. ⚠ **Drift Render vs Local**: si hay (ver `schema_diff_pieri.md`), decidir qué versión es "la verdad".
8. ⚠ **server_default vs default Python**: detectar el caso de `default=now_ar` y similares — el server_default puede no detectarse y el comportamiento difiere.

Cuando una tabla está OK, marcala con ✅ + fecha. Si hay un issue, anotalo en la columna "Notas".

---

## LOTE 1 — Core / Config / Infra (10) ✅ 2026-05-29

| # | Tabla | Estado | Notas |
|---|---|---|---|
| 1 | `alarmas_notificadas` | ✅ | Agregado `server_default='0'` en `count_total`. |
| 2 | `backup_log` | ✅ | Solo `id` con nextval (sequence implícita, false positive del compare). |
| 3 | `configuracion` | ✅ | Agregado `server_default=` a 8 columnas: `backup_hora`/`_diarios_max`/`_semanales_max`/`_quincenales_max`/`_mensuales_max`/`observer_ventas_meses`/`transfer_excedente_meses`/`transfer_necesita_meses`. |
| 4 | `cron_log` | ✅ | Solo `id` con nextval. |
| 5 | `farmacias` | ✅ | Solo `id` con nextval. |
| 6 | `mv_refresh_log` | ✅ | Solo `id` con nextval. |
| 7 | `panel_comandos` | ✅ | Agregado `server_default='pendiente'` en `estado`, `server_default=func.now()` en `solicitado_en`, e `Index('idx_panel_comandos_estado', 'estado', 'solicitado_en')`. |
| 8 | `panel_heartbeat` | ✅ | OK sin cambios. |
| 9 | `sucursales` | ✅ | Solo `id` con nextval. |
| 10 | `sync_lock` | ✅ | Solo `id` con nextval. |

**Cambios en `database.py`**:
- Import: agregado `Index, func` al `from sqlalchemy import (...)`.
- 11 `server_default=` agregados.
- 1 `__table_args__` con `Index` compuesto en `panel_comandos`.

**Patrón identificado**: cuando una columna se agregó con `_pg_add_columns` que incluye `DEFAULT X`, el modelo NO refleja el `server_default=` Python-side. Hay que ir agregándolo donde corresponda.

**False positives a ignorar globalmente**: las columnas `id` PK con `nextval('*_id_seq'::regclass)` son sequences implícitas de `Column(Integer, primary_key=True)`. Alembic `compare_server_default=True` las flagea pero son inofensivas. Considerar agregar `include_object` filter en `env.py` para suprimirlas (TODO sesión futura).

**Verificación**: re-corrida de `alembic revision --autogenerate` después de los cambios → 0 referencias a estas 10 tablas en el diff. Baseline pasó de 282 a 192 líneas (-32%).

## LOTE 2 — Auth / Users / Análisis (9) ✅ 2026-05-29

| # | Tabla | Estado | Notas |
|---|---|---|---|
| 1 | `usuarios` | ✅ | OK sin cambios. |
| 2 | `usuario_farmacias` | ✅ | OK sin cambios. |
| 3 | `usuarios_pedidos` | ✅ | OK sin cambios. |
| 4 | `home_card_clicks` | ✅ | Agregado `Index('idx_hcc_user','usuario_id', text('clicked_at DESC'))` + `Index('idx_hcc_card','card_id')`. |
| 5 | `analisis_sesiones` | ✅ | OK sin cambios. |
| 6 | `analisis_ia_cache` | ✅ | Agregado `server_default=func.now()` en `creado_en`. |
| 7 | `archivos_compartidos` | ✅ | Agregado `server_default='todos'` en `destinatarios`. |
| 8 | `compartido_importado` | ⚠ Drift Render | Defer: drift de Render (≠DEFAULT `accion`+`creado_en`). Acá local matchea modelo, decisión se posterga a sesión de reconciliación. |
| 9 | `obs_sync_log` | ✅ | Agregado `Index('idx_obs_sync_entidad','entidad', text('ejecutado_en DESC'))`. |

**Cambios en `database.py`**:
- 3 `server_default=` agregados (`analisis_ia_cache.creado_en`, `archivos_compartidos.destinatarios`, ya sumados antes).
- 2 `__table_args__` con `Index` agregados (`home_card_clicks` con 2 indexes; `obs_sync_log` con 1).

**Patrón identificado**: índices custom con `ORDER BY column DESC` se modelan con `text('column DESC')` en `Index(...)`. SQLAlchemy NO los expresa con `Column.desc()` por orden de inicialización (la clase Column todavía no está disponible cuando se evalúa `__table_args__`).

**Verificación**: baseline post-cambios 170 líneas (-40% del original 282), 6 `alter_column` restantes, 0 referencias a estas 9 tablas en el diff.

## LOTE 3 — Productos local + identificadores (10) ✅ 2026-05-29

| # | Tabla | Estado | Notas |
|---|---|---|---|
| 1 | `productos` | ✅ | 5 indexes + 2 partial (`idx_productos_alt1/2/3/alfabeta/observer_id` + `uq_productos_observer_id WHERE NOT NULL` + `idx_prod_no_pedir WHERE no_pedir=true`). Removido `index=True` y `unique=True` de Columns para evitar `ix_*` duplicados. |
| 2 | `producto_codigos_barra` | ✅ | `idx_pcb_producto`, `idx_pcb_codigo`, `uq_pcb_producto_codigo`. |
| 3 | `producto_atributos` | ✅ | 4 indexes: `idx_atributos_droga/conc/forma/fuente`. |
| 4 | `producto_flags` | ✅ | OK sin cambios. |
| 5 | `producto_precios_hist` | ✅ | 3 indexes: `idx_precios_codigo_barra/proveedor/fecha`. |
| 6 | `productos_pendientes_revision` | ✅ | `idx_pend_rev_supplier`, `idx_pend_rev_estado` (compound), `idx_pend_rev_llm WHERE llm_analizado_en IS NULL` (partial). |
| 7 | `product_analytics` | ✅ | OK sin cambios. |
| 8 | `barcode_mappings` | ✅ | OK sin cambios. |
| 9 | `equivalencias_proveedor` | ✅ | 5 indexes incl. 3 partial uniques con `postgresql_where`: `uq_equiv_drog_codigo`/`drog_desc`/`lab_codigo` y 2 regulares (`idx_equiv_codigo/drog`). |
| 10 | `pack_equivalencias` | ✅ | OK sin cambios. |

**Cambios en `database.py`** (lote más grande hasta ahora):
- **26 indexes** agregados via `__table_args__` en 6 clases.
- 8 `index=True` y 2 `unique=True` REMOVIDOS de Column inline para evitar que SQLAlchemy genere `ix_*` y `*_key` duplicados de los `idx_*` custom.

**Patrón identificado** (importante para próximos lotes):
- Cuando una tabla tiene índice con nombre custom (`idx_*`, `uq_*`) en DDL pero el modelo declara `Column(..., index=True)`, **SQLAlchemy crea AMBOS**: `idx_*` (de inline ALTER) + `ix_*` (de `index=True`). Resultado: índices duplicados en DB.
- Fix: **quitar `index=True`/`unique=True` del Column y declarar todo en `__table_args__` con `Index(name, ..., postgresql_where=text(...))` para matchear el DDL real.

**Partial indexes**: usar `Index(name, col, postgresql_where=text('cond'))` para `WHERE` parciales. Para uniqueness condicional, sumar `unique=True`.

**Stale `ix_*` detectados (no son del lote, son cleanup)**: el baseline post-lote 3 todavía contiene drops de `ix_productos_*`, `ix_producto_atributos_*`, etc. — son los índices auto-creados por `index=True` viejo, que quedaron huérfanos al limpiar el modelo. Cuando se aplique el baseline final con `upgrade head`, estos drops serán cleanup legítimo (los `idx_*` siguen vigentes, los `ix_*` son redundantes).

**Verificación**: 160 líneas de baseline post-cambios (-43% del original 282).

## LOTE 4 — Labs / Provs / Descuentos / Cronogramas (8) ✅ 2026-06-02

| # | Tabla | Estado | Notas |
|---|---|---|---|
| 1 | `laboratorios` | ✅ | OK sin cambios (matchea, incl. uniques `nombre`/`observer_id`). |
| 2 | `proveedores` | ✅ | OK sin cambios. |
| 3 | `descuentos_base` | ✅ | Local matchea modelo (incl. `uq_desc_base_lab_drog` + ix por FK). Drift Render diferido a sesión de reconciliación. |
| 4 | `laboratorio_drogueria` | ✅ | OK sin cambios (`uq_lab_drog` + ix por FK). |
| 5 | `parser_ofertas_lab` | ✅ | Local matchea modelo (PK = `laboratorio_id`). Drift Render (2×≠DEFAULT + FK perdida) diferido. |
| 6 | `proveedor_cronograma` | ✅ | Quitado `index=True` de `partner_tipo`; declarado `Index('idx_cronograma_partner_tipo','partner_tipo')`. Dropea el `ix_*_partner_tipo` redundante (cleanup). |
| 7 | `proveedor_horarios_reparto` | ✅ | Quitado `index=True` de `proveedor_id`; declarado `Index('idx_horarios_prov','proveedor_id')`. Dropea el `ix_*_proveedor_id` redundante (cleanup). |
| 8 | `tipo_pedido_config` | ✅ | OK sin cambios (`ix_tipo_pedido_config_slug` unique). |

**Cambios en `database.py`**:
- 2 `index=True` REMOVIDOS (`proveedor_cronograma.partner_tipo`, `proveedor_horarios_reparto.proveedor_id`) — generaban `ix_*` duplicados de los `idx_*` custom del DDL.
- 2 `Index` agregados a `__table_args__` (`idx_cronograma_partner_tipo`, `idx_horarios_prov`) para matchear el DDL real.

**Patrón (mismo que lote 3)**: índice custom `idx_*` (de inline ALTER en `init_db`) + `index=True` en el mismo Column → DB tiene AMBOS (`idx_*` e `ix_*`, duplicados sobre la misma columna). Fix: quitar `index=True`, declarar el `idx_*` en `__table_args__`. El baseline dropea el `ix_*` redundante = cleanup legítimo (igual que los `ix_*` stale del lote 3).

**Verificación**: re-corrida de autogenerate → para las 8 tablas solo quedan los 2 drops de `ix_*` redundantes (legítimos); 0 referencias a `idx_cronograma_partner_tipo`/`idx_horarios_prov`. Las otras 6 tablas: 0 ops. (Los `INFO ... SERIAL ... omitting` de sequences son false-positives conocidos.)

## LOTE 5 — Pedidos / Compra (10) ✅ 2026-06-02

| # | Tabla | Estado | Notas |
|---|---|---|---|
| 1 | `pedidos` | ✅ | 3 dups (`farmacia_id`/`partner_id`/`mostrar_hasta`) → quitado `index=True`, declarados `idx_pedidos_farmacia`/`_partner_id`/`_mostrar_hasta`. Agregado compuesto `idx_pedidos_estado_creado`. `estado`/`creado_en`/`origen` conservan `index=True` (sin dup). |
| 2 | `pedido_items` | ✅ | Dup `farmacia_id` → `idx_pedido_items_farmacia`. |
| 3 | `pedido_borrador` | ✅ | 2 dups (`drogueria_id`/`observer_id`) → `idx_borrador_drog`/`_obs`. `producto_id` conserva `index=True`. |
| 4 | `pedido_emitido` | ✅ | OK sin cambios. |
| 5 | `pedido_emitido_item` | ✅ | OK sin cambios. |
| 6 | `procesos_compra` | ✅ | Dup `farmacia_id` → `idx_procesos_compra_farmacia`. |
| 7 | `ofertas_minimo` | ✅ | 2 dups (`drogueria_id`/`vigencia_hasta`) → `idx_ofertas_drog`/`_vig`. Agregado compuesto `idx_ofertas_minimo_lab_tipo`. `laboratorio_id` conserva `index=True`. |
| 8 | `modulos` | ✅ | OK sin cambios. |
| 9 | `modulo_packs` | ✅ | OK sin cambios. |
| 10 | `plantillas` | ✅ | OK sin cambios. |

**Cambios en `database.py`**:
- **8 `index=True` REMOVIDOS** (los que tenían `idx_*` custom duplicado): `pedidos.farmacia_id/partner_id/mostrar_hasta`, `pedido_items.farmacia_id`, `pedido_borrador.drogueria_id/observer_id`, `procesos_compra.farmacia_id`, `ofertas_minimo.drogueria_id/vigencia_hasta`.
- **9 `Index` agregados** vía `__table_args__` en 5 clases (7 single-col que matchean el `idx_*` custom + 2 compuestos: `idx_pedidos_estado_creado`, `idx_ofertas_minimo_lab_tipo`).
- Mismo patrón que lotes 3-4 (índice duplicado `idx_*`+`ix_*`).

**Verificación**: autogenerate post-cambios → para las 10 tablas solo quedan 8 drops de `ix_*` redundantes (cleanup legítimo); 0 referencias a cualquier `idx_*` del lote. Las 5 tablas sin dup (`pedido_emitido`, `pedido_emitido_item`, `modulos`, `modulo_packs`, `plantillas`): 0 ops.

## LOTE 6 — Facturas / Stock / Documentos (10) ✅ 2026-06-02

| # | Tabla | Estado | Notas |
|---|---|---|---|
| 1 | `facturas` | ✅ | OK sin cambios. |
| 2 | `factura_items` | ✅ | Dup `factura_id` → `idx_factura_items_factura`. |
| 3 | `factura_faltante` | ✅ | Quitado `index=True` de `factura_id`/`codigo_barra` → `idx_factura_faltante_fac`/`_cb` (DB tenía solo `idx_*`, sin `ix_*`). Agregado `server_default=func.now()` a `creado_en` (DB tiene `DEFAULT now()`). **Drift Render** (`id` perdió SEQUENCE) diferido. |
| 4 | `invoice_batches` | ✅ | OK sin cambios. |
| 5 | `reclamos` | ✅ | Dup `factura_id` → `idx_reclamos_factura`. |
| 6 | `reclamo_items` | ✅ | OK sin cambios. |
| 7 | `erp_stock` | ✅ | Dup `codigo_barra` → `idx_erp_stock_codigo`. |
| 8 | `stock_differences` | ✅ | Dup `factura_id` → `idx_stock_diff_factura`. |
| 9 | `documentos_pendientes` | ✅ | OK sin cambios. |
| 10 | `pagos_ajustes_cc` | ✅ | OK sin cambios. |

**Cambios en `database.py`**:
- **5 `index=True` REMOVIDOS** (`factura_items.factura_id`, `factura_faltante.factura_id`+`codigo_barra`, `erp_stock.codigo_barra`, `reclamos.factura_id`, `stock_differences.factura_id`).
- **5 `Index` agregados** vía `__table_args__` en 5 clases (matchean los `idx_*` custom).
- **1 `server_default=func.now()`** en `factura_faltante.creado_en`.

**Verificación**: autogenerate post-cambios → 4 drops de `ix_*` redundantes (cleanup); `factura_faltante` 100% limpia (su DB no tenía `ix_*`, solo `idx_*`). 0 referencias a `idx_*` del lote, 0 `alter_column` de `creado_en`.

## LOTE 7 — ObServer mirror — Catálogo 1 (10) ✅ 2026-06-02

Todas OK sin cambios (0 ops en autogenerate). Los `index=True` de estas tablas
generan `ix_*` que ya existen en la DB sin `idx_*` custom duplicado.

| # | Tabla | Estado | Notas |
|---|---|---|---|
| 1 | `obs_categorias_clientes` | ✅ | OK sin cambios. |
| 2 | `obs_codigos_barras` | ✅ | OK sin cambios. Tabla VACÍA en Pieri por permisos Gestion. |
| 3 | `obs_colegios_medicos` | ✅ | OK sin cambios. |
| 4 | `obs_grupos_clientes` | ✅ | OK sin cambios. |
| 5 | `obs_laboratorios` | ✅ | OK sin cambios. |
| 6 | `obs_medicos` | ✅ | OK sin cambios. |
| 7 | `obs_medicos_matriculas` | ✅ | OK sin cambios. |
| 8 | `obs_nombres_drogas` | ✅ | OK sin cambios. |
| 9 | `obs_operadores` | ✅ | OK sin cambios. |
| 10 | `obs_rubros` | ✅ | OK sin cambios. |

## LOTE 8 — ObServer mirror — Catálogo 2 / Ventas (10) ✅ 2026-06-02

| # | Tabla | Estado | Notas |
|---|---|---|---|
| 1 | `obs_clientes` | ✅ | OK sin cambios. |
| 2 | `obs_convenios` | ✅ | OK sin cambios. |
| 3 | `obs_obras_sociales` | ✅ | OK sin cambios. |
| 4 | `obs_planes` | ✅ | OK sin cambios. |
| 5 | `obs_productos` | ✅ | 3 dups (`laboratorio_observer`/`codigo_alfabeta`/`id_tipo_venta_control`) → `idx_obs_prod_lab`/`_alfabeta`/`_tvc`. Declarado el **GIN trgm** `idx_obs_productos_descripcion_trgm` (`postgresql_using='gin'`, `postgresql_ops={'descripcion':'gin_trgm_ops'}`). `es_fraccionable` → `server_default=text('false')`. |
| 6 | `obs_subrubros` | ✅ | OK sin cambios. |
| 7 | `obs_stock` | ✅ | `fraccionado` → `server_default=text('false')`. |
| 8 | `obs_ventas_detalle` | ✅ | `operador_observer` (solo `idx`, sin `ix`) + `tipo_operacion` (dup) → quitado `index=True`, declarados `idx_obs_vd_operador`/`idx_ovd_tipo`. Agregados 4 compuestos `idx_ovd_{cliente,medico,os,producto}_fecha`. El resto de single-col conservan `index=True`. (~1M filas — solo modelo, no toca la DB.) |
| 9 | `obs_ventas_mensuales` | ✅ | Agregado compuesto `idx_obs_vtas_anio_mes`. |
| 10 | `cliente_os_inferida` | ✅ | OK sin cambios. |

**Cambios en `database.py`** (todos en lote 8; lote 7 sin cambios):
- **6 `index=True` REMOVIDOS** (`obs_productos`: lab/alfabeta/tvc; `obs_ventas_detalle`: operador_observer/tipo_operacion).
- **9 `Index` agregados** vía `__table_args__`: 5 single-col que matchean `idx_*` custom + 1 **GIN trgm** + 4 compuestos `idx_ovd_*_fecha` (1 más en obs_ventas_mensuales).
- **2 `server_default=text('false')`** (`obs_productos.es_fraccionable`, `obs_stock.fraccionado`).

**Patrón nuevo**: índice **GIN con trgm** se modela con `Index(name, col, postgresql_using='gin', postgresql_ops={col: 'gin_trgm_ops'})`. Autogenerate lo matchea exacto (requiere extensión `pg_trgm` instalada).

**Verificación**: autogenerate post-cambios → 3 drops de `ix_*` en `obs_productos` + 1 en `obs_ventas_detalle` (todos redundantes, cleanup); 0 referencias a `idx_*`/trgm del lote, 0 `alter_column` de booleanos.

## LOTE 9 — Rendición / Clientes / Plantillas (10) ✅ 2026-06-02

**Las 10 OK sin cambios** (0 ops). Tablas recientes/limpias — sin el legado de
índices duplicados `idx_*`/`ix_*` de las tablas viejas. Sus modelos ya declaran
todo correcto.

| # | Tabla | Estado | Notas |
|---|---|---|---|
| 1 | `devolucion_receta` | ✅ | OK sin cambios. |
| 2 | `motivo_devolucion` | ✅ | OK sin cambios. |
| 3 | `rendicion_grupo` | ✅ | OK sin cambios. Existe en DB + modelo (confirmado en esta branch). |
| 4 | `rendicion_grupo_os` | ✅ | OK sin cambios. |
| 5 | `rendicion_lote` | ✅ | OK sin cambios. |
| 6 | `rol_filtro_obra_social` | ✅ | OK sin cambios. |
| 7 | `vendedor_bookmark` | ✅ | OK sin cambios. |
| 8 | `clientes` | ✅ | OK sin cambios. |
| 9 | `plantilla_campos` | ✅ | OK sin cambios. |
| 10 | `plantillas_exportacion` | ✅ | OK sin cambios. |

## LOTE 10 — Misc / Estacionalidad / Cadencia / Kellerhoff (6) ✅ 2026-06-02

**Las 6 OK sin cambios** (0 ops).

| # | Tabla | Estado | Notas |
|---|---|---|---|
| 1 | `kellerhoff_catalogo` | ✅ | OK sin cambios. |
| 2 | `kellerhoff_equivalencia` | ✅ | OK sin cambios. |
| 3 | `estacionalidad_escenarios` | ✅ | OK sin cambios. |
| 4 | `estacionalidad_productos` | ✅ | OK sin cambios. |
| 5 | `cadencia_lab_snapshot` | ✅ | OK sin cambios. |
| 6 | `export_templates` | ✅ | OK sin cambios. |

---

## ✅ Review COMPLETO (93/93) — próximos pasos para el baseline final

La revisión por lotes terminó. El diff de autogenerate quedó reducido a:
1. **Drops de `ix_*` redundantes** (lotes 3-8): cleanup legítimo de índices que
   `index=True` duplicaba con los `idx_*` custom. Al aplicar el baseline en una DB
   con `upgrade head` se dropean (los `idx_*` siguen vigentes).
2. **`pack_equivalencias.laboratorio_id`/`fuente` + FK** (ruido de staleness): la
   branch está atrás de `main`. **Mergear `main` acá** lo resuelve (los modelos
   pasan a tener esas columnas → desaparece el drop).

**Antes de generar el baseline definitivo:**
- [x] Mergear `main` en `feat/alembic-baseline` (packs, rendición-grupos, appnucleo,
      PR #144) y re-correr el review sobre las tablas que toque. **HECHO 2026-06-02**:
      solo `pack_equivalencias` necesitó re-review (ver arriba). Diff final = 39 drops
      de `ix_*` redundantes, 0 ops estructurales.
- [ ] (Opcional) `include_object` en `env.py` para suprimir los false-positives de
      sequences SERIAL.
- [ ] Reconciliar los drift Render vs Local de la tabla de abajo.
- [x] **Generar el baseline final** — HECHO 2026-06-02: `alembic/versions/ae43763059ec_baseline_schema.py`
      (93 `create_table` + 141 `create_index`, FKs inline, + `CREATE EXTENSION pg_trgm`
      manual). Validado: `upgrade head` sobre DB vacía aplica limpio; diff vs local-vivo
      = solo los ~39 `ix_*` redundantes + `panel_heartbeat.id` (ver drift abajo).
- [x] `alembic stamp head` en cada instancia. **HECHO 2026-06-02**: Local + Render
      (`db_pieri`) en `ae43763059ec (head)`. Badia NO aplica. (Previo: fix de
      `panel_heartbeat.id` sequence también en Render.)
- [x] **Switch de `init_db`** — HECHO 2026-06-02 (commit). Patrón bootstrap aditivo:
      `_alembic_sync()` stampea (1ra vez) o `upgrade head` (siguientes), fail-soft,
      después de `create_all`+`_pg_add_columns` (que conviven durante la transición).
      Fuerza `ALEMBIC_DATABASE_URL` a la url de init_db. **Fix de bug latente**: el
      índice GIN trgm en el modelo hacía fallar `create_all` en DB fresca sin la
      extensión → ahora `CREATE EXTENSION pg_trgm` ANTES de `create_all`. Verificado:
      DB fresca → stamp + 93 tablas + trgm OK; DB stampeada → upgrade head no-op.
      ⚠ Todavía NO deployado a Render (la branch no está en main) — el primer deploy
      con esto sobre Render (ya stampeada) será un `upgrade head` no-op.

### Pendiente (gradual, no bloqueante)
- [ ] Migrar los `_pg_add_columns` inline a revisiones Alembic dedicadas (paso 5,
      conviven mientras tanto — son IF NOT EXISTS).
- [ ] (Opcional) `include_object` en `env.py` para los false-positives de sequences.
- [ ] Migración `drop_redundant_ix` (post-deploy del baseline).

### Sobre los 39 `drop_index('ix_*')` — decisión: NO tocarlos ahora (2026-06-02)

Esos drops son un **artefacto del validador** (autogenerate contra la DB viva, que
tiene los `ix_*` redundantes creados por el viejo `index=True`). **No son el baseline
final**: el baseline se generará contra una **DB vacía**, donde autogenerate hará
`CREATE` solo de lo que declaran los modelos (los `idx_*`); los `ix_*` no se declaran
→ no se crean. Las DBs nuevas nacen limpias; las existentes (`stamp head`) conservan
los `ix_*` redundantes como peso muerto inofensivo.

**No hacemos cleanup manual ahora** (sería DDL en prod + prematuro: Alembic aún no
está adoptado). Queda como TODO una **migración dedicada `drop_redundant_ix`**
post-adopción para barrer esos `ix_*` de las instancias existentes.
- [ ] TODO (post-adopción Alembic): migración que dropea los `ix_*` redundantes
      listados por el validador en las DBs ya existentes.

---

## Notas globales encontradas durante la revisión

> A medida que se revisan los lotes, anotar acá patrones que aparecen
> repetidamente (ej. "todas las tablas X usan `default=now_ar` que Alembic
> no captura como server_default").

1. **`server_default` faltante** (lotes 1-2): columnas agregadas con `_pg_add_columns`
   que incluían `DEFAULT X` no reflejan `server_default=` en el modelo. Agregarlo
   donde corresponda (`compare_server_default=True` los flagea como `alter_column`).
2. **Índices duplicados `idx_*` + `ix_*`** (lotes 3-4): un Column con `index=True`
   genera `ix_<tabla>_<col>`; si además hay un `idx_*` custom (inline ALTER) sobre
   la misma columna, la DB tiene los DOS. Fix: quitar `index=True`, declarar el
   `idx_*` en `__table_args__`. El baseline dropea el `ix_*` redundante (cleanup OK).
3. **Índices con `ORDER BY ... DESC`** (lote 2): modelar con `text('col DESC')` en
   `Index(...)`, no `Column.desc()` (orden de inicialización de la clase).
4. **False-positives a ignorar**: `id` PK SERIAL (`nextval(...)` / `INFO ... assuming
   SERIAL and omitting`) — sequences implícitas, inofensivas. TODO: `include_object`
   en `env.py` para suprimirlas.
5. **Índice GIN trgm** (lote 8): `Index(name, col, postgresql_using='gin',
   postgresql_ops={col: 'gin_trgm_ops'})`. Autogenerate lo matchea exacto (requiere
   extensión `pg_trgm`).
6. **`server_default` en booleanos** (lote 8): columnas con `DEFAULT false` en DB →
   `server_default=text('false')` (no `'false'` string, que no matchea el compare).

---

## Decisiones de drift (Render vs Local)

> Cuando aparezca un drift entre instancias en una tabla, anotar acá la
> decisión (qué versión gana) + el ALTER que reconcilia.

Diff en vivo Local vs Render (Pieri `db_pieri`) generado 2026-06-02 con
`scripts/schema_diff.py`. **Canónico = la versión con el DEFAULT/FK/sequence**
(el modelo declara todos ahora con `server_default`). Estado:

| Diff | Decisión (canónico) | ALTER | Estado |
|---|---|---|---|
| `compartido_importado.accion` ≠DEFAULT | DEFAULT 'importado' | `SET DEFAULT 'importado'` en Local | ✅ Local 2026-06-02 |
| `compartido_importado.creado_en` ≠DEFAULT+≠NULL | `now()` + NOT NULL | `SET DEFAULT now()` + `SET NOT NULL` en Local (0 filas) | ✅ Local 2026-06-02 |
| `parser_ofertas_lab.column_mapping` ≠DEFAULT | DEFAULT '{}' | `SET DEFAULT '{}'` en Local | ✅ Local 2026-06-02 |
| `parser_ofertas_lab.formato` ≠DEFAULT | DEFAULT 'plano' | `SET DEFAULT 'plano'` en Local | ✅ Local 2026-06-02 |
| `panel_comandos.id` (Local sin sequence) | SERIAL | `CREATE SEQUENCE panel_comandos_id_seq OWNED BY` + `setval` + `SET DEFAULT nextval` en Local | ✅ Local 2026-06-02 |
| `factura_faltante.id` (Render sin sequence) | SERIAL | `CREATE SEQUENCE factura_faltante_id_seq OWNED BY` + `setval` + `SET DEFAULT nextval` en Render | ✅ Render 2026-06-02 (arregló INSERTs rotos) |
| `parser_ofertas_lab.laboratorio_id_fkey` perdida en Render | FK presente | `ADD CONSTRAINT ... FOREIGN KEY (laboratorio_id) REFERENCES laboratorios(id)` en Render (0 orphans verificados) | ✅ Render 2026-06-02 |

`alembic_version` (solo en Local) = bookkeeping de Alembic; se resuelve cuando se
stampee Render. No es drift real.

**✅ Drift Local↔Render RESUELTO (2026-06-02)**: tras los ALTERs, el diff Local vs
Render quedó en **0** estructural. Único restante: `alembic_version` (cosmético).

**Drift extra descubierto al validar el baseline** (baseline-fresco vs vivo):

| Diff | Decisión | ALTER | Estado |
|---|---|---|---|
| `panel_heartbeat.id` sin sequence (AMBAS instancias lo perdieron; modelo=SERIAL) | SERIAL | `CREATE SEQUENCE panel_heartbeat_id_seq OWNED BY` + `setval` + `SET DEFAULT nextval` | ✅ Local + Render 2026-06-02 |

(Singleton id=1 → la pérdida nunca rompió nada, pero el baseline lo crea SERIAL.)
