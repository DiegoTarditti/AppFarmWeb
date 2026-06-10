# Mejoras pendientes — backlog vivo

Doc maestro de mejoras. Vivo: se actualiza con cada idea/decisión. Cuando algo se hace, se marca ✅ y se agrega fecha.

> ⚠️ **Lo URGENTE vive en [`docs/backlog_urgente.md`](backlog_urgente.md)** — lista corta priorizada (P0/P1/P2). Este doc es el backlog general (features + mejoras no urgentes).

---

## ✅ Componente reusable `cliente_picker` (2026-05-28 → 2026-06-10)

**Origen**: la sección "👤 Cliente + Dirección" de `/pedido/nuevo` está
bien armada y ya funciona (buscador multi-palabra, dropdown de domicilios
guardados, geocoder de dirección nueva, modales de alta/edición). Diego
propuso extraerla como **componente reusable** para usarla desde varias
pantallas en vez de duplicar HTML/JS.

**Casos de uso identificados**:
- `/atencion` — al atender un chat, botón "→ Crear pedido" que abre el
  picker con teléfono del cliente precargado.
- `/caja` — buscar cliente para refacturar / vincular ticket.
- Futuras pantallas — anulación por DNI, histórico por cliente, etc.

**Implementado (2026-06-10)** — commits `53bf194`, `f0eca4e`, `ed7c0fb`, `f94d915`:

1. ✅ **Macro Jinja** en `templates/_cliente_picker.html` (`cliente_picker()`
   y `cliente_picker_modales()`).
2. ✅ **Módulo JS** en `static/js/cliente_picker.js`. API pública:
   `ClientePicker.init({onAddressChange, onClienteSelected, onClear})`,
   `.getValues()`, `.clear()`, `.loadCliente({cliente_id, observer_id})`,
   `.pickCli()`, etc.
   - **Limitación actual**: una sola instancia por página (IDs fijos
     `pCliente`, `pDir`, etc.). Multi-instancia requiere namespacing —
     ver entry "Namespacing multi-instancia" más abajo.
3. ✅ **Endpoints `/api/clientes/*`** en `routes/clientes.py` (8 endpoints):
   `/buscar`, `/ficha`, `POST /` (crear), `POST /<cid>` (editar),
   `/observer/<oid>/domicilios`, `/geocodificar`, `/separar-direccion`,
   `POST /domicilios/<dom_id>/geo`. Los viejos `/reparto/api/*` y
   `/reparto/cliente*` se borraron una sesión después de la migración
   completa de callers (commit `ed7c0fb`).
4. ✅ **Migración de pantallas**:
   - `/pedido/nuevo` — usa el macro. Bajó de 787 → 366 líneas.
   - `/atencion` — botón "📝 Pedido" en toolbar de conversación que abre
     `/pedido/nuevo?observer_id=X`. Receiver: `pedido_nuevo.html` detecta
     query param y llama `ClientePicker.loadCliente()`.
   - `/reparto` — sigue con su buscador embebido propio (layout muy
     distinto al picker, no es swap directo, ver entry "Aplicar a /reparto"
     abajo).

**Lecciones de la implementación**:
- El "modo modal" del plan original NO se hizo. Hoy solo hay modo
  embedded (`{{ cliente_picker() }}` se incrusta en un `<div>`). Si
  aparece caso de uso del modal, se agrega.
- El "namespacing por prefix" se simplificó a IDs fijos. Una sola
  instancia por página alcanza para todo lo que tenemos hoy.

---

## ⏳ Pendiente — Aplicar `cliente_picker` a `/reparto` (2026-06-10)

**Bloqueo**: el HTML de `/reparto` usa un layout `label.muted` con styles
inline distinto al `grid grid-cliente` del macro. Swap directo rompe el
visual.

**Opciones**:
1. **Refactor del macro** para aceptar `layout='grid'|'inline'` y emitir
   markup distinto. ~1h.
2. **Refactor de `/reparto`** para adoptar el layout del macro
   (visualmente cambia, suma campos útiles: piso/depto/ref/coords). ~30 min
   + revisar regresión visual.
3. **Solo migrar el JS**: dejar el HTML como está en `/reparto` pero
   borrar las funciones JS duplicadas (`buscarCli`, `pickCli`,
   `abrirEditarCliente`, etc.) y usar `cliente_picker.js`. Requiere hacer
   las funciones del módulo **defensivas** (chequear `if(!el)` antes de
   acceder a DOM que solo existe en `pedido_nuevo`). ~45 min.

**Recomendación**: opción 3 si el objetivo es eliminar duplicación sin
cambiar UX. Opción 2 si querés unificar visual también.

---

## ⏳ Pendiente — Namespacing multi-instancia del `cliente_picker` (2026-06-10)

**Caso de uso**: si en alguna pantalla quisieras 2 buscadores de cliente
(ej. cliente comprador + cliente destinatario en envíos a terceros), el
módulo actual no lo soporta porque usa IDs fijos.

**Plan**:
- `cliente_picker(prefix='pedido')` macro recibe prefix para IDs.
- `ClientePicker(prefix, opts)` factory devuelve instancia con
  `.getValues()`, `.clear()`, etc.
- Document-click listener acepta múltiples prefijos.

**Cuándo**: cuando aparezca el primer caso real de 2 instancias.
No anticipar.

---

### Justificación histórica del `cliente_picker` (mayo 2026)

**Por qué SÍ valía la pena**:
- Reuso real concreto (2-3 lugares ya identificados, más a futuro).
- Mejoras al picker (mostrar último pedido, marcar VIP, etc.) propagan
  a todas las pantallas que lo usen.
- Riesgo bajo: componente acotado, si falla solo rompe donde se usa.
- Replaza el 80% del valor que tendría una "abstracción de Flow" con 20%
  del riesgo (ver descarte abajo).

**Lo que NO vamos a hacer** (descartado 2026-05-28): construir una
abstracción genérica de **Flow/state-machine** que reúna las 6 pantallas
(`/atencion`, `/pedido/nuevo`, `/caja`, `/reparto`, `/reparto/planilla`,
`/envio`) bajo un mismo motor de transiciones. Razones del descarte:
- Las rutas actuales son simples; la abstracción las haría más complejas.
- Regla del 3: hoy hay 1 flujo claro (pedido), no 3 repetidos. El shape
  del Flow se diseña SOLO con info real de producción.
- Flow engines genéricos tienen tasa de fracaso alta — son
  desproporcionados para 1 farmacia con 4-state machine.

**Camino intermedio adoptado** (en su lugar):
- Botones de transición explícitos pantalla-a-pantalla cuando se necesite
  ("→ Crear pedido", "→ Enviar a caja", "→ Marcar para reparto").
- Componentes reusables como `cliente_picker` para puntos de fricción
  concretos.
- Si dentro de 1-2 meses operando aparecen 3 flujos genuinos repetidos,
  recién ahí evaluar un Flow engine.

---

## 🤖 Bot asistente / Atención / Caja — pendientes (2026-06-05)

Hecho: bot Telegram, handoff/panel `/atencion`, ficha cliente, alta lead, UI
Helpdesk aislada, buscador de productos, card de pedido, presencia de agentes,
ciudades, **caja + cajero + formas de pago**, re-enganche con Sí/No, derivar por
texto libre. Lo que falta:

- [ ] **Visibilidad total en el panel** (EN CURSO): ver TODAS las conversaciones,
  incluidas las que maneja **solo el bot** (hoy el panel solo muestra cola/humano)
  + poder **intervenir** una charla del bot aunque el cliente no lo haya pedido.
- [ ] **Resumen del día**: embudo (consultas → pedido → caja → cobro, $ total) +
  resumen con IA de los chats. (Viene después de caja.)
- [ ] **⚠️ Valores de PROD en el `.env` de la LAN antes de salir en serio**: hoy en
  local están en testing → `ATENCION_AUTO_BOT_MINUTOS=30` (prod ~180) y
  `ATENCION_REENGANCHE_MINUTOS=1` (prod ~5). El default del código/compose ya es
  prod (180/5); el override de pruebas vive solo en el `.env` local.
- [ ] **Ficha real de Badia**: `bot/info.py` tiene DATOS DE PRUEBA → cargar
  horarios, servicios, obras sociales, formas de pago, delivery reales.
- [ ] **Regenerar token de Telegram** (quedó expuesto en el chat de desarrollo).
- [ ] **WhatsApp (Fase 2)**: número dedicado + verificación Meta + adaptador Cloud
  API + webhook expuesto (túnel Cloudflare/Tailscale o relay). Ver
  `docs/asistente_whatsapp.md` §10-13. El cerebro NO cambia, solo el adaptador.
- [ ] **Datos de droga/presentación incompletos**: `productos.monodroga` +
  `producto_atributos` no están cargados para todos → el buscador muestra solo el
  nombre en muchos. Poblar (backfill).
- [ ] **Caja → contabilidad**: enganchar los cobros con `flujo_fondos`/`cuentas`
  (hoy el ticket queda en `tickets_caja`, aislado).
- [ ] **Encargo**: capturar datos del cliente + enganchar con el módulo de pedidos
  (hoy el encargo solo deriva a la bandeja).
- [ ] **Búsqueda por síntoma/droga** (mejorar el matcher; la IA ya ayuda parcial).
- [ ] **Derivar por IA con tool**: darle a la IA una herramienta `derivar_a_humano`
  para los casos que el matcher por keywords no capta.
- [ ] **UI de flujos** (Fase 3): editor del menú del bot sin tocar código.
- [ ] **Migración**: cuando funcione, bajar Trii y subir esto al número principal.

---

## ⏳ Pendiente — Seguimiento del pedido para el cliente ("posición X de N en reparto") (2026-06-07)

**Idea**: que el cliente que hizo el pedido pueda ver en qué estado está su reparto
(en ruta, posición X de N, entregado) sin tener usuario en el sistema.

**Lo que ya existe** (no hay que inventar el dato):
- `PedidoReparto` (database.py:574) tiene `estado` (pendiente → en_ruta → entregado
  → anulado), `orden_en_ruta` (posición en la ruta, la calcula `reparto_optimizar`
  por vecino más cercano), `cliente_observer_id`, `ruta_id`, `cadete_id`, `canal`.
- "Posición X de N" = rank de `orden_en_ruta` del pedido entre las paradas todavía
  `pendiente`/`en_ruta` de su ruta. Calculable hoy.

**Lo que falta**:
1. **Acceso sin login** — hoy todo `/reparto/*` está cerrado a admin/dev/farmacia
   (`_ROLES_OK` en routes/reparto.py:17). Dos caminos:
   - **Link con token (recomendado)**: agregar columna `token` (uuid corto) a
     `PedidoReparto` + ruta pública `GET /seguimiento/<token>` SIN `@login_required`
     que devuelva estado + posición. Se manda por WhatsApp/Telegram al confirmar el
     pedido. Funciona venga el pedido del canal que venga.
   - **Por el bot**: el cliente pregunta "¿dónde está mi pedido?" y el bot responde.
     ⚠ Solo sirve si la conversación está linkeada a `cliente_observer_id`. OJO: el
     autovínculo por teléfono (bot/store.py:25-26) **solo aplica a WhatsApp** (ahí
     `canal_user_id` ES el teléfono). En **Telegram** (bot actual) `canal_user_id` NO
     es el teléfono → el link no es automático, hay que resolverlo aparte.
2. **BLOCKER real — que el estado avance en vivo**: hoy el estado lo cambia el operador
   a mano desde el panel (`reparto_estado`). El cadete en la calle no marca nada. Si
   nadie va marcando `entregado` parada por parada, "posición 3 de 7" queda congelada
   y miente. **Esto depende directo de la task pendiente "vista del cadete"** (el cadete
   marca entregado/no vino desde el celu): esa vista es la fuente que hace el seguimiento
   real-time. Sin ella, el seguimiento es estático.
3. **Puente bot→reparto**: hoy los `PedidoReparto` se crean solo manualmente por el
   operador; el bot no inserta en `PedidoReparto` (el campo `canal` lo soporta pero
   nadie lo usa). Si se quiere que el pedido hecho por el bot se auto-siga, falta ese
   puente también.

**Esfuerzo / orden**:
- Versión mínima (link público + estado + posición X de N): ~medio día. Útil, pero la
  posición solo se mueve si el operador marca entregas.
- Versión que vale la pena: **hacerla DESPUÉS de "vista del cadete"**, porque ahí el
  estado avanza solo desde la calle y la posición es genuina.

**Recomendación**: armar encima de "vista del cadete", no antes. Relacionado:
memoria `reparto-planilla-real` y `bot-asistente-estado`.

---

## ✅ HECHO — Adoptar Alembic para migraciones (2026-05-28 → completado 2026-06-02)

> **HECHO** (PRs #145-147): baseline `alembic/versions/ae43763059ec_baseline_schema.py` con las 93 tablas (review 93/93 ✅ en `docs/alembic_baseline_review.md`), `init_db` adoptó Alembic vía `_alembic_sync()` (bootstrap stamp/upgrade), `stamp head` aplicado en Local + Render, drift reconciliado en ambas. Pendiente gradual (no urgente): migrar los `_pg_add_columns` inline a revisiones dedicadas y borrar el zombie handler cuando haya confianza.

**Contexto / por qué**: el approach actual de migraciones (`Base.metadata.create_all` + `_pg_add_columns` con `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` inline) es la causa raíz del wipe del 2026-05-28: una columna NOT NULL agregada al `configuracion` perdió su DEFAULT en algún punto, el INSERT inicial empezó a fallar, el "zombie pg_type handler" lo intentaba arreglar con `DROP TABLE ... CASCADE` y arrastró todos los masters. Mitigado en PR #131 con guard + `RESTRICT`, pero la fragilidad de fondo queda.

**Limitaciones del approach actual**:
- No hay versionado / historial de migraciones — todo es un pile idempotente.
- No hay rollback (`alembic downgrade -1` no existe).
- No hay detección de drift entre instancias (Render perdió DEFAULTs de `transfer_excedente_meses/necesita_meses` sin avisarnos).
- El zombie handler existe **solo** por la fragilidad de mezclar `create_all` con inline DDL. Con Alembic no se necesita.
- Auto-generación de migraciones desde modelos: no.

**Plan de adopción** (1 sesión enfocada 4-6 h + gradual):
1. `alembic init` + config apuntando a `DATABASE_URL`. (~30 min)
2. **Baseline migration** que represente el schema actual completo (`alembic revision --autogenerate` desde una DB sana + revisión manual línea por línea). (~2-3 h, lo más delicado)
3. **`alembic stamp head`** en cada instancia (local Badia, Render, Pieri si aplica). Marca cada DB como "ya estás al día". (~30 min)
4. Cambiar `init_db` para que corra `alembic upgrade head` en vez de `create_all + _pg_add_columns`. (~1 h)
5. **Gradual**: migrar `_pg_add_columns` a revisiones Alembic dedicadas (las inline ALTERs son idempotentes, no estorban si conviven con Alembic durante la transición). (~1 sesión por mes)
6. **Borrar el zombie handler** después de tener confianza. (~5 min)

**Riesgos a manejar**:
- Drift entre instancias (Render vs local). Antes del paso 2, hacer un diff de schemas para reconciliar y arrancar Alembic con baseline limpio en todos.
- Si el baseline está mal, las migraciones siguientes lo arrastran. La revisión manual del paso 2 es crítica.
- Adoptar en medio de un sprint de features puede chocar con PRs que agregan columnas. Mejor en una semana tranquila o coordinando.

**No urgente**: el PR #131 ya cortó la sangría (guard + RESTRICT impiden el wipe). Se puede deployar y operar tranquilo con el approach actual. Pero conviene agendar la adopción para no perder este punto.

---

## 🟢 AppNúcleo — dashboard de grupo (read-only, app separada) (2026-05-27)

**Qué es**: UX separada que consolida las farmacias del grupo (Badia, Pieri; vienen **Grassi** y **Cappone**) **sin** multi-tenant in-DB. Cada farmacia sigue siendo su instancia; el Núcleo solo **lee y agrega**. Vive en `appnucleo/` (app Flask standalone). Detalle en `appnucleo/README.md`.

**Decisión de arquitectura**: "datos ya masticados" en el edge. El Núcleo lee la tabla `product_analytics` de cada farmacia (snapshot chico ya pre-agregado por su propio sync: stock+precio+ventas12m+lab+rubro) por **fan-out read-only**. Nunca toca `obs_ventas_detalle` crudo (OOM). Clave cross-instancia = `codigo_alfabeta` (producto) y `matrícula` (médico); `observer_id` difiere entre ObServers.

### ✅ Fase 0 — HECHO (PR #122, 2026-05-27)
- Landing: KPIs (ventas/unidades/stock valorizado/sin movimiento), tendencia 12m apilada por farmacia, participación, top labs, rotación, tabla por farmacia con salud del feed (🟢/🔴).
- Ventas-multi: pivot por lab/producto/rubro con columna por farmacia + consolidado (responsive PC+mobile).
- `data.py` con fan-out + caché TTL 5min + degradación por instancia + modo DEMO sintético. Logo + splash. 5 smoke tests. Validado contra Badia+Pieri reales (28k SKUs).
- Config por env `NUCLEO_FARMACIAS` (JSON, **rol read-only por farmacia**, URLs no commiteadas) / carga `appnucleo/.env` si hay python-dotenv.

### ✅ Hecho (2026-06-02/03)
1. **Deploy**: ✅ servicio Render **manual** (fuera del Blueprint — ver `docs/lecciones_deploy_render.md` #9). Registro desde tabla `sucursales` (no env). En prod con Badia+Pieri reales.
4. **Fase 3 — scoping por dueño**: ✅ login con usuarios (`NUCLEO_USERS`) + scoping por farmacia (PR #143).
5. **Visual ("caer los calzones")**: ✅ count-up, mapa de calor, drill-down, comparativa A vs B, animaciones de entrada (PR #142).

### ⏳ Pendiente — cómo sigue
2. **Fase 1 — dims médico (matrícula) + obra social**: no salen de `product_analytics` (necesitan `obs_ventas_detalle`). Resolverlas con **edge-ETL**: cada localhost prepara un feed normalizado (claves naturales) y lo pushea a un **warehouse propio del Núcleo** → el Núcleo consulta local, sin fan-out en vivo. (OS/droga necesitan tabla de normalización de nombres.)
3. **Fase 2 — pedidos grupales**: se crean en las apps locales (tabla `pedido_grupal` taggeable) y el Núcleo los **consolida** (mantiene al Núcleo read-only).
- **Endurecer**: rol Postgres read-only por farmacia (hoy las url_externa usan owner). Sumar Grassi + Cappone al registro `sucursales` cuando estén.

**Audiencia**: Diego + dueños (por ahora pocos). Ver memoria `project_appnucleo`.

---

## ⏳ Pendiente — Deduplicar modal de gráfico en order_detail.html (2026-06-03)

**Detectado** en auditoría de calidad. `templates/order_detail.html` (líneas ~2630-2797) tiene su **propia copia inline** del modal de gráfico histórico (`openChart` + `renderHistChart` + `closeHistChart` + HTML del modal), ~90 líneas que duplican el partial `templates/_grafico_historico.html` (que ya usan 10+ pantallas vía `{% include %}`).

**Por qué NO se hizo en el quality pass**: la copia de order_detail NO es idéntica, tiene 2 variantes a preservar:
1. `CHART_BTN` con **resolución de pack EAN** (`MODULO_PACKS[ean] → ean_unidad`): grafica el EAN unidad, no el pack.
2. **Botón extra de historial de precios** (`/precios/${ean}`).
Además la versión inline de `renderHistChart` es light-hardcoded (el partial es theme-aware, funcionaría igual en light). El modal usa `z-50` vs `z-[100]` del partial.

**Cómo hacerlo seguro**: extender el partial para parametrizar CHART_BTN (ej. `window.CHART_EAN_RESOLVER` para el pack + flag de botón de precios) y reemplazar el inline por el include. Como el partial lo usan 10+ pantallas y NO hay tests de frontend, requiere **sesión dedicada con la app corriendo** y verificación manual del gráfico en order_detail + las otras pantallas. order_detail es del módulo OS (premium) → cuidado.

---

## ⏳ Pendiente — Seguimiento de pedidos: etapa "Factura" sin implementar (2026-06-03)

**Detectado** en auditoría de calidad. En `routes/compras_dia.py` (líneas ~2279, 2348, 2358) el pipeline de seguimiento de pedidos tiene la etapa **Factura hardcodeada a `False`** con 3 `TODO`:
- `etapa_factura` siempre `False` (nunca se marca como facturado).
- Falta **vincular el pedido con `Invoice`** y cruzar con `InvoiceItem` por droguería + fecha.

**Qué falta**: implementar el cruce pedido↔factura (por droguería + fecha + ítems) para que la etapa Factura refleje la realidad. Hoy la UI muestra esa etapa siempre incompleta sin indicador de que es una feature a medio hacer.

---

## ⏳ Pendiente — Gap de marcas: streaming real del web search (2026-06-03)

El informe `/informes/lab-gap-marcas` (web search de Claude) tiene hoy un **teatro animado** (pasos simulados + fuentes reales en stagger) mientras procesa. Pendiente: **streaming real** (SSE) que muestre EN VIVO lo que Claude hace — cada `server_tool_use` (query que busca) y `web_search_tool_result` (página que lee) a medida que ocurren, fuente por fuente. Es el efecto "esto es una locura" porque es genuino.

Implementación: convertir `POST /informes/lab-gap-marcas/recopilar` a `Response(generator, mimetype='text/event-stream')` usando `client.messages.stream()` con la web_search tool; el generator emite eventos (`🔍 buscó: <query>`, `📄 leyó: <url>`) que el front consume con fetch+ReadableStream y pinta en el modal. Caveats: SSE ocupa un worker gunicorn durante el stream (OK para uso interno); solo aplica en la 1ª búsqueda de cada lab (las siguientes vienen del caché, instantáneas).

---

## ⏳ Pendiente — Transferencias: calcular según presentación (unidades vs cajas) (2026-05-23)

**Problema**: en `/transferencias` las cantidades (stock, venta, sugerido) están en
**unidades de venta**, no en envases. Ej. GENIOL PLUS mostró Pieri stock = **1216**,
que son **tabletas sueltas, no cajas**. Una transferencia entre sucursales normalmente
se mueve por **caja/envase**. Hoy el sugerido "← Badia 179" son 179 unidades sueltas;
habría que expresarlo/redondearlo en **cajas** (ej. si la caja trae 12 → ~15 cajas).

**Qué falta**:
- Traer `cantidad_envase` del producto (ya existe en `ProductoAtributo.cantidad_envase`
  / `ObsProducto.cantidad_envase`) y, cuando esté cargado, mostrar el sugerido también
  en **cajas** (unidades ÷ cantidad_envase, redondeo a múltiplos del envase).
- El cruce es cross-DB por alfabeta; el `cantidad_envase` puede salir de cualquiera de
  las dos farmacias (mismo producto → mismo envase).
- **Revisar caso por caso**: confirmar que stock/venta de ObServer vienen en unidades
  de venta y no ya en cajas (varía por producto / presentación).

**Relacionado**: item "Unidad de venta vs unidad de pedido (fraccionados)" más abajo —
misma necesidad de `cantidad_envase`. Si se resuelve la conversión ahí, reusarla acá.

**Trigger**: cuando se quiera que las transferencias se expresen/redondeen en cajas.

---

## ⏳ Pendiente — Factor de cálculo por horas hasta el próximo pedido (cadencia de reparto) (2026-05-23)

**Idea**: cuando la droguería tiene cargada su **lista de horarios de reparto**
(matriz semanal en `/compras/dia` — modelo de horarios + countdown a la próxima
ventana, ya existente), usar las **horas/días hasta el próximo pedido/reparto**
como **factor de cálculo adicional** del sugerido.

**Lógica**: hoy el "a pedir" cubre un horizonte fijo de N días. Si el próximo
pedido a esa droguería cae dentro de muchas horas (ventana larga sin reposición),
hay que pedir **más** para cubrir el gap; si el próximo reparto es pronto, **menos**.
O sea: escalar el sugerido por la **cobertura real hasta la próxima ventana de
reparto** en vez de un horizonte fijo.

**Qué ya existe**:
- Matriz de horarios de reparto por droguería + countdown a la próxima ventana
  (`routes/compras_dia.py` / `/compras/dia`).
- Cálculo del sugerido en `services/calculo_pedido.py` y `compras_dia_armar`.

**Qué falta**: derivar "horas/días hasta el próximo reparto" del horario de la
droguería y meterlo como horizonte dinámico / multiplicador en el `a_pedir`
(solo cuando el horario está cargado; si no, fallback al horizonte fijo actual).

**Trigger**: cuando los horarios de droguería estén cargados y se quiera afinar
el sugerido por la cadencia real de reparto.

---

## ⏳ Pendiente — Precio de última compra (para valorizar) (2026-05-22)

**Contexto**: en el informe de cadencias, el "Catálogo dormido" valoriza el stock
parado. Se pidió valorizar **a precio actual si tiene, sino al precio de última
compra**.

**Problema de datos**: ambos campos del master están **vacíos**:
- `Producto.precio_pvp`: 0 de 60.211 poblados.
- `Producto.ultima_compra`: 0 poblados.
- Facturas cargadas: solo 3 (23 productos) → no sirve como fuente general.

**Workaround actual (a26ffd0)**: se valoriza al **precio actual =
`ProductAnalytics.precio_pvp`** (snapshot, el que usa el dashboard; cubre ~70%);
fallback al **último precio de venta** histórico (monto/unidades de ObsVentaMensual),
marcado con `*`. NO se usa última compra porque no existe.

**Qué falta para "precio de última compra" de verdad**:
- Poblar `Producto.ultima_compra` (+ precio) desde una fuente real. Opciones:
  (a) cargar las compras como facturas (hoy casi no se hace), o
  (b) ver si ObServer expone una vista de compras (`DW.Compras` / similar) para
  sincronizar y derivar última compra + precio por producto.
- Una vez que exista, sumar como fallback intermedio: actual → última compra → venta.

---

## ⏳ Pendiente — Unidad de venta vs unidad de pedido (fraccionados) (2026-05-21)

**Problema**: hay productos que se **venden de a 1 unidad** (ej. ALIKAL sobre suelto)
pero se **piden por envase** (caja de 30 sobres). Hoy el "a pedir" cuenta unidades
vendidas (sobres) y **no** las convierte a envases → si vendiste 45 sobres y la caja
trae 30, el sistema sugiere 45 en vez de 2 cajas.

**Qué ya existe**:
- Dato `cantidad_envase` (= unidades de venta por envase). Viene de
  `DW.Productos.CantidadDelEnvase` → `ObsProducto.cantidad_envase`, también en
  `ProductoAtributo.cantidad_envase`.
- Se ve y se edita en la **ficha de producto** (`producto_detalle.html`: bloque
  "Datos de ObServer" + atributo editable).
- Puente venta↔pack solo vía `ModuloPack` (ean_pack ↔ ean_unidad × cantidad),
  pero solo aplica a labs `usa_packs` y al flujo de módulos/ofertas, no al
  "a pedir" general.

**Qué falta**:
- El cálculo de "a pedir" (`compras_dia.py`, `purchase.py`) **no usa**
  `cantidad_envase` ni distingue fraccionados.
- No sincronizamos el flag "Es Fraccionado" de `DW.Productos` (el SELECT de
  `sync_productos` no lo trae).

**Decisión (2026-05-21)**: NO auto-sincronizar el flag. Se configura **producto
por producto, a mano**, desde `/productos/flags` (tarjeta Presentación).

**✅ Fase 1 (2026-05-21)**: hecho.
- Columna `Producto.fraccionado` (bool) + migración PG inline.
- En `/productos/flags`, tarjeta "📦 Presentación": buscás producto → toggle
  fraccionado + editar cantidad de envase (guarda en `ProductoAtributo`,
  fuente=manual). Endpoints `GET/POST /api/producto/presentacion`. No se tocó la
  tarjeta "Asignar flag" (comportamiento sigue igual).

**⏳ Fase 2 (pendiente)**: que el "a pedir" use `fraccionado` + `cantidad_envase`
para convertir unidades vendidas → envases (redondeo a múltiplos del envase).
Afecta `compras_dia.py` / `purchase.py` / `services/pedido_estacional.py`.

---

## ⏳ Pendiente — Progreso en vivo del sync ObServer (2026-05-21)

En `/admin/observer-sync`, al correr "Sync todo" mostrar **dinámicamente qué
tabla se está procesando** (y filas), actualizándose, para ver si avanza. Hoy
el botón es síncrono y bloquea sin feedback hasta terminar (el sync completo
tarda minutos, sobre todo `ventas_detalle` con millones de filas).

**Infra que ya existe**:
- `sync_lock.paso_actual` (se setea con `_sync_lock_set_paso` en cada entidad,
  pero solo en el flujo `/api/auto-sync` del DockerPanel, no en el botón web).
- `GET /api/auto-sync/status` devuelve `{en_curso, paso_actual, ultimo_resultado}`.

**Falta**:
1. Que el "Sync todo" web (`observer_sync_run` con entidad='todo') corra async
   o vaya actualizando `paso_actual` por entidad (igual que el flujo DockerPanel).
2. Frontend: al disparar el sync, hacer polling a `/api/auto-sync/status` cada
   ~2s y mostrar "Sincronizando: <tabla> (<n> de <total>)" con barra de progreso,
   en vez del submit bloqueante actual.

Esfuerzo: 2-3h (refactor del botón a async + polling JS + barra).

---

## 🎯 Objetivo (no urgente) — Motor de pantallas de pedido dirigido por config (fábrica) (2026-05-20)

NO es una pantalla única gigante: es un **motor que genera pantallas** desde
config (`TipoPedidoConfig`). Una fila de config = una pantalla de pedido con su
comportamiento (columnas, base de demanda, modificadores de cálculo). Hoy hay 4
pantallas con lógica duplicada (`/compras/dia/armar`, `/informes/pedido-auto`,
`/pedido/prueba`, `/compras/laboratorio`) — son el ground truth del que se extrae
el motor. Plan completo: `docs/plan_motor_pantallas_pedido.md`.

Camino corto accionable ya: **extraer componentes compartidos** (builder de filas,
chip de flag, filtros) — mata el 80% de la duplicación sin construir el motor.
Prerequisitos del motor: source-of-truth de métricas (HECHO) + cerrar gap
oferta-min/estacionalidad en el motor de cálculo (pendiente, ver entrada más abajo).

---

## ⏳ Pendiente — Programación automática de compras + integración con flujo de fondos (2026-05-18)

Diego ya tiene `/flujo_fondos` funcional pero "sin inteligencia" — el operador marca
manualmente las semanas activas por proveedor (botones 1-8) para distribuir el peso
de compra. Idea: analizar ventas históricas y proponer un programa de compras
automático por lab, distribuido en el calendario.

**Inputs disponibles**:
- `ObsVentaMensual` (ventas por producto/lab por mes, 12m)
- Compras por proveedor (ya en `flujo_fondos`)
- `OfertaMinimo` (vigencias y mínimos)
- `DescuentoBase` (lab × drog)

**MVP propuesto** (1-2 días):

1. **Modelo nuevo `ProgramaCompraLab`**:
   - `lab_id` (FK)
   - `cadencia_dias` (15 / 30 / 45)
   - `monto_mensual_target` ($)
   - `proxima_fecha_sugerida`
   - `dia_preferido_del_mes` (opcional, ej. "primer lunes")
   - `notas`

2. **Algoritmo**:
   - Para cada lab activo (`u12m > umbral`): `monto_mensual_target = sum(m12m)/12 * margen_meta`
   - Distribuir en calendario de 8 semanas según cadencia
   - Asignar semanas evitando colisiones (no juntar todos los labs grandes la
     misma semana → spike de caja)
   - Considerar `OfertaMinimo.vigencia_hasta` para forzar compra en última
     semana antes de vencimiento

3. **Integración con `/flujo_fondos`**:
   - Botón "🤖 Sugerir distribución" → pre-tilda semanas activas según el plan
   - El operador puede des-tildar/ajustar (no es atómico)
   - Diff visual: "lab X: estabas en sem 3+7, te sugiero 2+5+8"

**Ganancias esperadas**:
- Suaviza cashflow semanal (evita semanas con $5M y otras con $500k)
- Captura ofertas que vencen al fin de mes
- Detecta labs sub-comprados (compras < 80% del target → alerta)

**Tradeoffs**:
- Requiere config inicial por lab (cadencia preferida, día preferido)
- Feedback loop: si el operador siempre des-tilda lab Y, hay que aprender
  ese patrón

**Esfuerzo**:
- MVP simple (cadencia fija + monto basado en u12m/26): 1-2 días
- Con estacionalidad + optimización de cashflow: 1 semana
- Con backtesting y ajuste continuo: más

---

## ⏳ Pendiente — Implementar sistema de Grupos para usuarios (2026-05-18)

Diego mostró el panel de Grupos de ObServer (CAJERO, Facturacion, Farmaceutico,
PERMISO TOTAL, VENTAS, "Todos los usuarios" como root). Quiere replicar el
concepto en AppFarmWeb.

**Decisiones pendientes** (a discutir cuando se retome):

1. **Alcance**:
   - A) Espejar grupos ObServer read-only (sync desde DW.Grupos).
   - B) Sistema propio, solo etiquetas (sin permisos).
   - C) Sistema propio + permisos por grupo (reemplaza/complementa rol).
   - D) `rol` actual + `grupo` como segundo eje sin lógica de permisos.

2. **Cardinalidad**: ¿un user en N grupos o solo en 1?

3. **Caso de uso real** que motiva esto.

**Esfuerzos estimados**: A=3h, B=3h, C=1-2 días, D=2h.

**Estado actual del sistema de permisos** (para referencia cuando se retome):
- `Usuario.rol` (String, default 'remoto') — soporta `farmacia | dev | remoto | admin | pedidos | rendicion`.
- `rendicion` ya tiene gating funcional en `routes/auth_routes.py:67-78` (solo
  accede a `/devoluciones/*` y `/rend`). Los 27 usuarios seed están ahí.
- `pedidos` también tiene gating similar (solo `/pedidos/*`).
- Resto de los roles no tiene gating fuerte — checks ad-hoc en algunos endpoints
  (`routes/observer.py:742` requiere `admin/dev`, etc.).

**Si arrancamos por D (más rápido)**: agregar `Usuario.grupos_json` Text (CSV
de nombres), UI en `/admin/usuarios` para tagger, filtro en listados. Sin
lógica de permisos.

**Si arrancamos por C (más completo)**: modelo nuevo `Grupo` + tabla N-N
`usuario_grupos` + `Grupo.permisos_json` + middleware que combine permisos
de todos los grupos del user. Reemplaza `rol` con grupo "PERMISO TOTAL"
equivalente a admin, "VENTAS" equivalente a rendicion, etc.

---

## ✅ HECHO 2026-05-19 — Alerta para productos con `cantidad_reposicion_fija` seteada

Implementado en `routes/productos.py:14` + `templates/index.html:202-243` (card home "📦 Repo fija" con desglose rojo/amarillo/verde) + `templates/productos_repo_alertas.html` (pantalla detalle con todos los productos incluso sin alerta activa, filtro por lab).

---

## ⏳ Pendiente original — Alerta para productos con `cantidad_reposicion_fija` seteada (2026-05-18)

Cuando un producto tiene `Producto.cantidad_reposicion_fija` cargado, debería
disparar una alerta en algún panel (alarmas / dashboard / lugar a definir) que
liste todos los productos con override activo. Razón: el override silencia el
cálculo dinámico; si quedó cargado por error u obsoleto, ningún workflow lo
muestra hasta que cae al mínimo y aparece el chip "Repo fija" en el armado.

**Idea**: agregar a `routes/alarmas.py` (o equivalente) un check "productos con
repo fija" que liste los registros con `cantidad_reposicion_fija IS NOT NULL`,
junto con su última venta y stock actual, para que el operador pueda revisar
periódicamente si todavía aplica.

Esfuerzo: 1-2 horas (query + tarjeta de alarma + link a `/productos?filtro=repo_fija`).
Prioridad: baja — anotado para revisar más adelante.

---

## ✅ HECHO 2026-05-19 — Planificadores respetan `unidades_minima` y `cantidad_reposicion_fija`

Implementado en commit `39975e2` ("feat(planificadores): borrar /informes/pedido-auto + migrar a /pedido/prueba + chips override + card comportamientos").

- `helpers.aplicar_overrides_planificador()` aplica precedencia cant_fija > oferta_min
- `routes/pedido_prueba.py` hace bulk-load de cant_fija_por_obs / oferta_min_por_obs y aplica overrides a ambos sugeridos (estacional + día actual)
- UI muestra chips 📦 Repo y 🎁 Mín oferta con tooltip explicando cuándo se activa
- `/informes/pedido-auto` borrado, migrado a `/pedido/prueba`

---

## ⏳ Pendiente original — Planificadores deben respetar `unidades_minima` y `cantidad_reposicion_fija` (2026-05-17)

Hoy ambos conceptos están desacoplados entre el armado táctico y los
planificadores. Resultado: el operador ve una sugerencia en `/pedido/prueba`
o `/informes/pedido-auto` que después no coincide con lo que produce
`/compras/dia/armar`.

**Estado actual:**

| Concepto | Modelo | Pantalla armado | `/pedido/prueba` | `/informes/pedido-auto` |
|---|---|---|---|---|
| Mínimo de oferta (TRF) | `OfertaMinimo.unidades_minima` | ✅ Considerado (filtro en `services/descuentos.py:106` + botón UI manual) | ❌ Ignorado | ❌ Ignorado |
| Cantidad fija de reposición | `Producto.cantidad_reposicion_fija` | ✅ Override real en `services/calculo_pedido.py:114-117` | ❌ Pasa `None` ([services/pedido_estacional.py:498](services/pedido_estacional.py#L498) tiene comentario explícito) | ❌ No leído |

**Trabajo a hacer:**

1. `/informes/pedido-auto` ([routes/informes.py:1659](routes/informes.py#L1659)):
   - Antes de calcular sugerido, bulk-load `Producto.cantidad_reposicion_fija`
     y `OfertaMinimo.unidades_minima` por EAN.
   - Si hay `cant_fija` y stock ≤ min → `sugerido = cant_fija`.
   - Mostrar chip "📦 Repo fija: N" o "🎁 Mín oferta: N" en la fila para
     que el operador entienda por qué la sugerencia es esa.

2. `/pedido/prueba` ([services/pedido_estacional.py](services/pedido_estacional.py)):
   - Quitar el `None` hardcodeado, leer `cantidad_reposicion_fija` real.
   - Decidir política: ¿el override gana sobre el cálculo estacional (igual
     que en `/compras/dia/armar`) o solo se usa como piso?

3. (Opcional) Indicador visual en `/productos`: badge "Repo: Nu" al lado del
   precio cuando el producto tiene `cantidad_reposicion_fija` seteado.

**Esfuerzo:** 2-3 horas backend + 1 hora UI/chips.

**Prioridad:** Media. Hoy el gap se nota cuando alguien usa el planificador
para anticipar un pedido grande y después al armarlo le sale distinto.

---

## ⏳ Pendiente — Catálogo de configuraciones de pedido (2026-05-17)

Pantalla nueva (futura, no urgente) que liste TODAS las configuraciones
cargadas a través de `/pedido/prueba` y `/informes/estacionalidad-drogas`,
para auditar/limpiar sin tener que recorrer lab por lab.

Ruta sugerida: `/config/comportamiento-catalogo` con 4 secciones:
- Escenarios producto (todos los `EstacionalidadEscenario` con
  `producto_id IS NOT NULL`).
- Escenarios droga (con `producto_id IS NULL`).
- Flags por producto (`ProductoFlag` con EAN seteado).
- Flags por laboratorio (`ProductoFlag` con `laboratorio_id`).

Cada sección con tabla buscable + filtros + acción "Eliminar"
(con confirmación). Útil para:
- Auditar todas las configuraciones de una.
- Limpiar configuraciones de productos discontinuados que ya no aplican.
- Detectar inconsistencias (ej. un escenario producto que duplica el
  de la droga sin cambios = redundante).

Esfuerzo estimado: 3-4 horas.
Trigger: cuando haya 50+ configuraciones cargadas y empiece a costar
revisarlas una a una desde /pedido/prueba.

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

### ~~Migrar a Alembic~~ ✅ HECHO 2026-06-02 (PRs #145-147)
- Baseline en `alembic/versions/ae43763059ec_baseline_schema.py`. Ver
  entrada "HECHO — Adoptar Alembic" arriba en este mismo doc para detalle.

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

### Estacionalidad: consumir escenario default en cálculo de pedidos (2026-05-17)
- **Trigger**: el panel de ajuste ya guarda escenarios pero todavía no los
  consume al sugerir cantidades. Hoy `calcular_metricas_pedido_auto` y la
  lógica de `compras_dia_armar` calculan sugerido sin estacionalidad.
- **Esfuerzo**: 1 día.
- **Cómo**:
  1. Helper `obtener_factor_estacional(droga_id, mes_objetivo) -> float`:
     consulta el escenario default de la droga y devuelve
     `indices[(mes_objetivo + lead_time) % 12] * cobertura_meses`.
     Default 1.0 si no hay escenario.
  2. En `calcular_metricas_pedido_auto` (routes/informes.py): multiplicar
     `sugerido` por `obtener_factor_estacional(droga, mes_pedido)`.
     Bridgear producto → droga vía obs_productos.
  3. Igual en `routes/compras_dia.py` / `purchase_helpers.calcular_min_sugerido`.
  4. Mostrar en UI: badge "ajustado por estacionalidad ×1.5" cuando el
     factor != 1.0, link al escenario que lo gobierna.
- **Riesgo**: toca código crítico que ya está en producción. Validar
  primero con 5-10 drogas marcadas y comparar contra cálculo viejo.
- **Beneficio**: el ajuste manual del usuario empieza a tener efecto real
  en las cantidades que se proponen. Cierra el loop de la feature.

### ~~Estacionalidad: refinar pooling con accion_terapeutica~~ ⚠ DESCARTADO 2026-05-17
- Intentamos pivotar el pooling a `productos.accion_terapeutica`, pero la
  tabla `productos` está **vacía en producción** (y casi vacía en local: 115
  registros, todos sin AT). `producto_atributos` también está vacía. La
  columna AT existe pero ningún flujo actual la setea — solo se llenaría
  con la "Fase 3 — Backfill producto_atributos" del backlog.
- **Pivot aplicado (commit `47ff310`):** pooling adaptativo. Subrubros
  con >30 drogas distintas se consideran heterogéneos y NO se usan como
  pool. La droga queda con patrón crudo. Badge "crudo" en UI cuando
  aplica. Validado: 1464 drogas pasaron a crudo (incluido Paracetamol,
  Ibuprofeno y casi todo el top vendidas — antes salían pooled con
  "Medicamentos"); 2 drogas mantienen pooling en subrubros cohesivos.
- **Cuando se haga Fase 3 (backfill producto_atributos),** retomar este
  item para usar `monodroga_norm` o agrupar por familia química como
  pool de mayor granularidad.

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

- 2026-05-17: **Informe de estacionalidad por droga** — `/informes/estacionalidad-drogas`
  con heatmap E-D (índice = ventas_mes / promedio_anual), pooling bayesiano
  por subrubro (K=12) para drogas con poca historia, CV para ordenar por
  "más estacional", confianza por años de data (1/2/3+). Endpoint API
  `/api/estacionalidad/droga/<id>` para serie por año (chart al expandir).
  11 tests verdes. Pendiente refinar el pooling con `accion_terapeutica`
  del catálogo local (el subrubro "Medicamentos" es demasiado grueso).
- 2026-05-17: **Pooling adaptativo en estacionalidad** — subrubros con
  >30 drogas distintas se consideran heterogéneos y no se usan como pool
  (caso "Medicamentos" 40k productos, "Perfumería" 58k). La droga queda
  con patrón crudo + badge "crudo" en UI. Resuelve el problema donde
  Paracetamol salía pooled con "Medicamentos" λ=0.59. Constante
  `HETEROGENEIDAD_MAX_DROGAS = 30` en routes/estacionalidad.py. 4 tests
  nuevos del pooling adaptativo + 24 existentes verdes.
- 2026-05-17: **Escenarios manuales de estacionalidad** — tabla
  `estacionalidad_escenarios` (UNIQUE droga+nombre, es_default exclusivo)
  + 4 endpoints CRUD `/api/estacionalidad/droga/<id>/escenarios[...]`.
  Panel inline en la pantalla de estacionalidad con tabs Histórico/Ajustar:
  12 sliders verticales (índice por mes), sliders lead_time + cobertura,
  chart de 3 series (calculado / ajustado / barras "a comprar" desplazadas
  por lead_time × cobertura). Múltiples escenarios nombrados por droga
  ("base", "agresivo", etc.) con uno marcable como default. Badge
  `★ <nombre>` en la fila cuando hay escenario default. 13 tests
  funcionales de endpoints verdes (CRUD + upsert + exclusividad default
  + clipping + persistencia).
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
