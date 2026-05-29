# Alembic — review del baseline (tracking por lotes)

Doc vivo para trackear progreso de la revisión del baseline migration
(paso 2 de la adopción de Alembic).

Plan general: ver `docs/mejoras_pendientes.md` → "Adoptar Alembic para migraciones".

## Estado global

- **Total tablas a revisar**: 93
- **Lotes**: 10 (de ~10 tablas cada uno)
- **Revisadas**: 29 / 93 (Lotes 1-3 ✅ 2026-05-29)

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

## LOTE 4 — Labs / Provs / Descuentos / Cronogramas (8)

| # | Tabla | Estado | Notas |
|---|---|---|---|
| 1 | `laboratorios` | ⬜ | |
| 2 | `proveedores` | ⬜ | |
| 3 | `descuentos_base` | ⬜ | Drift: `transfer_excedente_meses`/`necesita_meses` perdieron DEFAULT en Render (PR #131). |
| 4 | `laboratorio_drogueria` | ⬜ | |
| 5 | `parser_ofertas_lab` | ⬜ | Drift Render: 2×≠DEFAULT, 1 FK perdida (`laboratorio_id_fkey`). |
| 6 | `proveedor_cronograma` | ⬜ | |
| 7 | `proveedor_horarios_reparto` | ⬜ | |
| 8 | `tipo_pedido_config` | ⬜ | |

## LOTE 5 — Pedidos / Compra (10)

| # | Tabla | Estado | Notas |
|---|---|---|---|
| 1 | `pedidos` | ⬜ | |
| 2 | `pedido_items` | ⬜ | |
| 3 | `pedido_borrador` | ⬜ | |
| 4 | `pedido_emitido` | ⬜ | |
| 5 | `pedido_emitido_item` | ⬜ | |
| 6 | `procesos_compra` | ⬜ | |
| 7 | `ofertas_minimo` | ⬜ | |
| 8 | `modulos` | ⬜ | |
| 9 | `modulo_packs` | ⬜ | |
| 10 | `plantillas` | ⬜ | |

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

(vacío todavía)

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
