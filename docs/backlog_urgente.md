# Backlog URGENTE — AppFarmWeb

_Creado: 2026-05-21. Lista corta y priorizada de lo que duele pronto. Lo demás
(features, mejoras no urgentes) vive en `docs/mejoras_pendientes.md`._

Orden: P0 = afecta producción AHORA · P1 = riesgo/correctitud · P2 = deuda que acumula.

---

## 🟠 P1 — Búsqueda de clientes por DNI (Diego 2026-06-22)

**Qué pasa:** hoy `bot/data.buscar_clientes` y el cliente_picker (`/api/clientes/buscar`)
buscan por nombre/apellido/teléfono pero NO por DNI. El bot tiene flujo de
identificación que pide DNI (`identificar_por_dni`) pero solo lo usa para
matchear contra ObServer; los Cliente locales con `dni` guardado no se levantan
por ese campo en otros buscadores (panel /atencion, picker /pedido/nuevo).

**Acción:** sumar `Cliente.dni` al buscador. Que un operador pueda escribir
"30123456" en el campo Cliente y traiga el match. También revisar `Cliente.dni`
indexado (¿hay índice?) — si hay 50k clientes vale tenerlo.

**Esfuerzo:** ~30 min. **Tocá:** `bot/data.py`, `routes/clientes.py` `/api/clientes/buscar`.

---

## 🔴 P0 — Re-sync de ventas (prod muestra ventas INFLADAS)

**Qué pasa:** el fix `ee12bc6` (ventas netas) hace que `sync_ventas_mensuales`
reste devoluciones/notas de crédito. Pero **el dato viejo en `obs_ventas_mensuales`
sigue inflado hasta correr el sync**. Mientras tanto, TODO lo que lee ventas
(gráficos, avg_3m/12m de `producto_metrics`, sugeridos de armado, dashboard)
muestra números más altos de lo real.

**Acción:** correr el sync de ventas desde la farmacia (LAN de ObServer) — botón
"Sincronizar" / DockerPanel. Es local-only (no se puede desde Render).

**Quién:** Diego, en la farmacia. **Esfuerzo:** 5 min (correr el sync).

---

## 🟠 P1 — Planificadores ignoran unidades_minima y cantidad_reposicion_fija

**Qué pasa:** `/pedido/prueba` (estacional) e `/informes/pedido-auto` NO respetan
el mínimo de oferta (`OfertaMinimo.unidades_minima`) ni la cantidad fija de
reposición (`Producto.cantidad_reposicion_fija`). El armado táctico
(`/compras/dia/armar`) sí. Resultado: el planificador sugiere una cantidad que
después no coincide con lo que sale al armar el pedido → confusión operativa.

**Acción:** cablear ambos en el motor (`services/calculo_pedido.py` +
`services/pedido_estacional.py`) y exponerlos como flags de config. Es
prerequisito del "motor de pantallas" (ver `plan_motor_pantallas_pedido.md`).

**Esfuerzo:** 2-3 h backend. **Detalle:** entrada en `mejoras_pendientes.md`.

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
