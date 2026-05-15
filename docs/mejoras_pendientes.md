# Mejoras pendientes — backlog vivo

Doc maestro de mejoras. Vivo: se actualiza con cada idea/decisión. Cuando algo se hace, se marca ✅ y se agrega fecha.

---

## 🎨 Migración UX al theme-emerald (en curso, 2026-05-08+)

Rediseño visual unificado iniciado en commit `d0243e4` (home + design system).
Patrón: cada pantalla extiende `base.html` con `{% block body_class %}theme-emerald{% endblock %}`,
usa `page-header`, `card`, `btn-{primary,secondary,ghost,mint,danger}`, `badge-{mint,orange,danger,warn,info,mute}`,
`icon-tile`, `section-label`, `glow-text`, `ds-input/select/textarea`, tokens `--ds-*`.

### ✅ Hechas (12 pantallas + base + sidebar)
- 2026-05-07: index, login, base.html (sidebar/topbar/DS tokens) — commit `d64b04c`
- 2026-05-07: compare, results, claim, claims_list, providers — commit `d64b04c`
- 2026-05-08: orders_list — commit `33b7355`
- 2026-05-09: provider_invoices, provider_mappings, invoice_items, pick_fields (closure
  flujo factura) — commit `ff77da2`
- 2026-05-09: order_detail Etapa 1 (top-bar + step-card + banners + .prop-input) —
  commit `305a00d`
- 2026-05-09: compras_dia.html (completo) — commit `23cc2ba`

### ✅ Desempate matcher por forma farmacéutica (HECHO 2026-05-13)
Helper `_detectar_forma(desc)` en `producto_matcher.py:429` + tiebreaker
aplicado en 3 lugares (estrategia fuzzy_lab, fallback global, fase 3 obs).
Cuando hay empate al mismo score y la forma extraída del raw text identifica
un solo candidato, se desempate y se agrega warning `tiebreak_forma`.
Tests en `tests/` (forma/dexalergin/tiebreak — 11 verdes).

### ✅ Agente IA para matching de pendientes (HECHO 2026-05-11)
Implementado y en producción:
- `services/llm_matcher.py` con prompt estructurado y cache_control ephemeral.
- 4 endpoints en `routes/productos_pendientes.py`: analizar-ia, estimar-costo-ia,
  aplicar-ia (singular) y aplicar-ia-bulk.
- Modelo `ProductoPendienteRevision` con todas las columnas `llm_*`
  (`database.py:442`).
- UI con botón "🤖 Analizar con IA" + badge de sugerencia + aplicar bulk
  por umbral de confidence.
- Modelo usado: Haiku 4.5. Documentado en `CLAUDE.md` (sección "LLM matcher").

### ⏳ Pendiente — `compras_rapido` vs `compras_dia_armar` multi-drog (2026-05-10)
Diego confirmó (2026-05-10) que `compras_rapido` "se reemplazó por el hero" pero
**NO deprecar todavía** — antes hay que portar features valiosas a
`compras_dia_armar`.

**Lo que `compras_rapido` tiene y `compras_dia_armar` NO**:
1. **Selector de ámbito (labs)** — tildar labs específicos a procesar; útil para
   enfocar ofertas concretas.
2. **"Mejor descuento" auto-elegido** — sistema decide la drog óptima por
   producto (en `compras_dia_armar` el user elige manual con toggle Drog: + Libres a:).
3. **Alert "Conflicto"** — marca si el user cambió la drog elegida a una
   sub-óptima (oportunidad perdida monetariamente).
4. **Auditoría de descuentos aplicados** — panel desmarcable mostrando qué
   descuentos sumó el sistema, recalcula al desmarcar.
5. Atajos de teclado documentados (Alt+1..9, Esc, etc.).

**Lo que `compras_dia_armar` tiene y `compras_rapido` NO**:
- Filtros tokenizados (prod/lab/droga/rubro)
- Sync stock ObServer en vivo
- Panel chart dual sticky (AÑO + MES) por producto
- Toggle Drog filter + "Libres a:" bulk
- Emisión real (no solo guardar pedido)
- Pendientes anteriores (NO_VINO)

**Plan de unificación**:
1. Portar a `compras_dia_armar` las features 🟢 #2 (mejor desc auto), #3
   (conflicto alert), #4 (auditoría descuentos). Estas dos son las más
   valiosas — la lógica de cálculo del mejor descuento ya existe en
   `compras_rapido`.
2. Validar UX en producción 1-2 semanas.
3. Recién ahí deprecar `compras_rapido` y redirigir su URL al hero.

Esfuerzo estimado: 1 día (portar #2 y #3 son lo difícil).

### ⏳ Pendiente — Unificar `informe_pedido_auto` ↔ `compras_dia_armar` (2026-05-10)
Las 2 pantallas hacen lo mismo (sugerir pedido) pero con distintos enfoques
y feature sets. NO son redundantes pero sí tienen overlap revisable.

**`informe_pedido_auto`** (eje laboratorio): único en mostrar
- Pérdida estimada $/mes total + por producto.
- Charts top 10 pérdida en unidades + valorizada.
- Diagnóstico textual ("Bajo — cubre ~9d, sugerido ≥19").
- Comparar drogs por producto (botón ⇄).

**`compras_dia_armar`** (eje droguería): único en
- Asignación drog (matriz lab × drog + Libres a: bulk).
- Ofertas con %off, mín, plazo.
- Sync ObServer stock.
- Emisión real del pedido (no solo planificación).
- Panel dual chart (AÑO + MES) sticky.

**Plan sugerido**:
- Portar a `compras_dia_armar` el **diagnóstico textual** ("Bajo — cubre Xd") y el
  **valor de pérdida $/mes** (señal ROI).
- Mantener `informe_pedido_auto` como vista de planificación/diagnóstico (eje lab).
- Revisar BI tablero — quitar entry points duplicados (movimos los botones
  "Armar pedido por lab/productos" al home en commit 2026-05-10).

Esfuerzo: 1-2 horas (portar diagnóstico + valor) + auditoría BI tablero (½ día).

**2026-05-13** — Detectado bug de divergencia: `informe_pedido_auto` sugería
`qty=1` para productos con `u12m=0` mientras que `compras_dia_armar` ya seteaba
`a_pedir=0` en ese caso. Fix puntual en `calcular_metricas_pedido_auto`
(routes/informes.py): si `u12m<=0` → `sugerido=0` y `base_sugerido='sin_ventas'`.
**Sigue pendiente unificar el cálculo de propuesta de mínimos** en una sola
función compartida (hoy hay 2: `calcular_metricas_pedido_auto` con totales u12m
y `purchase_helpers.calcular_min_sugerido` con array mensual de ventas) para
que este tipo de drift no vuelva a pasar.

### ✅ Queue de productos sin match (HECHO 2026-05-13)
Modelo, ruta y UI listos. Hooks de imports cableados:
- **`ofertas_import`** (`routes/ofertas_import.py:918`): not_found → queue
  con oferta_data para re-aplicar al resolver.
- **`modulos_import`** (`routes/modulos_import.py:390`): not_found → queue
  (sin oferta_data, ya que módulos no aplican descuento al resolver).
- Facturas: no aplica — el flujo de facturas genera `stock_differences` por
  diferencia de stock, no items "sin match" que requieran resolución diferida.

Tabla: `/productos/pendientes-revision` con filtros, autocomplete catálogo,
crear/vincular/descartar. Helper público `enqueue_pendiente` en
`routes/productos_pendientes.py:35` (dedup, anti-ruido, counter).

### ⏳ Pendiente — Refinamiento de candidatos en match manual (2026-05-09)
Cuando el matcher devuelve top-N candidatos (todos por debajo de threshold),
hoy se muestran tal cual con el score Jaccard del bulk pass. Idea: agregar
una **segunda pasada** sobre ese subset chico (5-10 items) con análisis costoso
que no escala a 122k items:
- Levenshtein full string (premia parecido textual: "cr" más cerca de "cre"
  que de "emu").
- Prefix match de tokens huérfanos: source "cr" + candidate "crema" → bonus.
- N-gram overlap (bigrams/trigrams).
- Análisis estructural: parsear en {producto, forma, dosis, cantidad, lab}
  y matchear campo-por-campo.

**API propuesta**: `refinar_candidatos(source_desc, candidatos: list[(score, prod)]) → list[(score, prod)]`.
Llamado por la UI de match manual antes de renderizar.

**Beneficio**: resuelve casos como DERMAGLOS cr ↔ CRE vs EMU sin canonicalizar
formas (lo cual rompería matches con suppliers que omiten la forma).

Esfuerzo: 2-3 horas si solo Levenshtein + prefix; medio día si full estructural.

### ⏳ Pendiente — compras_dia_armar header layout (2026-05-09)
Reorganizar la barra de filtros del encabezado en 2 columnas:
- **Col 1**: `Filtrar producto` + `Filtrar lab` (stacked verticalmente).
- **Col 2**: `Filtrar rubro` arriba, debajo los checks `Solo venta libre` + `Solo con sugerencia (subir/bajar)`.

Hoy van todos en una fila horizontal larga que en pantallas medianas wrappea feo.
Ver captura sesión 2026-05-09.

### ⏳ Pendiente — order_detail Etapas 2-3
- **Etapa 2 (tablas internas)**: ~150 ocurrencias de `bg-emerald-50/amber-50/sky-100/violet-100`
  en filas de tablas de los 3 step-cards (módulos / ofertas / resumen). Reemplazar por
  fondos `rgba(token,.X)` con tokens del DS.
- **Etapa 3 (botones e inputs internos)**: ~30 botones `bg-emerald-600/700`, `bg-red-600/700`,
  `bg-fuchsia-600` → `btn btn-mint/primary/danger`. Inputs varios faltantes (`prop-input`
  ya migrado).
- **Modales**: 2 (match manual + chart histórico) — heredan estilos del DS pero hay que
  pasar background custom al theme-emerald.

### ⏳ Pendiente — Resto del flujo Compras
Por orden sugerido (más usado primero):
- `compras_dia_armar.html` (1448L, mediano-alto esfuerzo) — pantalla operativa diaria
  con grilla por droguería, transfers, sugerencias.
- `compras_rapido.html` (743L) — armado rápido sin análisis previo.
- `purchase_suggest.html` (519L) — sugerencias automáticas.
- `purchase_results.html` (764L) — resultados del análisis.
- `compras_transfers.html` (201L), `purchase_analysis.html` (171L),
  `purchase_batch.html` (92L), `purchase_processed.html` (74L) — más chicos, rápidos.

### ⏳ Pendiente — Resto del sistema (~108 templates)
Inventario completo con priorización por flujo está en el chat de la sesión 2026-05-09.
Top próximos por flujo:
- **Catálogo**: productos, producto_detalle, vademecum, estadisticas_drogas, obs_productos.
- **Laboratorios**: laboratorios, lab_equivalencias, lab_ofertas_minimo, ofertas_import,
  modulo_packs, modulos_import, plantilla_editor.
- **Informes**: informes_index + 8 sub-pantallas.
- **OS/Clientes**: os_* (9), clientes_* (4), recetas_scan, obras_sociales_catalogo.
- **Admin**: admin_* (10).

### Lección aprendida (PR #23)
**No quitar funcionalidad por estética.** El rediseño inicial del home dejó solo 6 cards
de "Acciones frecuentes", quitando informes/BI/productos/clientes/OS. Hubo que restaurarlas
en `fix/home-cards-restore`. Regla: en migraciones visuales, **conservar todos los entry
points** del menú aunque visualmente se reorganicen.

---

## 🆕 Pendiente — Chequeo recetas PAMI/OS para liquidación (2026-05-06)

Cruce de 3 fuentes para la liquidación mensual de PAMI y otras OS:
1. Listado oficial PAMI (PDF que baja del portal).
2. Recetas físicas (escaneadas con pistola).
3. Observer.Gestion.Recetas.

**Estado:**
- ✅ Cruce físicas ↔ Observer: implementado en `/recetas/scan` (escaneo + match
  contra `Gestion.Recetas` por OPF/NumeroReceta/NumeroAutorizacionExterno).
- ⏳ Cruce listado oficial PAMI ↔ Observer: pendiente.
- ⏳ Vista unificada con las 3 fuentes (encuentra discrepancias).

Detalle completo + ejemplo de PDF PAMI:
[docs/feature_checkup_recetas_pami.md](feature_checkup_recetas_pami.md).

---

## 📐 Reglas generales del sistema

### Imports siempre validan contra el catálogo existente
**Filosofía**: mejor descartar/avisar un dato malo que importar basura.

Cualquier import (ofertas, módulos, facturas en el conversor, etc.) tiene que pasar por una etapa de validación entre el mapeo de columnas y el guardado:

1. **Match contra catálogo**: cada item se intenta matchear contra `productos` (por EAN, alts, codigo_alfabeta).
2. **Fallback por descripción + lab**: si no hay match exacto, se intenta fuzzy match por descripción dentro del lab del archivo.
3. **Detección de outliers en precio**: si matchea y hay `precio_pvp` previo, comparar contra el precio importado. Variación > umbral (sugerido 30-50%) → warning.
4. **Panel de validación previo al guardado**: mostrar 4 buckets:
   - ✅ OK (limpio)
   - 🔍 Match fuzzy (matcheó por descripción, score < 1.0)
   - ⚠ Warning de precio (variación grande)
   - ❌ No encontrado (item no está en catálogo)
5. **Descartar por default los items con problemas**: el user puede destildar para incluirlos.
6. **Solo se importa lo limpio + lo explícitamente habilitado**.

Aplica a:
- Importador de ofertas (Fase B en curso).
- Conversor de facturas (cuando se ajuste).
- Cualquier import futuro.

### ~~Módulo unificado de matching de productos~~ ✅ HECHO 2026-04-25
- `producto_matcher.py` central con `match_producto(target='producto'|'obs_producto')`.
  Cascada: EAN → alfabeta → descripción exacta → tokens superset → Jaccard
  por lab → fuzzy global. Modifiers: cantidad envase (+0.10), monodroga
  (+0.05), variación de precio >30% (-0.20 + warning).
- `match_productos_bulk()` para N items, `buscar_candidatos()` para
  dropdowns de match manual.
- Migrados: `routes/ofertas_import.py`, `observer_matcher.candidatos_para_producto`,
  `scripts/vincular_pedido_observer._matchear` (con `pool` precargado para
  mantener filtro `fecha_baja IS NULL`). Ver commit `9cbb176`.
- 28 tests específicos del matcher (incluye target ObsProducto).

**Pendiente (gradual):** migrar `observer_matcher.match_productos` (bulk-job
de 30k×122k productos) — su precarga de índices in-memory es performance
crítica y no conviene reemplazarla item-por-item; se va a tratar como
una tarea aparte.

**Cómo leerlo:**
- Cada item tiene **trigger** (cuándo conviene hacerlo) y **esfuerzo** estimado.
- Si el trigger se cumple → arrancarlo.
- Antes de empezar a trabajar en algo nuevo, scrollear esta lista por si hay algo más urgente.

---

## 🚀 Rendimiento — cuando empiece a tardar

### ~~Vista materializada para `/estadisticas/drogas`~~ ✅ HECHO 2026-04-25
- Implementado preventivamente. `mv_stats_drogas` con refresh automático post-push a Render. Banner de frescura en la pantalla. Ver commit `8aa1d76`.

### ~~Trigram index en `obs_productos.descripcion`~~ ✅ HECHO 2026-04-25
- `CREATE EXTENSION pg_trgm` + GIN trigram index en `obs_productos(descripcion)`.
  Creado idempotentemente en `_crear_matviews`. EXPLAIN ANALYZE confirma
  Bitmap Index Scan (~0.7ms vs full scan). Aplica a /obs/productos,
  modulo_packs, pack_detector, purchase. Ver commit posterior a `82bc3af`.

### ~~Bulk queries en `/api/pedido/<id>/indicadores`~~ ✅ HECHO 2026-05-06
- Iteraciones previas ya redujeron a queries en lote con `.in_(obs_ids)` y un solo SUM con CASE para u3m+u12m.
- 2026-05-06 commit `b0e6ba6`: unificadas las 2 queries de `obs_ventas_mensuales` (u3m+u12m con CASE + serie_mensual GROUP BY) en 1 sola query raw, agregando en Python. Ahorra 1 round-trip Render→DB. 7 tests test_indicadores siguen verdes.
- Si vuelve a tardar (pedidos >2000 items), siguiente paso: caching corto del JSON de respuesta por pedido_id+q.

### ~~Limpieza periódica de `home_card_clicks`~~ ✅ YA EXISTÍA
- Endpoint `POST /api/cron/limpiar-home-card-clicks` en `routes/admin.py:572`. Workflow `.github/workflows/cron-limpiar-home-card-clicks.yml` corre domingos 03:30 UTC. Borra >90 días.

### Migrar PDFs a S3 / Cloudflare R2
- **Trigger**: el bucket de PDFs (facturas + reclamos) pasa de 5-10 GB.
- **Esfuerzo**: 1 día.
- **Cómo**: subir a R2 (más barato que S3), guardar URL en `Invoice.pdf_filename`. Backfill scripted.

### ~~Optimizar `/api/droga/<id>/comparar-labs`~~ ✅ YA EXISTÍA
- `routes/observer.py:551` usa GROUP BY en todas las queries de ventas. Optimizado.

---

## 🛠 Calidad de código

### Migración EANs alt1/2/3 → producto_codigos_barra (1-a-N) — multi-fase
- **Trigger**: ya en curso. Cerrar 2026-05-XX según validación.
- **Plan documentado en `/admin`** (cards de "Migración EANs"):
  1. **Fase 1.2 — Backfill alt1/2/3 → 1-a-N** ✅ HECHO 2026-05-04 (commit `e678d31`).
     Script idempotente + endpoint `/api/admin/migrar/backfill-codigos-barra` + UI dry-run/ejecutar.
  2. **Fase 2 — Bridge masivo `productos.observer_id`** ✅ HECHO 2026-05-04 (commit `805d1be`).
     Vincula por EAN o codigo_alfabeta cuando match único. Endpoint `/api/admin/migrar/bridge-productos-observer` + UI.
  3. **Fase 3 — Backfill `producto_atributos`** desde Observer vía bridge. Infra ya existe (`/catalogacion`).
     Solo falta correr una vez Fases 1.2 + 2 ejecutadas y data fresca de Observer.
  4. **Fase 1.1 — Activar `EAN_LEGACY_ALTS_DISABLED=1`** en Render. Cuando Fases 1.2+2+3 OK
     y validamos 1-2 semanas que la doble escritura no escribe nada nuevo en alt1/2/3.
  5. **Fase 1.3 — DROP COLUMN alt1/2/3**. Cambio de schema. Eliminar refs a esas columnas en
     `helpers.py` (`_add_alt_barcode`, `_bulk_upsert_productos`), `data_extract.py`, todos los
     sitios donde se lean. Una migración inline al final.
- **Por qué importa**: `productos.codigo_barra` (UNIQUE) + 3 slots fijos `alt1/2/3` no escala
  para productos con 4+ EANs. La 1-a-N tiene trazabilidad por fuente (`manual` / `factura` /
  `observer` / `cruce` / `legacy_alt` / `legacy_principal`) + factura_id, que las columnas
  legacy nunca tuvieron.

### Simplificar `tipo_descuento` en `OfertaMinimo`
- **Trigger**: cualquier refactor del flujo de ofertas/transfers.
- **Esfuerzo**: 2-3 horas.
- **Por qué**: `tipo_descuento='simple'` vs `'con_minimo'` es redundante — la distinción real ya está en `unidades_minima` (si es NULL o ≤1 → aplica desde 1 unidad; si es >1 → requiere mínimo). Todo descuento es "con mínimo", la diferencia es si el mínimo es 1 o N.
- **Qué borrar**: campo `tipo_descuento`, índice `idx_ofertas_minimo_lab_tipo`, endpoint separado `/api/ofertas/preview-con-minimo`, hidden input `con_minimo` en `laboratorios.html`, lógica que bifurca los dos endpoints. Unificar todo en el wizard `/ofertas/import`.
- **Qué conservar**: columna en DB (dejarla como obsoleta hasta que no haya dependencias externas), `OfertaMinimo.unidades_minima` como única fuente de verdad.

### Rutas Flask huérfanas (sin link desde sidebar/templates)
- **Trigger**: cualquier momento, decisión simple.
- **Esfuerzo**: 30 min cada una.
- **Detectadas (route-orphan-finder 2026-04-30)**:
  1. ✅ ~~`/clientes` (clientes_list)~~ — **2026-04-30**: linkeada en sidebar bajo "Obras Sociales" como "Clientes / Pacientes" (templates/base.html).
  2. ✅ ~~`/purchase/processed` (purchase_processed)~~ — **2026-05-01**: linkeada desde `purchase_suggest.html` como "Análisis guardados".
  3. ✅ ~~`/observer/laboratorios` (observer_laboratorios)~~ — eliminada en sesión anterior junto con `observer_labs.html`.

### ~~Cache de evaluación de alarmas~~ ✅ YA EXISTÍA
- `alarmas.py:272-316`: `_CACHE_TTL_SEG=30s`, dict `_cache`, `invalidar_cache()`, `evaluar_todas(force=False)`.

### ~~Linter (`ruff`)~~ ✅ HECHO 2026-05-01
- Job `lint` en `.github/workflows/ci.yml:21-33` con `ruff check .`. `pyproject.toml` con select conservador, ignores y per-file-ignores.

### Más tests para flujos de oro
- **Trigger**: cualquier momento; cuanto antes mejor.
- **Esfuerzo**: 2-4 horas por flujo.
- **Cubrir**:
  - Generación de reclamo + PDF (`routes/claims.py`).
  - Bridge `vincular_pedido_observer.py` con casos edge (ambiguos, sin lab, etc.).
  - Endpoint `/api/pedido/<id>/indicadores` con varios pedidos de prueba.
  - `/api/sync-status` y banner.
  - Comparación de labs en `/estadisticas/drogas`.
- **Hoy**: 132 tests, mayormente sobre `data_extract`, `purchase_engine`, `plantillas` y rutas básicas.

### Type hints + `mypy`
- **Trigger**: refactor grande o cuando un bug de tipado nos muerda.
- **Esfuerzo**: progresivo (varios días).
- **Cómo**: agregar tipos a funciones nuevas y de a poco a las existentes. `mypy --strict-optional` en CI.

### ~~Branch protection en `main`~~ ✅ HECHO 2026-05-01
- Repo hecho público + ruleset via API (id=15842390): require `Syntax check` + `Pytest`, no force-push, no delete. Rama `dev` para trabajo diario, `main` solo para bloques listos.

### Migrar a Alembic
- **Trigger**: pasamos las ~30 tablas en `database.py` o aparece una migración compleja (renombre, mover datos).
- **Esfuerzo**: 1-2 días.
- **Cómo**: instalar Alembic, generar baseline desde la DB actual. Convertir cada `ALTER TABLE IF NOT EXISTS` inline en una migración versionada.

### Docstrings consistentes
- **Trigger**: cuando un nuevo dev se sume al proyecto.
- **Esfuerzo**: progresivo.
- **Cómo**: convención de Google/NumPy style. Incluir args, returns, raises.

### ~~Pre-commit hooks~~ ✅ HECHO 2026-05-01
- `git-hooks/pre-commit`: trailing whitespace + ruff en .py staged. `git-hooks/pre-push`: syntax + ruff completo. Bypass: `SKIP_COMMIT_CHECK=1` / `SKIP_PUSH_CHECK=1`.

---

## 🎨 UX — pulir el sistema

### ~~Botón "Crear y exportar con plantilla" en pedido auto~~ ✅ HECHO 2026-05-01
- Botón "Crear + exportar plantilla 📥" en `templates/informes_pedido_auto.html:389`. Backend en `routes/informes.py:848` — genera XLSX inline sin round-trip a `/order/<id>`. Solo visible cuando `tiene_plantilla=True`.

### ~~Filtro arriba en Pedidos guardados~~ ✅ YA EXISTÍA
- `templates/orders_list.html`: filtro estado (Pendientes/Procesados/Todos) + canal/droguería + búsqueda libre + rango de fechas. Completo.

### ~~Color de fondo del botón en home (no solo del ícono)~~ ✅ HECHO 2026-05-01
- `templates/index.html:199` aplica `card.bg` al `<a>` completo. Selector de color en `personalizar_home.html:76` guarda el color por card. Commit `cda55f5`.

### ~~Botón "?" contextual del manual~~ ✅ HECHO 2026-05-01
- Botón flotante `#help-fab` en `templates/base.html:582-629`. Drawer con marked.js, mapeo URL → sección, atajo `Shift+?`, `Esc` para cerrar.

### Llenar contenido del manual
- **Trigger**: ir poblando con uso real.
- **Esfuerzo**: ~1 hora por doc.
- **Ver**: `docs/manual/TODO.md` para prioridades por sección.
- **Empezar por**: `flujos/01_analizar_laboratorio.md`, `flujos/03_subir_factura.md`, `glosario.md`.

### Capturas de pantalla en el manual
- **Trigger**: cuando llenes contenido de los flujos.
- **Esfuerzo**: 5 min por doc.
- **Cómo**: carpeta `docs/manual/img/` con sufijo de fecha (`indicadores_2026-04.png`). Versionar (o `git lfs` si pesan).

### Onboarding tour primera vez
- **Trigger**: cuando hagas onboarding a otra farmacia.
- **Esfuerzo**: 4-6 horas.
- **Cómo**: librería como IntroJS. Tour guiado al primer login con `rol=farmacia`.

### Mobile más pulido
- **Trigger**: cuando recibas reportes de uso desde el celular.
- **Esfuerzo**: progresivo, pantalla a pantalla.
- **Cómo**: ya empezó. Falta auditar pantallas tipo `compare.html`, listados largos, modales de Indicadores en mobile.

### PWA (instalable como app)
- **Trigger**: si querés que Lisandro pueda usar la app como icono en home del celular.
- **Esfuerzo**: 1 día.
- **Cómo**: `manifest.json` + service worker mínimo. Habilita "agregar a home screen".

---

## ⚙️ Operación / Mantenimiento

### ~~Backup explícito a almacenamiento externo~~ ✅ HECHO 2026-05-01
- Cron GitHub Actions semanal (lunes 04:00 UTC): `pg_dump` + upload a Cloudflare R2 vía AWS CLI. `.github/workflows/cron-backup-externo.yml`.

### ~~Sentry o similar para errores en prod~~ ✅ HECHO 2026-05-01
- `sentry-sdk[flask]>=2.0` en requirements. Init opt-in via `SENTRY_DSN` env var. `SENTRY_ENV` configurable. `traces_sample_rate=0.1`.

### Logs centralizados
- **Trigger**: si Render se vuelve insuficiente (logs limitados a últimas N horas).
- **Esfuerzo**: 4 horas.
- **Cómo**: integrar con Logflare, Better Stack, o BetterStack Logs.

### ~~Health check page interno~~ ✅ HECHO 2026-05-01
- `/admin/health` con DB ✓/✗, conteos por tabla, sync ObServer, últimos 5 crons, SHA versión, hora server, Python + PID worker.

### ~~Render como "buzón de comandos remotos" para DockerPanel~~ ✅ HECHO 2026-04-29
**Implementado**:
- Tabla `panel_comandos` + migración inline + agregada al whitelist de pg_type cleanup.
- Endpoints en `routes/admin.py`: `/admin/panel` (UI), `POST /admin/panel/comandos` (encolar), `GET /admin/panel/comandos/recientes` (auto-refresh JSON), `GET /api/panel/comandos/proximo` (DockerPanel polea), `POST /api/panel/comandos/<id>/resultado` (DockerPanel reporta).
- Template `admin_panel.html` con dropdown + tabla auto-refresh c/3s + modal de resultado.
- Auth runner: header `X-Panel-Token` validado contra env var `PANEL_REMOTO_TOKEN` (fail-safe 503 si no está set).
- DockerPanel: thread `_panel_remoto_loop`, config en `agente_config.txt` (`panel_remoto_*`), botones ON/OFF + Configurar, label en status bar, diálogo con botón "Probar conexión".
- Whitelist de comandos en DockerPanel: `pull_restart`, `restart`, `restart_full`, `logs`, `status`, `version`, `sync_now`.

**Pendiente para etapa 2**:
- Setear `PANEL_REMOTO_TOKEN` en Render (env var) y configurar el mismo token en el DockerPanel de la farmacia.
- Multi-farmacia: cuando se vendan más instancias, el `origen` del comando ya está reportado, falta UI para filtrar/escalar.
- Heartbeat: comando periódico (cada N min) que la farmacia auto-genere reportando `version` y se vea en el panel cuándo fue el último heartbeat.

### ~~Bot de Telegram~~ (descartado a favor del buzón Render)
- Mantener nota: si por alguna razón se necesita un canal de comandos por *push* (que la PC reciba inmediatamente sin polling), Telegram long-polling sigue siendo la alternativa. Por ahora el polling outbound al buzón Render alcanza.

### ~~Notificaciones de alarmas críticas a Telegram~~ ✅ HECHO 2026-05-01
- `notificaciones.py` con `enviar_telegram()`, `evaluar_y_notificar()`, dedup en tabla `alarmas_notificadas` con gap 4h y lógica de resurrección.
- Endpoints en `routes/admin.py`: `/api/admin/alarmas/probar-telegram` + `/api/cron/notificar-alarmas`.
- Env vars: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `ALARMAS_SEVERIDADES`.
- Cron `cron-alarmas.yml` cada 15 min con `X-Cron-Secret`. Commit `008fcea`.
- **Multi-farmacia futuro**: 1 bot global + N chats (uno por farmacia). Cuando se venda a más, cada farmacia reporta a su grupo de Telegram.

### Setup Tailscale + VSCode Remote SSH (doc listo, falta ejecutar)
- **Trigger**: cuando se quiera editar/operar la PC farmacia desde cualquier laptop como si fuese local.
- **Esfuerzo**: 30 min de instalación.
- **Doc completo**: ver [docs/tailscale_vscode_remoto.md](tailscale_vscode_remoto.md) — paso a paso para Windows farmacia + laptop.
- **Decisión pendiente**: estructura de cuentas Tailscale (ver doc para opciones — una cuenta tuya con todas las PCs, vs cuenta de Lisandro en farmacia + cuenta propia + share).
- **Por qué**: te queda terminal + editor remoto en cualquier lado, sin abrir puertos. Reemplaza muchos casos de uso del panel remoto (que sigue siendo útil para celular, multi-farmacia, audit trail).

---

## 🌟 Features pendientes

### Flujo de fondos — programar compras por presupuesto semanal (2026-05-15)
- **Trigger**: Diego lo planteó como tema integral. "Plataforma para evaluar totales
  de ventas por lab → programar las compras en función de lo que se puede pagar
  por semana".
- **Esfuerzo**: 1-2 semanas (complejo, varios componentes).
- **Concepto**: análisis integral que cruza:
  - Ingreso esperado por semana (ventas históricas por lab × proyección).
  - Egreso planificado por semana (cronograma de compras programadas + plazos
    de pago de cada drog/lab).
  - Stock actual + cobertura (cuánto aguanta cada lab sin reponer).
  - Capacidad de pago semanal (caja + cuenta corriente disponible).
- **Salida esperada**: vista semanal que muestra "esta semana pagás $X, te entran
  $Y, capacidad libre $Z". Permite ajustar el cronograma moviendo cargas de una
  semana a otra para nivelar flujo.
- **Estrategia de stock**: la reposición chica (matriz drog vía `compras_dia`)
  queda como válvula de escape — si por flujo no se puede meter la compra grande
  de un lab esta semana, el día a día sigue cubriendo los faltantes en cantidades
  chicas sin descuento. La planificación grande prioriza descuentos/módulos.
- **Componentes a construir**:
  1. **Ingreso proyectado por lab**: ya tenemos `obs_ventas_mensuales`. Agregar
     vista `/finanzas/ingreso-proyectado` con promedio + estacionalidad por lab.
  2. **Egreso planificado**: cruzar `ProveedorCronograma` (programados) +
     `OfertaMinimo` (oportunidades) + `pagos_ajustes_cc` (plazos abiertos).
     Vista semanal de "qué tengo que pagar cuándo".
  3. **Planificador semanal**: vista que muestra ingreso vs egreso por semana
     en barras + capacidad libre. Drag de un lab de una semana a otra para
     reprogramar (toca `proxima_fecha` del cronograma).
  4. **Alerta de sobrecarga**: cuando el egreso planificado supera la capacidad
     de la semana, badge rojo + sugerencia de mover algún lab.
- **Modelo nuevo aprox.**:
  - `flujo_caja_semanal(anio, semana, ingreso_proy, egreso_planificado, ajuste)` (snapshot).
  - `plazo_pago_partner(partner_tipo, partner_id, dias_plazo)` (cada lab/drog tiene
    su plazo: contado, 30d, 60d, etc).
- **Relacionado**:
  - Cronograma (ya hecho) define cuándo se programan los pedidos.
  - Matriz `tipo_pedido_config` (ya hecho) define cómo se calculan las cantidades.
  - Falta capa de "cuánto cuesta cada uno" + "cuándo se paga".

### Forecast simple de ventas
- **Trigger**: el user pide "y cuánto voy a vender el mes que viene".
- **Esfuerzo**: 1-2 días.
- **Cómo**: media móvil ponderada o regresión lineal sobre 12m. Mostrar en `/estadisticas/drogas` y en Indicadores.

### Sistema de reglas / alertas configurables
- **Trigger**: el user pide alertas más allá del banner de sync.
- **Esfuerzo**: 2-3 días.
- **Cómo**: tabla `reglas_alerta(condicion_json, severidad, accion)`. Cron evalúa diariamente.

### Comparación temporal de pedidos
- **Trigger**: cualquier momento, agrega valor.
- **Esfuerzo**: 1 día.
- **Cómo**: en `/order/<id>` botón "Comparar con pedido anterior" → match por proveedor/lab + período cercano.

### Cruce ventas vs Obras Sociales
- **Trigger**: cuando ObServer exponga `IdPlan` en `DW.ProductosVendidos`.
- **Bloqueante**: pendiente que averigüe Lisandro.
- **Esfuerzo**: 2-3 días.
- **Cómo**: nueva sync `obs_ventas_plan_mensuales`. Dashboard en `/obras-sociales/<id>` con qué se vende por OS.

### Multi-tenant
- **Trigger**: si querés ofrecer la app a 2+ farmacias.
- **Esfuerzo**: 2-3 semanas.
- **Cómo**: agregar `farmacia_id` a casi todas las tablas, scopes en cada query, roles refinados.

### Sugerencia automática de pedido (AI)
- **Trigger**: estabilizar primero forecast + reglas.
- **Esfuerzo**: 3-5 días.
- **Cómo**: integrar Claude API que dado un análisis sugiera cantidades + texto explicativo.

### Cuentas corrientes con vencimientos
- **Trigger**: cuando se necesite tracking de plazos.
- **Esfuerzo**: 1 día.
- **Cómo**: ya existe `pagos_ajustes_cc`. Agregar campo de vencimiento + alerta cuando se acerque.

### ~~Horarios de reparto por droguería + countdown al próximo cierre~~ ✅ HECHO 2026-05-01
- Tabla `proveedor_horarios_reparto`, editor UI, `proximo_cierre()` en `services/horarios.py`, countdown live en compra rápida, badge en lista de proveedores.

### Compras Kellerhoff con mínimo (TRF + IVA + indicador stock)
- **Trigger**: Diego va a explicar el detalle.
- **Referencia**: captura del sitio de pedidos de Kellerhoff (28-04-2026). Layout por fila:
  - Producto + foto + chips `» TRF` (transfer) y `+IVA`.
  - Precio normal · Precio TRF (descuentado) · % descuento (ej 39,99 / 41,37) · "Min. N" (cantidad mínima para el descuento) · precio neto (post-descuento + IVA).
  - Semáforo de disponibilidad (verde / rojo) por fila.
  - Input cantidad a pedir.
  - Botón "Mostrar ofertas" abajo.
- **Pendiente**: Diego pasa contexto de qué quiere replicar/integrar de esta vista (probablemente ligado a "Pedidos a droguerías/laboratorios" abajo y al sistema de mínimos por producto).

### Pantalla "Pedidos a droguerías/laboratorios" estilo ObServer
- **Trigger**: cuando madures Compra Rápida y quieras una vista equivalente a la de ObServer para hacer pedidos manuales fuera del flujo "rápido".
- **Esfuerzo**: 1-2 días (la base ya existe — `/order/<id>` y `/compras/rapido` cubren ~70%).
- **Referencia**: captura de ObServer (28-04-2026). Layout:
  - **Header**: proveedor selector + botones `Guardar pedido / Agregar producto / Imprimir`.
  - **Tabs**: `Parámetros` | `Unidades a reponer`.
  - **Panel KPIs por producto seleccionado**: Existencia · Pedidos · Encargados · Mínimo · Máximo · Rep.Auto · Período (Quincenal/Mensual/etc) · Reposición (Mínimo/Máximo) · Venta Anual.
  - **Mini-charts inline**: "Evolución de ventas del período" (Q-3, Q-1) + "Evolución de ventas anual" (12 meses, barras + línea de tendencia).
  - **Tabla central** (scroll horizontal) con columnas: Sugerido · Encargado · Falta (SÍ/NO badge) · **A Pedir** (input editable) · Stock · Producto · Laboratorio · Precio · Motivo (Mínimo/Máximo) · Es Fraccionado · Cant.Disp · Disp · Mín.Oferta · Ofertas · Precio · Conflicto · Nombres drogas · Nombres drogas presentación.
- **Lo que aporta sobre lo que ya tenemos**:
  1. Mini-charts inline por producto seleccionado (hoy abrimos el modal `_grafico_historico` por click).
  2. Columna `Conflicto` que marca si hay descuento/oferta mejor en otra droguería para el mismo EAN.
  3. Toggle período/reposición desde la cabecera (afecta toda la tabla en vivo).
  4. Botón `Imprimir` con layout listo para ObServer (no solo XLSX).
- **Cómo**:
  - Reutilizar query de Compra Rápida pero con UI tabular tipo Excel.
  - Endpoint `/api/compras/conflictos?ean=...` que devuelve si hay mejor opción en otra drog.
  - Mini-chart por fila usando `Chart.js` con `type: 'bar'` de altura ~40px.
- **Relacionado**: este flujo se complementa con el de horarios de reparto (arriba) — pantalla unificada de "armado de pedido" donde ves countdown + sugerido + conflictos.

---

## 🐞 Bugs conocidos / limitaciones

### Pedidos sin link a ObServer
- **Síntoma**: items "sin link" en Indicadores.
- **Workaround**: botón "🔗 Vincular ahora" que matchea por descripción + lab.
- **Solución definitiva**: bridging automático al crear el pedido, no después.

### `obs_clientes` no tiene `IdObraSocial`
- **Síntoma**: no se puede cruzar clientes con OS desde el catálogo actual.
- **Bloqueante**: ObServer debe agregar el campo en `DW.Clientes` o tenemos que mapear via dispensas.

### Productos con `fecha_baja` en ObServer no aparecen por defecto
- **Síntoma**: el user busca un producto y no aparece porque está dado de baja en el catálogo de ObServer.
- **Solución actual**: badge "BAJA" con opacidad, toggle "Solo activos" si quiere filtrar.

### Migraciones inline en `init_db()`
- **Síntoma**: cada cambio de schema requiere agregar `ALTER TABLE IF NOT EXISTS`. Frágil para cambios complejos (renombre, drop, mover datos).
- **Plan**: migrar a Alembic cuando aparezca un cambio que no se pueda hacer así.

### ~~Auto-sync del DockerPanel hace hammer-loop al fallar~~ ✅ HECHO 2026-05-01
- `last_attempt` se persiste en `agente_config.txt` al inicio de cada intento. `_debe_correr_ahora` aplica backoff exponencial (30→60→120→240 min) cuando `_auto_sync_fallos > 0`. Solo se libera cuando el sync tiene éxito y resetea `fallos=0`.

### ~~Post-check del DockerPanel da falsos positivos por logs históricos~~ ✅ HECHO 2026-05-01
- `_post_check_web` busca el último "Starting gunicorn" y solo escanea desde ahí.

### ~~`init_db()` backfills en boot~~ ✅ RESUELTO 2026-05-02
- Backfills (`producto_codigos_barra`, `producto_precios_hist`) removidos del thread de boot.
- Ahora solo corren si `RUN_BACKFILLS=1` está seteado, o manualmente con `python scripts/run_backfills.py`.
- Boot de Render ya no toca la DB para backfills en ningún deploy normal.

---

## ✅ Hechos recientes (histórico)

- 2026-04-25: **`field_inference.py` central + endpoints `/api/inferir/*`** — diccionario de datos de campos del dominio (núcleo: ean, codigo, descripcion, cantidad, precio, descuento) + funciones reusables: `inferir_tipo_valor`, `inferir_campo_por_header`, `inferir_columnas`, `relacion_aritmetica`, `detectar_campos_factura`. 4 endpoints HTTP en `routes/inferencia.py`. 13 tests de endpoints + 65 tests del módulo. Botón "⚡ Auto-detectar (server)" en `converter_pick.html` que reemplaza JS local.
- 2026-04-25: **Wizard de ofertas con OCR** — acepta XLSX, PDF (texto + escaneado), JPG/PNG/WEBP/etc. Fallback automático si `extract_tables` no encuentra: `helpers.extract_text_with_ocr_fallback` → tokenización por línea → matriz best-effort. Botones "Plantilla rápida" para preset descuento+mín o solo descuento.
- 2026-04-25: **Trigram index en obs_productos** — `pg_trgm` + GIN gin_trgm_ops para acelerar `ILIKE '%...%'` (full scan → bitmap index ~0.7ms).
- 2026-04-25: **Matcher central `producto_matcher.py`** — `match_producto(target=...)` reemplaza primitivas duplicadas en observer_matcher, vincular_pedido_observer y ofertas_import. Soporta `Producto` y `ObsProducto`. 28 tests específicos.
- 2026-04-25: **Importador de ofertas (Fase B parte 1)** — `/ofertas/import` con wizard de 4 pasos: subir → mapear columnas → revisar → confirmar. Snapshot del archivo, validación contra catálogo, dropdown manual para items no encontrados. Excel `%` reconocido.
- 2026-04-25: **Alerta sync fallido** — banner + endpoint + `estado_syncs()`.
- 2026-04-25: **CI mínimo** — workflow GitHub Actions con syntax + pytest.
- 2026-04-25: **Test isolation fixes** — autouse fixture + mock de `entorno`.
- 2026-04-25: **Bug `_bulk_upsert_productos`** — falta de flush entre llamadas → UNIQUE violation.
- 2026-04-25: **Simplificación de ramas** — eliminada `desarrollo`, todo trabajo en `main`.
- 2026-04-25: **Esqueleto del manual de usuario** — 22 archivos en `docs/manual/`.
- 2026-04-25: **Vista materializada `mv_stats_drogas`** — pre-calcula agregados por monodroga + banner de frescura + auto-refresh post-push.
- 2026-04-25: **Indicadores del pedido** — modal con 5 tabs + sub-modal alternativas.
- 2026-04-25: **Estadísticas por droga** — comparación de labs con 12+ gráficos.

---

**Cómo mantener este doc:**
- Cuando agregues una idea, ponela en la sección que corresponda.
- Cuando completes algo, movelo a "Hechos recientes" con la fecha.
- Si una idea cambia de prioridad, actualizá el trigger.
