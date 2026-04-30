# Mejoras pendientes — backlog vivo

Doc maestro de mejoras. Vivo: se actualiza con cada idea/decisión. Cuando algo se hace, se marca ✅ y se agrega fecha.

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

### Bulk queries en `/api/pedido/<id>/indicadores`
- **Trigger**: pedidos de más de 500 items tardan > 3 seg en abrir Indicadores.
- **Esfuerzo**: 1-2 horas.
- **Cómo**: hoy hace varios queries pequeños. Refactorear a 2-3 queries con joins masivos.

### Limpieza periódica de `home_card_clicks`
- **Trigger**: la tabla pasa de 100k filas o se nota lentitud al cargar el home.
- **Esfuerzo**: 15 min.
- **Cómo**: cron en DockerPanel: `DELETE FROM home_card_clicks WHERE clicked_at < now() - interval '90 days'`.
- **Por qué**: solo se usa para el ranking de cards en el home, datos viejos no aportan.

### Migrar PDFs a S3 / Cloudflare R2
- **Trigger**: el bucket de PDFs (facturas + reclamos) pasa de 5-10 GB.
- **Esfuerzo**: 1 día.
- **Cómo**: subir a R2 (más barato que S3), guardar URL en `Invoice.pdf_filename`. Backfill scripted.

### Optimizar `/api/droga/<id>/comparar-labs`
- **Trigger**: comparar 5+ labs tarda > 2 seg.
- **Esfuerzo**: 1 hora.
- **Cómo**: consolidar las queries por lab en una sola con `GROUP BY lab_id`.

---

## 🛠 Calidad de código

### Rutas Flask huérfanas (sin link desde sidebar/templates)
- **Trigger**: cualquier momento, decisión simple.
- **Esfuerzo**: 30 min cada una.
- **Detectadas (route-orphan-finder 2026-04-30)**:
  1. ✅ ~~`/clientes` (clientes_list)~~ — **2026-04-30**: linkeada en sidebar bajo "Obras Sociales" como "Clientes / Pacientes" (templates/base.html).
  2. `/purchase/processed` (purchase_processed) — `routes/purchase.py:360`. No linkeada desde ningún flujo. Decidir: ¿linkear desde `purchase_suggest.html`, agregar al sidebar bajo "Compras", o eliminar?
  3. `/observer/laboratorios` (observer_laboratorios) — `routes/observer.py:914`. Probablemente deprecada (era stepping stone del flujo viejo, ahora se entra directo por `purchase_suggest`). Decidir: eliminar o fusionar.

### Cache de evaluación de alarmas
- **Trigger**: si la página `/admin` se vuelve lenta.
- **Esfuerzo**: 30 min.
- **Síntoma**: cada navegación a `/admin` evalúa alarmas (7 queries). Si el user va `/admin → /admin/alarmas`, se evalúa 2 veces.
- **Solución**: cachear el resultado por 30-60s en memoria de proceso (`functools.lru_cache` con TTL custom o `cachetools.TTLCache`).

### Linter (`ruff`)
- **Trigger**: cualquier momento, gratis.
- **Esfuerzo**: 5 min.
- **Cómo**: agregar job `ruff check .` en `.github/workflows/ci.yml`. Crear `pyproject.toml` con reglas suaves al inicio.
- **Por qué**: detecta imports no usados, variables sin asignar, líneas muy largas. Limpia el código sin esfuerzo.

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

### Branch protection en `main`
- **Trigger**: ya, cuando puedas (5 min).
- **Esfuerzo**: 5 min.
- **Cómo**: GitHub repo → Settings → Branches → Add rule para `main` → "Require status checks" → marcar `syntax` y `tests`.
- **Por qué**: evita pushear código que rompe CI a producción.

### Migrar a Alembic
- **Trigger**: pasamos las ~30 tablas en `database.py` o aparece una migración compleja (renombre, mover datos).
- **Esfuerzo**: 1-2 días.
- **Cómo**: instalar Alembic, generar baseline desde la DB actual. Convertir cada `ALTER TABLE IF NOT EXISTS` inline en una migración versionada.

### Docstrings consistentes
- **Trigger**: cuando un nuevo dev se sume al proyecto.
- **Esfuerzo**: progresivo.
- **Cómo**: convención de Google/NumPy style. Incluir args, returns, raises.

### Pre-commit hooks
- **Trigger**: cuando el equipo crezca o querés más fricción contra commits sucios.
- **Esfuerzo**: 30 min.
- **Cómo**: `pre-commit` con ruff, trailing-whitespace, end-of-file-fixer.

---

## 🎨 UX — pulir el sistema

### Botón "Crear y exportar con plantilla" en pedido auto
- **Trigger**: cuando se use seguido pedido auto y quieras saltarte el paso de ir a `/order/<id>` para exportar.
- **Esfuerzo**: ~1 hora — agregar segundo botón "Crear pedido + exportar plantilla" en `informes_pedido_auto.html` que: (1) crea el Pedido como hoy, (2) en el handler, en lugar de redirigir a `/order/<id>`, arma el JSON `data` con las columnas que la plantilla del lab espera, (3) llama internamente a la lógica de `order_export_plantilla` (extraída a una función reusable) y devuelve el XLSX.
- **Hoy**: hay un cartel verde que avisa que el lab tiene plantilla y que la verás al ir al pedido. Es 2 clicks en lugar de 1.
- **Detalle clave**: el endpoint `/order/<int:pedido_id>/export/plantilla` espera `request.form['data']` con el formato exacto de columnas; hay que construir ese array desde los items del pedido respetando los `field` que la plantilla declare.

### Filtro arriba en Pedidos guardados
- **Trigger**: cuando puedas — la pantalla `/orders` no tiene filtro y con varios pedidos hay que scrollear.
- **Esfuerzo**: ~30 min — input de búsqueda + filtro por lab/estado/fecha.
- **Por qué**: hoy hay que buscar manualmente con Ctrl+F.

### Color de fondo del botón en home (no solo del ícono)
- **Trigger**: cuando el home tenga 10+ botones y se necesite agrupación visual.
- **Esfuerzo**: 30 min — agregar campo `bg_color` al modelo de cards (o reusar uno existente con sufijo `_btn`), exponerlo en el editor, aplicar via `style="background-color: ..."` en el render.
- **Por qué**: hoy se puede personalizar el color del ÍCONO de cada botón, pero el fondo del botón es uniforme. Se necesita poder dar un color distinto al fondo del botón completo para agrupar visualmente (ej. todos los de Mis Informes en un mismo tono).

### Botón "?" contextual del manual
- **Trigger**: ya, cuando puedas (1-2 horas).
- **Esfuerzo**: ~1-2 horas.
- **Cómo**: ya hay esqueleto en `routes/help.py` + `docs/manual/`. Falta:
  - Botón "?" flotante en `base.html`.
  - Drawer con marked.js para renderizar el `.md`.
  - Mapeo de URL → sección del manual.

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

### Backup explícito a almacenamiento externo
- **Trigger**: ya, cuando puedas.
- **Esfuerzo**: 1 hora.
- **Cómo**: cron en DockerPanel: `pg_dump` + subir a Drive/Dropbox/R2. Mensual o semanal.
- **Por qué**: hoy el único backup es Render (que también puede fallar). Tener una copia más es seguridad.

### Sentry o similar para errores en prod
- **Trigger**: cuando lleguen 2+ farmacias a usarlo.
- **Esfuerzo**: 2 horas.
- **Cómo**: `sentry-sdk[flask]` con DSN en env var. Captura excepciones automáticamente.

### Logs centralizados
- **Trigger**: si Render se vuelve insuficiente (logs limitados a últimas N horas).
- **Esfuerzo**: 4 horas.
- **Cómo**: integrar con Logflare, Better Stack, o BetterStack Logs.

### Health check page interno
- **Trigger**: ya, opcional.
- **Esfuerzo**: 30 min.
- **Cómo**: `/admin/health` con: estado de DB, conteo de tablas, último sync, espacio disponible, versión deployada. Útil para diagnóstico rápido.

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

### Notificaciones de alarmas críticas a Telegram
- **Trigger**: cuando el módulo de alarmas (`/admin/alarmas`) detecta una crítica o alta, mandar push al celular del admin para no depender de que entres a la página.
- **Esfuerzo**: 2 hs.
- **Cómo**:
  1. Crear bot con `@BotFather`, conseguir TOKEN. Mandar `/start` para conseguir `chat_id`.
  2. Env vars en Render: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (1 token + 1 chat por farmacia, eventual N).
  3. Endpoint `POST /api/admin/alarmas/notificar` que evalúa, deduplica con tabla `alarmas_notificadas(nombre, ultima_notif)` y manda al bot. Solo notifica si `ahora - ultima_notif > 4h` o si la alarma RESUCITA después de estar OK (= salió + entró de nuevo).
  4. GH Actions cron cada 15 min llamando ese endpoint con `X-Cron-Secret`.
  5. Mensaje formato: `🚨 Sync ObServer parado · 52h sin sync · /admin/alarmas`.
  6. Severidades a notificar: `critica` siempre, `alta` opcional con flag, `media`/`baja` no.
- **Por qué Telegram > WhatsApp**: setup 5min vs 30min+, 0 costo vs 0.005-0.05 USD/msg, API simple vs Cloud API/Twilio, no expone tu número.
- **Multi-farmacia futuro**: 1 bot global + N chats (uno por farmacia). Cuando se venda a más, cada farmacia reporta a su grupo de Telegram.

### Setup Tailscale + VSCode Remote SSH (doc listo, falta ejecutar)
- **Trigger**: cuando se quiera editar/operar la PC farmacia desde cualquier laptop como si fuese local.
- **Esfuerzo**: 30 min de instalación.
- **Doc completo**: ver [docs/tailscale_vscode_remoto.md](tailscale_vscode_remoto.md) — paso a paso para Windows farmacia + laptop.
- **Decisión pendiente**: estructura de cuentas Tailscale (ver doc para opciones — una cuenta tuya con todas las PCs, vs cuenta de Lisandro en farmacia + cuenta propia + share).
- **Por qué**: te queda terminal + editor remoto en cualquier lado, sin abrir puertos. Reemplaza muchos casos de uso del panel remoto (que sigue siendo útil para celular, multi-farmacia, audit trail).

---

## 🌟 Features pendientes

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

### Horarios de reparto por droguería + countdown al próximo cierre
- **Trigger**: cuando empieces a usar Compra Rápida en serio — saber a qué hora cierra cada droguería ayuda a priorizar emisión.
- **Esfuerzo**: 4-6 horas.
- **Referencia**: el widget que tienen las droguerías en su web (matriz por día de la semana × franjas horarias 07:10/10:20/15:00/19:00, contador "Faltan 03:29:24 hs al cierre del próximo reparto", fecha del próximo reparto).
- **Cómo**:
  - Tabla `proveedor_horarios_reparto(proveedor_id, dia_semana 0-6, hora TIME)` — cada fila un slot.
  - UI editor en `/provider/<id>/horarios` (matriz tipo grilla, igual que la captura).
  - Helper en server: `proximo_cierre(proveedor_id) → datetime` calcula el próximo slot futuro respetando día actual.
  - Widget en compras_rapido (panel transfers o sticky header): chips por droguería con countdown live (`HH:MM:SS`) hasta el próximo cierre. Si quedan menos de N min → chip rojo "Cerrá ahora si querés que entre hoy".
- **Por qué**: el principal driver de ansiedad al armar un pedido grande es perderse el cierre. Tener el contador a la vista decide cuándo emitir.

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

### Auto-sync del DockerPanel hace hammer-loop al fallar
- **Síntoma**: si el sync falla por timeout, `last_run` solo se persiste en éxito → cada 60 s el loop ve `delta_min >= arranque_min(180)` → vuelve a disparar → gunicorn worker timeout → repeat. Llena los logs de `WORKER TIMEOUT` y `Limpiando Render…`.
- **Workaround actual**: apagar `autosync_enabled` en `agente_config.txt`.
- **Solución definitiva**: persistir `last_attempt` en cada intento (no solo `last_run` en éxito) y aplicar backoff exponencial cuando hay N fallas seguidas. Ver `DockerPanel/docker_panel.py:1462`.

### Post-check del DockerPanel da falsos positivos por logs históricos
- **Síntoma**: después de un restart exitoso, el aviso `⚠ Post-check: la app parece haber crasheado al arrancar — Detecté: traceback, importerror` aparece igual porque scanea el log entero, incluyendo trazas anteriores al restart.
- **Solución**: limitar el scan a las líneas que tienen timestamp posterior al `Starting gunicorn` más reciente, o usar `docker logs --since=<timestamp>` con el momento del restart.
- **Workaround actual**: ignorar el aviso si la app responde (curl /health → 200).

### ~~`init_db()` bloquea el boot del worker en Render~~ ✅ MITIGADO 2026-04-28 (workaround `--preload`)
- **Síntoma**: backfills inline en migración (ej. `producto_codigos_barra`, `producto_precios_hist`) corren en cada worker al import time. En Render con `--workers 2` sin `--preload`, dos workers ejecutan los backfills en paralelo sobre el Postgres remoto, hacen contención, el HTTP port no abre a tiempo y Render aborta el deploy con `No open HTTP ports detected`.
- **Workaround aplicado** (2026-04-28): `--preload` en el CMD del Dockerfile → master corre `init_db` una sola vez antes de forkear workers. Verificado en `Dockerfile:30`. Backfills movidos a thread async (ver `_ejecutar_backfills_async` en `database.py:1305`).
- **Pendiente solución definitiva**: mover backfills a management script one-shot (o disparar con env-var) para que NO corran en el path crítico de boot. Hoy con el thread async ya no es problemático en producción.

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
