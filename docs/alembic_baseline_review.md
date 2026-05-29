# Alembic — review del baseline (tracking por lotes)

Doc vivo para trackear progreso de la revisión del baseline migration
(paso 2 de la adopción de Alembic).

Plan general: ver `docs/mejoras_pendientes.md` → "Adoptar Alembic para migraciones".

## Estado global

- **Total tablas a revisar**: 93
- **Lotes**: 10 (de ~10 tablas cada uno)
- **Revisadas**: 0 / 93

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

## LOTE 1 — Core / Config / Infra (10)

| # | Tabla | Estado | Notas |
|---|---|---|---|
| 1 | `alarmas_notificadas` | ⬜ | |
| 2 | `backup_log` | ⬜ | |
| 3 | `configuracion` | ⬜ | Cuidado: tabla causante del wipe 2026-05-28 (DEFAULT perdido en NOT NULL). |
| 4 | `cron_log` | ⬜ | |
| 5 | `farmacias` | ⬜ | |
| 6 | `mv_refresh_log` | ⬜ | |
| 7 | `panel_comandos` | ⬜ | Drift Render: ≠DEFAULT en `id` (Local sin `nextval`, Render con). |
| 8 | `panel_heartbeat` | ⬜ | |
| 9 | `sucursales` | ⬜ | |
| 10 | `sync_lock` | ⬜ | |

## LOTE 2 — Auth / Users / Análisis (9)

| # | Tabla | Estado | Notas |
|---|---|---|---|
| 1 | `usuarios` | ⬜ | |
| 2 | `usuario_farmacias` | ⬜ | |
| 3 | `usuarios_pedidos` | ⬜ | |
| 4 | `home_card_clicks` | ⬜ | |
| 5 | `analisis_sesiones` | ⬜ | |
| 6 | `analisis_ia_cache` | ⬜ | |
| 7 | `archivos_compartidos` | ⬜ | |
| 8 | `compartido_importado` | ⬜ | Drift Render: ≠DEFAULT `accion`+`creado_en`, ≠NULL `creado_en`. |
| 9 | `obs_sync_log` | ⬜ | |

## LOTE 3 — Productos local + identificadores (10)

| # | Tabla | Estado | Notas |
|---|---|---|---|
| 1 | `productos` | ⬜ | |
| 2 | `producto_codigos_barra` | ⬜ | |
| 3 | `producto_atributos` | ⬜ | |
| 4 | `producto_flags` | ⬜ | |
| 5 | `producto_precios_hist` | ⬜ | |
| 6 | `productos_pendientes_revision` | ⬜ | |
| 7 | `product_analytics` | ⬜ | |
| 8 | `barcode_mappings` | ⬜ | |
| 9 | `equivalencias_proveedor` | ⬜ | |
| 10 | `pack_equivalencias` | ⬜ | |

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
