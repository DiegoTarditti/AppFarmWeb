# App Seguimiento — cómo sigo en casa

Estado al **2026-05-04** al cerrar la sesión en la oficina. Esta nota es para
arrancar la sesión siguiente sin volver a leer todo el chat.

## Lo más reciente (sesión 2026-05-04)

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
