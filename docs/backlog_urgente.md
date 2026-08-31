# Backlog URGENTE — AppFarmWeb

_Creado: 2026-05-21. Lista corta y priorizada de lo que duele pronto. Lo demás
(features, mejoras no urgentes) vive en `docs/mejoras_pendientes.md`._

Orden: P0 = afecta producción AHORA · P1 = riesgo/correctitud · P2 = deuda que acumula.

> 🗺️ **Para ubicarte en el repo**: [`docs/MAPA.generado.md`](MAPA.generado.md) (767 rutas,
> 122 modelos, 21 syncs, con archivo:línea — generado del código, el CI lo verifica).
> **Trampas del dominio** (ObServer, catálogo, precios): [`CLAUDE.md`](../CLAUDE.md).

---

## ✅ Búsqueda de clientes por DNI (Diego 2026-06-22 → 2026-06-23)

Cerrado. El operador puede tipear el DNI (exacto o parcial) en cualquier
buscador de clientes (panel `/atencion`, picker `/pedido/nuevo`, etc.).

**Hecho en 2 pasos:**

1. **Diego 2026-06-22** — `bot/store.buscar_clientes_unificado` incluye
   `Cliente.dni` y `Cliente.telefono` con ilike en el brazo multi-token, y
   `Cliente.dni == q` exacto en el brazo numérico. `Cliente.dni` ya tiene
   `index=True`. Cubrió el cliente_picker (`/api/clientes/buscar`).

2. **2026-06-23** — `bot/store.buscar_clientes` (la firma vieja, usada
   por `/atencion`) delega en `buscar_clientes_unificado`. Los leads
   locales sin observer_id ahora aparecen también en `/atencion`. Como
   los leads no tienen observer_id, se extendió `store.vincular_cliente`
   para aceptar `cliente_id` directo, `/atencion/<conv>/vincular-cliente`
   lo pasa en el body, y `atencion.html` etiqueta esos matches con
   `(lead)` y vincula por cliente_id cuando falta observer_id.

**Gaps aceptados:** DNI parcial-numérico solo matchea partial en tokens
(brazo `q.isdigit()` sigue exacto). El operador tipea el DNI completo,
no es un problema real.

---

## ✅ P0 — Re-sync de ventas (prod mostraba ventas INFLADAS) — resuelto 2026-08-30

**Qué pasaba:** el fix `ee12bc6` (ventas netas) hizo que `sync_ventas_mensuales`
reste devoluciones/notas de crédito, pero el dato viejo en `obs_ventas_mensuales`
seguía inflado hasta correr el sync. Mientras tanto, TODO lo que lee ventas
(gráficos, avg_3m/12m de `producto_metrics`, sugeridos de armado, dashboard)
mostraba números más altos de lo real.

**Resuelto:** Diego corrió el sync varias veces desde la farmacia. Confirmado
vía `/api/auto-sync/status`: corrida completa 2026-08-30 18:00–18:26, paso
`ventas_mensuales` con `ok:true` (80.393 upserts) + `push_render` posterior
(1.752.145 filas a Render). Los números de `producto_metrics` (incluidas las
columnas nuevas de `/control-gondola`) ya reflejan datos correctos.

---

## ✅ P1 — Planificadores ignoraban unidades_minima y cantidad_reposicion_fija — obsoleto, ya resuelto

**Estaba desactualizado.** Este item describía un estado de código que ya no
existe: `/informes/pedido-auto` fue borrado y migrado a `/pedido/prueba`
(commit `39975e2`, 2026-05-19 — ver "✅ HECHO 2026-05-19" en
`mejoras_pendientes.md`). Verificado 2026-08-30 contra `main` actual:
`routes/pedido_prueba.py:308-323` aplica `helpers.aplicar_overrides_planificador()`
(con `cant_fija_por_obs` y `oferta_min_por_obs`, bulk-cargados) a **ambos**
sugeridos (estacional y "Día actual"), justo para que el planificador no
diverja de `/compras/dia/armar`. El `cantidad_reposicion_fija=None` que
todavía aparece en `services/pedido_estacional.py:612` es de
`calcular_sugerido_dia_actual` (calcula el número crudo de comparación); el
override real se aplica después, en `pedido_prueba.py`, a los dos por igual.

---

## 🟠 P1 — Verificar que el preDeploy siga corriendo

**Estado:** ya configurado (Pre-Deploy Command en dashboard = `python scripts/migrate.py`).
**Riesgo:** vive en el dashboard, no en el render.yaml (el servicio lo ignora).
Si alguna vez se recrea el servicio o se limpia el dashboard, las migraciones
dejan de correr y vuelve el 500 por columna nueva.

**Acción:** en el PRÓXIMO deploy que toque el schema, confirmar en el log la
línea `[migrate] init_db OK`. Si falta → re-setear el comando en Settings.

---

## 🟡 P2 — Deuda que acumula (no urgente, pero crece)

- **Duplicación.** ✅ Hecho: chip de flag → `services/flags.py`. **Próximo target:**
  el builder de filas / contexto de armado (`compras_dia_armar` arma `items[]`
  con lógica que se podría compartir con pedido_auto / pedido_prueba). Hacerlo
  DESPUÉS de cerrar el P1 de planificadores.
- **Gating local/render a nivel rutas.** Hoy es solo UI (se ocultan botones).
  Las rutas de sync siguen registradas en Render → superficie de ataque. Mover
  a registro condicional de blueprints (`detectar_entorno()` + `_modules_local_only`).
- **ProductAnalytics stale.** `/dashboard` y `/purchase/suggest` leen un snapshot
  de ~1 mes. Decidir: migrarlos a cálculo en vivo (y borrar PA) o deprecarlos.
- **Kellerhoff como posible isla nueva.** Vigilar que el "fraccionado" se
  generalice y no quede una rama paralela Kellerhoff-only.

---

## 🟡 P2 — Flujo nuevo /pedido/nuevo + WhatsApp grupo (2026-06-08)

Pantalla limpia para tomar pedidos (alternativa a `/reparto`) + integración con
WAHA (whatsapp-web.js) para publicar pedidos a un grupo de WhatsApp y aceptar
"tomas" por reply.

**Listo:**
- `/pedido/nuevo` con autocomplete de cliente (live, multi-token) + autocomplete
  de producto contra `obs_productos` (híbrido: link a obs_id si elige sugerencia,
  texto libre si no) + buscador de dirección 📍 con sugerencias georef-ar.
- Cotizador de envío integrado (cuadras manual override, badge zona/tramo,
  semáforo de antigüedad del geocoding).
- Persistencia automática del DomicilioCliente al crear pedido con nueva
  dirección + lat/lng (anti-dup por dir+loc).
- Servicio WAHA en docker-compose (Core, gratis). Sesión `default` paireada.
- Tabla `pedidos_reparto` con columnas nuevas: `envio_costo`,
  `producto_observer_id`, `waha_msg_id`, `publicado_en`, `tomado_por_wsap`,
  `tomado_en`.
- Tabla `domicilios_cliente.geo_actualizado_en` para track del semáforo.
- Botón "📤 Publicar" en `/reparto/planilla` → manda formato pedido al grupo
  configurado en `WAHA_GRUPO_ENVIOS`.
- Webhook `/whatsapp/grupo/webhook` matchea replies con frases de toma
  (`tomo`, `voy`, `lo tomo`, `yo voy`, `voy yo`, `lo agarro`, `oktomo`) →
  asigna `tomado_por_wsap`, intenta match con `cadetes.nombre` para llenar
  `cadete_id`, pasa pedido a `en_ruta`, responde en grupo. Anti-doble-toma
  (segundo cadete que cita el mismo recibe `⚠️ ya lo tomó X`).

**Pendiente (para usar en serio):**
- Sacar `# TEMP TEST` en `routes/reparto.py::reparto_whatsapp_grupo_webhook`
  que acepta mensajes propios (línea con `if msg.get('fromMe')` comentada).
  En prod los cadetes mandan desde sus celulares, no del número vinculado.
- Sacar `print('[WHATSAPP-WEBHOOK]', ...)` debug en el mismo handler.
- Cargar cadetes reales en `/cadetes` con sus nombres (case-insensitive,
  partial match contra pushName de WhatsApp) para que `cadete_id` enganche
  automáticamente al "tomo".
- (Opcional) Mapear `participant @lid` → teléfono → `cadetes.telefono` para
  match robusto (hoy es solo por nombre).
- (Opcional) Reducir info sensible del mensaje público si se usa grupo
  compartido con otras farmacias: mandar solo `Pedido #N · zona · $ envío`
  al grupo y los datos completos al DM del cadete cuando confirma.

---

## ✅ Cerrado recientemente (para no re-discutir)

- Métricas unificadas (`producto_metrics`) — cards == gráficos.
- preDeploy + migraciones automáticas + render.yaml alineado (web starter, db basic_4gb).
- `/pedidos/dia` rediseñado (tabla de cierres, Pedir, ✓/sin pedido).
- Dedup chip de flag (`services/flags.py`).
- Paleta de templates de reparto/caja/panel/envio/cadetes refrescada a tonos
  más claros (zebra striping en planilla). 2026-06-08.

---

## 🟡 P2 — Robot: bajar comprobantes de Kellerhoff → cuenta corriente (Diego 2026-08-18)

Automatizar la descarga diaria de los comprobantes (facturas y notas de crédito)
del portal de Kellerhoff y **cargarlos en la cuenta corriente del proveedor**,
en vez de bajarlos a mano.

**El robot (scraper):**
- 1 vez por día, entra a `https://www.kellerhoff.com.ar/ctacte/ConsultaDeComprobantes`
  con las credenciales de cliente (`badiar` / clave — en `.env`, NO hardcodear).
- Lista los comprobantes, baja los **PDF nuevos** (dedup por número de
  comprobante para no repetir).
- Portal ASP.NET (dev: Nativo Sistemas), con **reCAPTCHA en el login** — es el
  principal obstáculo. Ver si es v3 (invisible, suele dejar pasar a un bot que
  se comporta como humano) o v2 (challenge). Si es v2, la salida robusta es
  **mantener la sesión viva** (guardar cookies del login y reusarlas) para no
  loguear cada día. Herramienta: Playwright headless (ya se usa en la cartelera
  del mismo server).

**La integración con cuentas corrientes (lo que pidió Diego):**
- No dejar los PDF sueltos: engancharlos al flujo que ya existe. Tocar:
  - `templates/cuenta_corriente.html` — hoy tiene "Importar comprobantes de ARCA"
    (`comprobantes_importar`) y `cuenta_corriente_add(provider_id)`.
  - `routes/providers.py` — ya maneja `pdf_filename` y `tipo_comprobante` (FAC/NC).
  - `services/cuenta_corriente.py::clasificar_comprobante(tipo, total)`.
- Por cada PDF: parsear nº, fecha, tipo (FAC/NC), total → crear el movimiento en
  la CC del proveedor Kellerhoff con el PDF adjunto, clasificado (débito la
  factura, crédito la NC). Reutilizar `clasificar_comprobante`.
- Kellerhoff ya es proveedor conocido (ver `docs/kellerhoff_equivalencias.md`);
  mapear al `provider_id` correcto.

**Definir antes de arrancar:**
- Confirmar autorización de Diego para automatizar el login (es su cuenta).
- Ver el reCAPTCHA con un login de prueba → decide cuánto trabajo es.
- Dónde corre: como sync/job del server (junto a los otros syncs) o script aparte.
- ¿Sólo bajar + adjuntar, o también conciliar contra los pedidos/pagos ya
  cargados? (empezar por bajar + cargar en CC; la conciliación, después.)

---

## 🟡 P2 — Cruce factura vs recepción desde ObServer (sin PDF/Excel) (Diego 2026-08-18)

Hoy `/ingresos` cruza la factura (PDF parseado por IA, `services/factura_ia.py`)
contra el **Excel del ERP subido a mano**. Se puede evitar el Excel/PDF leyendo
la recepción directo de ObServer: **la data ya está accesible.**

**Lo que hay (hallazgo 2026-08-18):**
- El `observer_db` local ya tiene la tabla **`recepciones`** con la estructura
  justa para el cruce:
  `fecha_recepcion · proveedor_cuit · proveedor_nombre · numero_factura ·
  codigo_barra (EAN) · descripcion · cantidad · precio_unitario · lote ·
  vencimiento`. Indexada por `numero_factura`, `proveedor_cuit` y `codigo_barra`.
- `observer_source.py` ya se conecta al ObServer real (SQL Server `ObServerGestion`,
  pymssql) y lee precios/stock/ventas con credenciales que llegan a `Gestion.*`.

**El pero:** la tabla `recepciones` tiene **~8 filas** → el **sync que la puebla
desde ObServer no está corriendo en serio**. Es lo que `routes/observer.py` llama
"traer recepciones desde ObServer todavía no está implementado"
(`_recepciones_implementadas()` chequea `hasattr(observer_source,
'get_recepciones_factura')`, que no existe).

**Qué hacer:**
1. **Terminar el sync de `recepciones`**: escribir `observer_source.get_recepciones_factura`
   (o un sync batch) que lea las recepciones de ObServer (`dbo.` / `Gestion.`) y
   pueble la tabla `recepciones` del observer_db. Definir la clave (nº factura +
   CUIT proveedor) y la periodicidad (junto a los otros syncs).
2. **Wirear `/ingresos`** (`routes/core.py` + `compare_invoice_vs_erp` en
   `routes/invoices.py`) para que, si hay recepción en `recepciones` para ese nº
   de factura, cruce contra eso en vez de pedir el Excel del ERP. El Excel/PDF
   quedan como fallback y para el registro.

**Enlace con el robot de Kellerhoff** (ver ítem anterior): los dos lados del
cruce se automatizan —
- **Factura** → la baja el robot de Kellerhoff (PDF + nº de factura).
- **Mercadería** → sale de `recepciones` (ObServer), matcheada por `numero_factura`.

→ Control factura-vs-ingreso **sin tipear ni subir nada**. La factura además va a
la cuenta corriente (ítem anterior). Es el mismo dato sirviendo a los dos flujos.
