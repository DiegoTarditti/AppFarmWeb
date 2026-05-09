# App Seguimiento — cómo sigo en casa

Estado al **2026-05-09** al cerrar la sesión en casa. Esta nota es para
arrancar la sesión siguiente sin volver a leer todo el chat.

## Lo más reciente (sesión 2026-05-09)

### Tirada larga de migración UX al theme-emerald

Rama `feat/migracion-pantallas-emerald` pusheada con 17 commits.
PR pendiente cuando quiera mergear:
https://github.com/DiegoTarditti/AppFarmWeb/pull/new/feat/migracion-pantallas-emerald

**Pantallas migradas** (17 + base.html):
- Closure flujo factura/proveedor: `provider_invoices`, `provider_mappings`,
  `invoice_items`, `pick_fields` (commit `ff77da2`).
- Home pulido en 5 iteraciones: shapes integradas, Personalizar movido,
  sidebar sin emojis duplicados, icon-tile en línea con título de entity
  cards, page-header + hero en la misma fila (gana ~100px above-fold),
  shapes eliminadas, hero con pattern líneas + dots fade.
- order_detail Etapa 1 (estructural — top-bar, step-cards, banners,
  prop-input) en `305a00d`. Etapa 2 (tablas internas) resuelta pasivamente
  por los overrides globales.
- Compras flow 100% migrado: `compras_dia` (`23cc2ba`),
  `compras_dia_armar` (`4678d09`), `compras_rapido` (`145070c`),
  `compras_transfers` (`f0486b3`).
- `productos.html` (`1867447`).

**El truco**: commit `31900a7` agregó overrides en `body.theme-emerald`
dentro de `base.html` que reasignan TODAS las clases Tailwind viejas
(`text-[#1e1e1e]`, `bg-white`, `bg-amber-50`, etc.) a tokens DS
equivalentes. Pantallas con miles de líneas de Tailwind hardcoded se
migran agregando solo `body_class theme-emerald` y heredan dark theme
sin tocar HTML interno. Extendido en `f18611f` (bg pasteles cremas) y
`1867447` (`bg-surface-input`/`bg-surface-head` named).

**Mock comparativo del hero**:
[docs/mocks/04_hero_decoration_options.html](mocks/04_hero_decoration_options.html)
con 7 variantes (A-G). Diego eligió B+C combinadas.

**Limpieza**:
- 2 mocks viejos borrados (`mock_pedidos_nuevo`, `mock_stock_excedente`)
  + sus rutas en `routes/admin.py` y link en `pedidos_nuevo.html`
  (commit `0e831f4`, -545 L).
- `docs/docker_wsl_fix.md` actualizado con paso 2 del fix
  (`wsl --import-in-place` post-BIOS, commit `56ee2ef`).
- `docs/mejoras_pendientes.md` con sección "🎨 Migración UX emerald"
  documentando estado y pendientes (commit `ec810c5`).

**Lección clave (PR #23)**:
No quitar funcionalidad por estética. El rediseño inicial del home dejó
solo 6 cards de "Acciones frecuentes", quitando informes/BI/productos/
clientes/OS. Hubo que restaurarlas. Regla: en migraciones visuales,
**conservar todos los entry points** del menú.

**Pendiente para próxima sesión**:
- order_detail Etapa 3 (botones internos `bg-emerald-600/700`,
  `bg-red-600/700` → `btn btn-mint/primary/danger`; modales match manual
  + chart histórico).
- compras_dia_armar Etapa 2 (cells de tabla con badges de drog,
  ofertas, sugerencias — ~150 condicionales).
- `purchase_suggest` (519L), `purchase_results` (764L), `purchase_batch`
  (92L), `purchase_processed` (74L), `purchase_analysis` (171L).
- Resto del sistema (~108 templates): catálogo (vademecum, estadisticas
  drogas), labs, informes (9 sub-pantallas), OS/Clientes (13 templates),
  admin (10 templates).

Inventario detallado por flujo en
[docs/mejoras_pendientes.md](mejoras_pendientes.md) sección "🎨 Migración
UX al theme-emerald".

### Bonus — Docker reparado (mañana)

Causa final descubierta: tras habilitar virtualización en BIOS (paso 1 del
[docs/docker_wsl_fix.md](docker_wsl_fix.md)), faltaba **registrar la distro
docker-desktop en WSL**. El VHD existía pero WSL no la veía. Solución:
`wsl --import-in-place docker-desktop "<ext4.vhdx>"`. Después
`docker ps` responde en ~5s.

---

## Sesión 2026-05-08

### Motor de búsqueda / matching de productos — ampliado y optimizado

PR #24 (`fix/modulo-packs-mejoras` → `dev`, mergeado) reescribe gran parte del
matcher. Foco: importación de ofertas multi-lab (modo droguería) que antes
saltaba el fuzzy match para no clavarse con archivos grandes.

**Performance — inverted index `{token → set(producto_ids)}`:**
- Antes: 1056 items × 60k productos locales × 122k obs = 128M+ jaccards (4-7 min).
- Ahora: cada item evalúa solo los productos que comparten ≥1 token significativo
  con el input (~50-200 candidatos por item). **15-30s totales para 1056 items**.
- Aplicado en `match_productos_bulk` (fase 1 locales + fase 3 obs) y
  `buscar_candidatos_bulk` (prefetch de candidatos).
- Cache de tokens en pool pre-cargado (`c._toks_cached`) — evita 5M
  tokenizaciones redundantes.

**Exactitud — normalización mejorada de descripciones:**
- **Decimales**: `0.5` y `0.50` ahora son equivalentes (antes splitteaba en
  "0 5" vs "0 50"). XANAX 0.5 vs 0.50 → score 0.6 → 1.0.
- **`x` pegado a número**: `x30` → `x 30`, `compx30` → `comp x 30`. Resuelve
  los DIABESIL AP 1000 mg comp.rec.x30 que el proveedor exporta sin espacios.
- **Bigramas/trigramas duplicados**: el preprocesador `_normalizar_descripcion_proveedor`
  ahora detecta `AP 850 AP 850` → `AP 850` (antes solo limpiaba 1-grams
  adyacentes tipo "1000 1000").
- **Stopwords faltantes**: `grs/gms/mgs` (variantes plural unidad), `oft/col/nasal`
  (formas oftálmicas, colirio, nasal), `emu/cre/ung/lec` (variantes cortas
  de emulsión, crema, ungüento, leche). Antes `TROPIOFTAL F sol.oft` no
  matcheaba con `TROPIOFTAL F COL` por desfase de stopwords.

**Auto-matching:**
- Threshold default bajado a `0.9` (antes `1.0`).
- **1 solo candidato post-dedup** → auto-match independiente del score
  (es la única opción posible).
- **Empate en score** → auto-match solo si el alfabeta es el mismo (= mismo
  producto duplicado entre local y observer); si el alfa difiere, manual.
- **Dedup por alfabeta también en bulk**: antes `buscar_candidatos_bulk`
  dedupaba por `('prod', id)` vs `('obs', id)` → mostraba duplicados. Ahora
  agrupa por alfabeta como ya lo hacía la versión single-item.

**Resultado en archivo real (1056 items, droguería CIAFARMA):**
- Iteración 1 (post-índice): 8 OK + 744 fuzzy + 304 pendientes (71% auto).
- Iteración 2 (post-dedup): 147 OK + 744 fuzzy + 165 pendientes (84% auto).
- Iteración 3 (post-normalización + 1-cand): pendiente medir, esperado <100.

**Observación importante (pendiente prod):**
- DB de Render se cayó hoy a las 4:34 PM por **disco lleno (93%)**. Plan
  upgradeado a Basic-4gb pero el disco sigue en 1GB (es separado del RAM).
  Soporte de Render abierto. Cuando vuelva, identificar qué tabla creció
  tanto: `obs_ventas_mensuales`, `obs_productos`, `home_card_clicks` son
  candidatos. Query:
  ```sql
  SELECT relname AS tabla, pg_size_pretty(pg_total_relation_size(relid)) AS size
  FROM pg_catalog.pg_statio_user_tables
  ORDER BY pg_total_relation_size(relid) DESC LIMIT 15;
  ```

---

## Sesión 2026-05-07

### Acceso remoto a la PC de oficina (Tailscale + SSH)

Pendiente terminar mañana:
- ✅ Tailscale instalado y andando — esta PC en tailnet es `badia-oficina-1`
  (IP `100.101.4.71`).
- ✅ OpenSSH Server instalado (`Add-WindowsCapability` completado).
- ✅ `sshd` arrancado, responde a `ssh Lisandro@localhost`.
- ⏳ **Falta**: confirmar que el user `Lisandro` tenga password de Windows
  configurada (no me la sabía hoy). Si no la recuerda:
  `net user Lisandro *` desde PowerShell admin para resetearla.
- ⏳ **Falta**: dejar `sshd` en arranque automático:
  `Set-Service -Name sshd -StartupType Automatic`
- ⏳ **Falta**: regla firewall para puerto 22:
  `New-NetFirewallRule -Name sshd -DisplayName 'OpenSSH Server' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22`
- ⏳ **Falta**: probar desde casa: `ssh Lisandro@100.101.4.71`.
- 🔮 **Opcional, después**: configurar clave pública (`ssh-keygen` en casa,
  copiar `.pub` a `C:\Users\Lisandro\.ssh\authorized_keys` en oficina) para
  no tipear password cada vez.

### Features pusheadas hoy (PR #17 mergeado a main)

Render debería haber deployado. Pendiente probar con data real:
1. `docs/pendiente_de_prueba_2026-05-07.md` — lista completa con plan ordenado
   por prioridad (mín corregido > rotación 3m > ofertas multi-lab > informe
   correcciones > crear drog manual).
2. Reverts documentados en sección 9 del mismo doc por si rompe algo.

### Refactors de simplificación (no urgente)

Auditoría guardada en `docs/simplify_2026-05-07.md`. Top 3:
1. **[30min]** Migrar `compras_dia_armar.html` a `_grafico_dual_panel.html`
   (borra ~250 líneas duplicadas, el componente ya existe pero no se adoptó).
2. **[1h]** Extraer `cargar_ventas_12m` + `clasificar_min` a `purchase_helpers.py`
   — duplicación grande en 4 callsites de `compras_dia.py` + `informes.py`.
3. **[15min]** Limpiar imports dentro del loop en `compras_dia.py:625-649`.

**No tocar antes** de validar las features de hoy en prod.

### Agente nuevo: `simplify`

Guardado en `.claude/agents/simplify.md`. Revisa working tree o últimos N
commits buscando duplicación, simplificaciones obvias y bugs sutiles. Solo
lectura. Para correrlo: `/simplify` o invocar como subagent_type.

---

## Sesión 2026-05-04

**Etapa 1 del plan de simplificación de catálogo (en curso):**
Cortar duplicación de EANs en `productos`. Hoy un EAN puede vivir en 4 lugares
(`codigo_barra`, `alt1`, `alt2`, `alt3`) + en la 1-a-N `producto_codigos_barra`.
Objetivo: dejar UN solo lugar.

Hecho hoy:
1. ✅ Render con plan Starter activado (shell + más memoria).
2. ✅ Pre-poblamos `productos` en Render desde `obs_productos` (60157 filas).
   Script: `scripts/popular_productos_desde_obs.py`. Card en `/admin`.
3. ✅ Sincronizamos `laboratorios` desde `obs_laboratorios` (1989 creados +
   10 vinculados). Lookup robusto a variantes (case/acentos/espacios).
4. ✅ Update retroactivo de `productos.laboratorio_id` (55867 actualizados).
5. ✅ Backfill `producto_codigos_barra` de los EANs principales (60157).
6. ✅ Refactor: quitar lecturas de `alt1/2/3` en `helpers.py` y matcher.
   PR #6 mergeado el 2026-05-04.

Pendiente para retomar en casa:
1. **Validar 1-2 días** que todo siga andando OK con el código nuevo.
2. **Setear env** `EAN_LEGACY_ALTS_DISABLED=1` en Render (defensivo, ya las
   lecturas no las leen pero por si quedó alguna).
3. **DROP COLUMN** `alt1/2/3`: agregar migración inline `ALTER TABLE productos
   DROP COLUMN IF EXISTS codigo_barra_alt1/2/3` + remover del modelo en
   `database.py`. PR aparte.

Después → empezar **Etapa 2** (unificar `barcode_mappings` + `equivalencias_proveedor`
en una sola tabla `mapeo_proveedor`).

## Bug pendiente — parser de movimientos Observer (signos invertidos)

**Detalle del problema** (detectado leyendo el detalle de movimientos en
Observer):
- **Ventas** vienen con `Envases = -1` (negativo, "vendí 1 unidad").
- **Devoluciones de venta / NC** vienen con `Envases = +1` (positivo).

**Bug actual**: el parser usa `int()` directo y borra el `-` (o aplica
`abs()` ciego). Resultado: las cantidades se inflan — devoluciones se suman
como si fueran ventas en lugar de restarse.

**Fix correcto** (cuando tengamos export HTML/XLS para procesar):
- `Tipo == 'Venta'` → sumar `abs(envases)` (cantidad real vendida).
- `Tipo == 'Devolución de venta'` o `'Nota de crédito'` → **restar**
  `abs(envases)` del total.
- NO aplicar `int()` ciego que pierda el signo.

Bloqueante: necesito el formato de export HTML/XLS de Observer para tocar
el parser. Sin eso solo queda documentado.

## Sesión tarde 2026-05-04 (post-merge PR #5/#6/#7/#8/#9)

**Hecho:**
- Mergeados PRs y deploy live en Render.
- Pre-poblar productos + sync labs corrido en Render via shell (60157 productos
  creados, 55867 con laboratorio_id, 1989 labs nuevos).
- Card nuevo "🔬 Ventas por droga / producto / médico / fecha" agregado en
  `/informes` (sección Catálogo y ventas).
- Pantalla `/informes/ventas-multi` armada (rama `feat/informe-ventas-multi`):
  - 4 filtros: rango fechas (default 30d), droga, producto, médico — los
    3 últimos con autocomplete.
  - 5 modos de agrupación: producto / droga / médico / mes / día.
  - Top 200 ordenados por cantidad. Columnas: ítem, ops, cant, importe, % total.
  - Endpoints nuevos: `/api/informes/buscar-medico`, `/api/informes/buscar-producto-obs`.
  - Reusa `/api/informes/buscar-droga` existente (devuelve `descripcion`, JS
    soporta `nombre || descripcion`).

**Pendiente (rama `feat/informe-ventas-multi` sin commitear):**
- Verificar autocomplete del médico en navegador (user reportó vacío).
  Debug: F12 → Network al tipear, ver request a `/api/informes/buscar-medico`.
  Si 404 → restart web. Si 500 → logs.
- Una vez validado, commit + PR a main.
- Pulido futuro: paginar resultados (ahora top 200 fijo), export XLSX, drill-down
  (click en una fila para sub-agrupar), chart de evolución temporal.

**Continuación de Etapa 1 (catálogo simplificación):**
1. Validar 1-2 días con el código nuevo (lecturas alt1/2/3 ya removidas).
2. Setear env `EAN_LEGACY_ALTS_DISABLED=1` en Render (defensivo).
3. PR para DROP COLUMN `alt1/2/3` + remover del modelo.
4. Etapa 2: unificar `barcode_mappings` + `equivalencias_proveedor`.

## Otras cosas hechas hoy (mañana)
- **`EquivalenciaProveedor`**: tabla nueva para guardar match manual texto→producto
  del wizard de ofertas (antes el match manual se perdía). Estrategia 0 del
  matcher consulta esta tabla antes del fuzzy.
- **Matcher fuzzy más rápido**: pre-filtro ILIKE por al menos un token >=3 chars
  (universo de 60k → ~cientos). Antes tardaba 18s para "Buscar similar"; ahora 1-2s.
- **Tokenización mejorada**: separa "300MG"→"300 mg" y "x30"→"x 30", re-mergea
  vitaminas (b12). Captura más matches por descripción.
- **Inferencia de columnas**: header con "producto/descripcion/nombre" excluye
  ean/codigo. Por contenido: si los valores tienen espacios o >20 chars, no se
  proponen como código. Caso testeado: "Cód. Producto" sigue mapeando a `codigo`.
- **Compra del día**:
  - `stock <= mín` (antes `<` estricto) + `a_pedir mín 1` cuando stock=mín.
  - Cobertura objetivo configurable en URL `?target=N` (default 7d).
  - Universo: bajo mín OR `stock cubre <N días`.
  - Badge "No urgente" para los que entran solo por cobertura.
  - Filtros nuevos: "Solo urgentes (≤ mín)".
  - `a_pedir = 0` si `u12m=0` o `sin_mov_60d`.
  - No-pedir aparecen ahora con badge + botón Reactivar (antes ocultos).
  - Sugerencia de mín usa `purchase_engine` (estacionalidad + crónicos).
  - Buscador "+ Agregar producto" en línea de arriba.
  - Panel gráfico arranca colapsado.

## Estado anterior (sesión 2026-04-28)

## Cómo arrancar la app en casa

1. Levantar Docker: `docker-compose up -d`. Esperar 5-10s a que `db` esté healthy y `web` levante.
2. Restaurar el dump de datos:
   ```bash
   gunzip -c dumps/seed_pedidos_dia.sql.gz | docker-compose exec -T db psql -U postgres -d farmacia
   ```
   Si la DB está limpia (post `init_db`), entra directo. Si tiene data vieja, primero TRUNCATE — ver `dumps/README.md`.
3. Login con user `pedidos` / pass `pedidos123` (debe cambiar al primer ingreso).
   - Admin sigue siendo `admin` / `cambiar123`.
4. Pantalla principal del flujo: `/compras/dia` → "Armar pedido →" en alguna droguería.

## Lo hecho hoy (resumen)

- Rol `pedidos` con seed automático + redirect a `/compras/dia` post-login.
- Sidebar oculto + guard global que bouncea cualquier path fuera del flujo.
- Pantalla `/compras/labs-drogerias` con matriz de checkboxes lab × drog (filtro multi-token, ajax toggle).
- Limpieza de 5 labs duplicados sin ventas.
- Panel sticky con gráfico (Año rico + Mes simple) que se actualiza al click en una fila.
- 3 series en Mes: **Salidas** (verde, ventas), **Pedido** (amber, `pedido_emitido`), **Entradas** (azul, facturas).
- Filtros tokenizados: producto + lab + "Solo venta libre" + "Solo con sugerencia".
- Buscador "+ Agregar producto…" con autocomplete.
- Modelo `PedidoEmitido` + `PedidoEmitidoItem` (con campos para 2 vías de recepción: `cantidad_revisada_op` manual + `cantidad_confirmada_obs` automática).
- Pantalla `/pedidos-emitidos` (lista) y `/pedidos-emitidos/<id>` (recepción row-by-row).
- Sección "🔁 Pendientes anteriores" en el armado, con chips clickeables que reagrega productos.
- Sugerencia de mínimo (Subir/Bajar/OK) usando `purchase_engine.analyze_product` (estacionalidad + prorrateo + crónicos).
- Badge "Prom Vtas" para crónicos (CV<0.30 y vende 8/11 meses).
- Datos demo: 2 pedidos emitidos con mix RECIBIDO/NO_VINO/PENDIENTE.

## Para retocar gráficos en casa

Foco que pediste: **ver cómo queda el chart con la recepción ya hecha.**

Caminos rápidos:

1. Login pedidos → `/compras/dia/armar?prov=<id>` → click en cualquier producto del pedido demo #1 → mirar el chart de Mes (debería tener barras amber "Pedido" en el día -5 y posibles barras azules "Entradas" si hay facturas reales con ese EAN).
2. `/pedidos-emitidos/1` → modificar la revisión (ej. marcar otra como NO_VINO) → guardar → ver cómo cambian los chips del armado.
3. Crear un pedido nuevo desde `/compras/dia/armar`, marcar recepción → ver el ciclo entero.

## Pendientes (orden sugerido)

1. **Estilo del chart de Mes**: las 3 series como barras agrupadas pueden quedar muy comprimidas con 30 días. Probar:
   - Pedido y Entradas como puntos/iconos sobre la línea de Salidas.
   - O barras apiladas con colores distintos (Salidas neutro, Pedido y Entradas con highlight).
2. **Endpoint para cargar confirmación Observer** desde un export CSV/XLSX (botón "Importar ingreso" en `/pedidos-emitidos/<id>`).
3. **Plantilla específica de exportación "pedidos día"** por droguería (lo conversamos: tipo='pedidos_dia' en `PlantillaExportacion`).
4. **Botón "Aplicar sugerencias"** que persista los nuevos mínimos (cuando exista push a Observer; por ahora podría guardar local).
5. **Contador en header** "X subir · Y bajar" + ordenar por sugerencia urgente arriba.
6. **Commit + push** de todo lo de hoy (lab×drog, recepción 2-vías, gráfico mixto, sugerencias, dump). No commiteado todavía.
7. **Gráfico de médicos** (extenso, dejado para después). Pensar pantalla / chart con métricas
   por médico: top prescriptores, evolución temporal, ranking por OS, etc. Datos en `obs_ventas_detalle`
   (`medico_observer`, `medico_matricula_observer`) — joinea con `obs_medicos`. Es trabajo grande,
   abordar como feature aparte cuando los pendientes de Compra del día estén estables.

## Archivos clave que toqué

- `database.py` — modelos `LaboratorioDrogueria`, `PedidoEmitido`, `PedidoEmitidoItem` + columnas extra.
- `auth.py` + `routes/auth_routes.py` — rol `pedidos`, seed, guard global, redirect cambio password.
- `routes/compras_dia.py` — endpoints buscar, emitir, recepción, confirmación-observer; pantalla matriz; sugerencias con `purchase_engine`.
- `routes/informes.py` — endpoints `chart-mes` (3 series) y `ingresos-mes`.
- `templates/compras_dia.html` — sin "← Inicio" para rol pedidos.
- `templates/compras_dia_armar.html` — todo el armado, panel sticky, filtros, búsqueda, sugerencias, emitir.
- `templates/labs_drogerias.html` — matriz lab × drog.
- `templates/pedidos_emitidos_list.html` + `pedido_emitido_detalle.html` — flujo de recepción.
- `templates/base.html` — `_hide_chrome` para gateo de sidebar/topbar.
- `dumps/seed_pedidos_dia.sql.gz` — snapshot de datos.

## Pelusa

- El `init_db` corre con `--preload` en gunicorn (Render) — si rompe el boot por una migración inline, sospechar de eso.
- Las tablas nuevas se agregan al `zombie_names` en `database.py:1081` para evitar `pg_type` huérfanos en deploys.
- El dump excluye `obs_ventas_detalle` (200+ MB). Si querés probar el chart "Entradas" con datos reales necesitás repoblarla — desde la app local sería con el sync ObServer del DockerPanel.
