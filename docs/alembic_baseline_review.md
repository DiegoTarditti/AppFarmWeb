# Alembic — review del baseline (tracking por lotes)

Doc vivo para trackear progreso de la revisión del baseline migration
(paso 2 de la adopción de Alembic).

Plan general: ver `docs/mejoras_pendientes.md` → "Adoptar Alembic para migraciones".

## Estado global

- **Total tablas a revisar**: 93
- **Lotes**: 10 (de ~10 tablas cada uno)
- **Revisadas**: 47 / 93 (Lotes 1-3 ✅ 2026-05-29 · Lotes 4-5 ✅ 2026-06-02)

> ⚠ **BLOQUEANTE antes de finalizar el baseline**: la branch `feat/alembic-baseline`
> está **atrás de `main`**. El review del lote 4 (2026-06-02) detectó que la diff
> quiere dropear `pack_equivalencias.laboratorio_id` + `fuente` + su FK — son los
> campos del feature de packs (PR del 1-jun, mergeado a main DESPUÉS del review del
> lote 3). Los modelos de esta branch no los tienen. **Hay que mergear `main` acá**
> (trae packs, rendición-grupos, appnucleo, etc.) antes de generar el baseline
> definitivo, sino arrastra drops espurios de tablas ya revisadas.

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

## LOTE 6 — Facturas / Stock / Documentos (10)

| # | Tabla | Estado | Notas |
|---|---|---|---|
| 1 | `facturas` | ⬜ | |
| 2 | `factura_items` | ⬜ | |
| 3 | `factura_faltante` | ⬜ | Drift Render: ≠DEFAULT `id` (Render perdió la SEQUENCE — INSERTs romperían). |
| 4 | `invoice_batches` | ⬜ | |
| 5 | `reclamos` | ⬜ | |
| 6 | `reclamo_items` | ⬜ | |
| 7 | `erp_stock` | ⬜ | |
| 8 | `stock_differences` | ⬜ | |
| 9 | `documentos_pendientes` | ⬜ | |
| 10 | `pagos_ajustes_cc` | ⬜ | |

## LOTE 7 — ObServer mirror — Catálogo 1 (10)

| # | Tabla | Estado | Notas |
|---|---|---|---|
| 1 | `obs_categorias_clientes` | ⬜ | |
| 2 | `obs_codigos_barras` | ⬜ | Tabla VACÍA en Pieri por permisos Gestion. |
| 3 | `obs_colegios_medicos` | ⬜ | |
| 4 | `obs_grupos_clientes` | ⬜ | |
| 5 | `obs_laboratorios` | ⬜ | |
| 6 | `obs_medicos` | ⬜ | |
| 7 | `obs_medicos_matriculas` | ⬜ | |
| 8 | `obs_nombres_drogas` | ⬜ | |
| 9 | `obs_operadores` | ⬜ | |
| 10 | `obs_rubros` | ⬜ | |

## LOTE 8 — ObServer mirror — Catálogo 2 / Ventas (10)

| # | Tabla | Estado | Notas |
|---|---|---|---|
| 1 | `obs_clientes` | ⬜ | |
| 2 | `obs_convenios` | ⬜ | |
| 3 | `obs_obras_sociales` | ⬜ | |
| 4 | `obs_planes` | ⬜ | |
| 5 | `obs_productos` | ⬜ | Tabla central, índices importantes (lab/rubro/droga/codigo_alfabeta). |
| 6 | `obs_subrubros` | ⬜ | |
| 7 | `obs_stock` | ⬜ | |
| 8 | `obs_ventas_detalle` | ⬜ | Tabla grande, ~1M filas en Pieri. Cuidado con índices `idx_ovd_*`. |
| 9 | `obs_ventas_mensuales` | ⬜ | |
| 10 | `cliente_os_inferida` | ⬜ | |

## LOTE 9 — Rendición / Clientes / Plantillas (10)

| # | Tabla | Estado | Notas |
|---|---|---|---|
| 1 | `devolucion_receta` | ⬜ | |
| 2 | `motivo_devolucion` | ⬜ | |
| 3 | `rendicion_grupo` | ⬜ | NUEVA (PR #138). Verificar en Render post-deploy. |
| 4 | `rendicion_grupo_os` | ⬜ | NUEVA (PR #138). Verificar en Render post-deploy. |
| 5 | `rendicion_lote` | ⬜ | |
| 6 | `rol_filtro_obra_social` | ⬜ | |
| 7 | `vendedor_bookmark` | ⬜ | |
| 8 | `clientes` | ⬜ | |
| 9 | `plantilla_campos` | ⬜ | |
| 10 | `plantillas_exportacion` | ⬜ | |

## LOTE 10 — Misc / Estacionalidad / Cadencia / Kellerhoff (6)

| # | Tabla | Estado | Notas |
|---|---|---|---|
| 1 | `kellerhoff_catalogo` | ⬜ | |
| 2 | `kellerhoff_equivalencia` | ⬜ | |
| 3 | `estacionalidad_escenarios` | ⬜ | |
| 4 | `estacionalidad_productos` | ⬜ | |
| 5 | `cadencia_lab_snapshot` | ⬜ | |
| 6 | `export_templates` | ⬜ | |

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

---

## Decisiones de drift (Render vs Local)

> Cuando aparezca un drift entre instancias en una tabla, anotar acá la
> decisión (qué versión gana) + el ALTER que reconcilia.

Pendientes del diff de Pieri (ver `schema_diff_pieri.md`):

| Diff | Decisión | ALTER |
|---|---|---|
| `compartido_importado.accion` ≠DEFAULT | — | — |
| `compartido_importado.creado_en` ≠DEFAULT + ≠NULL | — | — |
| `factura_faltante.id` ≠DEFAULT (Render sin sequence) | — | — |
| `panel_comandos.id` ≠DEFAULT (Local sin sequence) | — | — |
| `parser_ofertas_lab.column_mapping` ≠DEFAULT | — | — |
| `parser_ofertas_lab.formato` ≠DEFAULT | — | — |
| `parser_ofertas_lab.laboratorio_id_fkey` perdida en Render | — | — |
