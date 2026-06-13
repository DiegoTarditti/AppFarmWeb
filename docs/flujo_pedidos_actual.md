# Flujo de pedidos — estado actual (2026-06-12)

> Documento autoritativo del flujo end-to-end **cliente → entrega**, después de
> mergear las PRs #199 a #206. Reemplaza al borrador previo `flujo_pedidos_bot_reparto.md`
> en lo operativo.

---

## 1. Vista general

```
ENTRADA A  ─ Cliente WhatsApp/Telegram
           ─→ Bot identifica (DNI/teléfono)
           ─→ Deriva a operador (/atencion)
           ─→ "Cerrar transacción" (form completo)
                                                ╲
ENTRADA B  ─ Operador toma pedido a mano          ╲
           ─→ /pedido/nuevo                        ╲
           ─→ Submit form                           ╲
                                                    ╲
                          ┌──────────────────────────╯
                          ▼
              ╔═══════════════════════════════════╗
              ║  PedidoReparto creado             ║
              ║  estado='en_caja', canal=...      ║
              ║  pagado=true/false según método   ║
              ╚═══════════════════════════════════╝
                          │
                          ▼
                    /caja (3 bandejas)
                    ├─ 💰 Por cobrar (estado='en_caja')
                    ├─ 🛵 Cadetes
                    └─ 📦 Droguería (stock='esperar')
                          │
                          ▼
                    Botón "✓ Cobrado" en caja
                    → emite ticket fiscal en ObServer
                    → estado transiciona según destino+stock
                          │
       ┌──────────────────┼──────────────────┐
       ▼                  ▼                  ▼
  'en_planilla'      'para_retiro'    'esperando_drog'
  (reparto+hay)     (retiro+hay)      (stock=esperar)
       │
       ▼
   /reparto/planilla del día
   - 📦 Pendientes del turno previo (días anteriores sin entregar)
   - ⚪ Sin asignar (sin turno)
   - 🌅 Mañana / 🌆 Tarde (turno asignado)
       │
       ├──→ 🖨️ Imprimir ticket cadete (ESC/POS 80mm vía DockerPanel local)
       └──→ 📤 Publicar al grupo WhatsApp (WAHA)
                     │
                     ▼
       Cadete TOMO (vía grupo o asignación manual)
                     │
                     ▼
                Sale a reparto / entrega
                     │
                     ▼
            /reparto (control por cadete)
            - Stats por cadete: cobrar / debe / entregados
            - Botón "✓ Cobrado" por pedido
            - Botón "Liquidar cadete" → suma envíos no liquidados
```

---

## 2. Entrada A — Pedidos por chat (Bot → `/atencion`)

### 2.1 El cliente escribe al bot

- WhatsApp (vía WAHA) o Telegram.
- Bot identifica al cliente: match por teléfono primero; si no, pide **DNI** y reusa el match. Si tampoco match → opcionalmente pide nombre.
- El cliente puede pedir hablar con un humano explícito ("hablar con persona") o el bot deriva por keyword/IA.
- Al derivar, la conversación queda en cola para `/atencion`.

### 2.2 Operador toma la conversación

**Template:** `templates/atencion.html`  
**Ruta:** `/atencion`

- El operador ve la bandeja (sin asignar, en cola, etc.).
- Click en una conversación → la "toma" y empieza a chatear.
- El bot **NO interviene más** mientras esté tomada.
- Ve la **ficha del cliente** (datos de ObServer si está vinculado) + el último domicilio.

### 2.3 Define la compra en ObServer

> **El operador carga los productos directo en ObServer** (no en AppFarmWeb).  
> AppFarmWeb solo captura el **total** que sale de ObServer y los datos de cobro/destino.

### 2.4 "Cerrar transacción" (modal en `/atencion`)

**Endpoint backend:** `POST /atencion/<conv_id>/cerrar-transaccion` (`routes/atencion.py`)

Captura del modal:
| Sección | Campos |
|---|---|
| **Pago** | `total`, `forma_pago` (link_mp/transferencia/efectivo/tarjeta), campos condicionales según forma: link MP, nro de operación MP, paga_con/vuelto, ult4/nombre/marca de tarjeta |
| **Cobertura** | `obra_social`, `receta_estado` ('no'/'pendiente'/'recibida'), `requiere_firma` (PAMI) |
| **Stock** | `stock_status` ('hay'/'esperar') + `drogueria_id` (dropdown poblado del endpoint `/api/proveedores/drog-activas` — solo droguerías con `activa_ped=true`) |
| **Destino** | `destino` ('reparto'/'retiro') |
| **Envío** | `envio_costo` (cotizado automático cuando hay coords; editable) |
| **Prioridad** | normal / alta / urgente |

### 2.5 Reglas de `pagado` (inferidas del frontend)

- `efectivo` → `pagado=false` (el cadete cobra al entregar).
- `tarjeta` (POS presencial) → `pagado=true`.
- `link_mp` / `transferencia` → `pagado=false` hasta que el operador pegue el **nro de comprobante MP**. Al pegarlo → `pagado=true` automático.

### 2.6 Se crea el `PedidoReparto`

Con `estado='en_caja'`, `canal='atencion'`, `tomo=operador`, sin `turno` (lo asigna la planilla).  
> **Importante** (fix #205): aunque el pago ya esté confirmado (`pagado=true`), el pedido SIEMPRE pasa por `estado='en_caja'` para que el cajero emita el ticket fiscal en ObServer.

---

## 3. Entrada B — Pedido manual (`/pedido/nuevo`)

### 3.1 Casos de uso

- Cliente viene al **mostrador** y pide un envío.
- Llamada **telefónica**.
- WhatsApp por canal **no integrado** con el bot.
- Cliente pide algo para **otra persona** ("para mi mamá, mandámelo a su casa").

### 3.2 Pantalla

**Template:** `templates/pedido_nuevo.html`  
**Ruta:** `GET /pedido/nuevo`

Bloque cliente (componente reusable `_cliente_picker.html`):
- Búsqueda dinámica multitoken contra `obs_clientes` + `clientes` locales.
- Si no existe → botón "+" para crear cliente nuevo.
- Domicilio: dropdown con domicilios guardados del cliente + opción para escribir uno nuevo (con geocoding y pin).

Bloque pedido:
- **Producto**: autocompletar contra `obs_productos`. Al elegir, si NO es venta libre (`id_tipo_venta_control != 'L'`), se prende el toggle "📋 Traer Receta" + badge naranja al lado de Observación.
- **Forma de pago**: dropdown con reglas reactivas (ver tabla abajo).
- **Vuelto**: cuando es efectivo, "Con cuánto paga $" se muestra y el vuelto se calcula auto, **redondeado a $100** (floor).
- **Envío**: cotización automática vía `/config/envio/api/cotizar` al elegir domicilio. Muestra el tramo de la tabla (ej. "Hasta 50 cuadras") + las cuadras reales (ej. "~176 cuadras").

### 3.3 Reglas reactivas según forma de pago

| Forma de pago | Campos extras visibles | `pagado` resultante | Validación bloqueante |
|---|---|---|---|
| (vacío) | — | false | — |
| **Efectivo** | "Con cuánto paga $" + Vuelto editable | false | — |
| **Transfer** | Nro comprobante MP | true si comprobante | sí, sin comprobante no submite |
| **Link** | Link MP + Nro comprobante MP | true si comprobante | sí |
| **Débito / Crédito** | Marca tarjeta + Nro cupón | true | sí, exige marca y cupón |
| **SC** (sin cargo) | — | true | — |

### 3.4 Se crea el `PedidoReparto`

`POST /reparto/pedido` (`routes/reparto.py`) — `canal='manual'`, sin `turno`.

---

## 4. `/caja` — 3 bandejas

**Template:** `templates/caja.html`  
**Endpoint datos:** `GET /caja/api/bandeja/<name>` (`routes/caja.py`)

### 4.1 Bandejas

| Pestaña | Filtro | Para qué |
|---|---|---|
| **💰 Por cobrar** | `estado='en_caja'` | Pedidos esperando que el cajero los procese (ticket fiscal en ObServer + cobro si no estaba) |
| **🛵 Cadetes** | `estado IN ('en_planilla', 'publicado')` | Pedidos listos para despachar — el cajero ve quién viene a retirar |
| **📦 Droguería** | `stock_status='esperar'` (cualquier estado activo) | Esperando ingreso de droguería para destrabar |
| **📜 Tickets sueltos** | tabla `TicketCaja` (vieja) | Legacy — pedidos del flujo viejo, todavía operativo pero deprecado |

### 4.2 Acciones por card

- **"✓ Cobrado"** (solo en "Por cobrar") → `POST /caja/pedido/<id>/cobrar`
  - Marca `pagado=true` (si no estaba).
  - Transiciona `estado` según destino+stock (ver §6.1).
  - El cajero emite el ticket fiscal en ObServer en paralelo (manual).
- **"🖨️ Ticket cadete"** (pestaña "Cadetes") → POST a `localhost:5055/print-ticket` (helper en DockerPanel)
  - Imprime ESC/POS 80mm con: dirección, teléfono, monto a cobrar, forma pago, vuelto, recetas, observaciones.
  - Lo lleva el cadete físico al despachar.

### 4.3 Datos sensibles

El cajero **NO ve el vuelto** ni notas internas. Esos van solo al ticket del cadete (impreso).

---

## 5. `/reparto/planilla` — cockpit del día

**Template:** `templates/reparto_planilla.html`  
**Ruta:** `GET /reparto/planilla?fecha=YYYY-MM-DD`

### 5.1 Secciones

```
📦 Pendientes del turno previo
   - Pedidos con fecha < hoy y estado activo
   - Botones 🌅 / 🌆 para asignar a turno de hoy
   - Color rojo claro (warning)

⚪ Sin asignar
   - Pedidos del día sin turno
   - Botones 🌅 / 🌆 para asignar

🌅 Mañana
   - Pedidos asignados a mañana
   - Sin botones (ya organizados)

🌆 Tarde
   - Pedidos asignados a tarde
```

### 5.2 Columnas por fila

`#`, `Cliente`, `Dirección` (con botón 📍 Maps), `Envío`, `Tomó`, `Importe`, `Forma pago`, `Vuelto` (formato `$`), `Producto`, `Observación`, `Receta`, `Estado`, `Entregó`, `Cadete`, `Recibió`, `Pagado` ($), `WhatsApp` (botón Publicar / timer SLA).

### 5.3 Timers SLA en `WhatsApp`

Cuando un pedido está `publicado` pero no `tomado_por_wsap`:
- 🟢 < 20 min
- 🟡 20-40 min (warning)
- 🔴 > 40 min (crítico)

### 5.4 Publicar al grupo

`POST /reparto/pedido/<id>/publicar` → llama `bot/whatsapp_grupo.publicar_en_grupo(texto)` via WAHA.

**Mensaje** (privacidad: solo lo geográfico):
```
🚚 *Pedido #X*
📍 Av. Córdoba 3400
🗺️ https://www.google.com/maps?q=-32.95,-60.65
🌅 Mañana · 🚨 URGENTE

Responder *tomo* o *yo* para tomarlo.
```

Sin nombre, sin teléfono, sin producto, sin total, sin forma de pago. Todo lo demás se le pasa al cadete por chat 1:1 cuando tome (Fase D — no implementado todavía).

---

## 6. `/reparto` — Control por cadete (post-despacho)

**Template:** `templates/reparto_control.html`  
**Ruta:** `GET /reparto`

### 6.1 Panel por cadete

Card por cadete activo del día con:
- Stats: **Cobrar** (pedidos no pagados que entregó), **Debe** (envíos no liquidados), **Entregados** (total).
- Tabla de pedidos asignados (filas tenues si ya entregados).

### 6.2 Acciones

- **"✓ Cobrado"** por pedido → `POST /reparto/pedido/<id>/cobrar` — marca `pagado=true` cuando el cadete vuelve con la plata (caso efectivo).
- **"Liquidar cadete"** → `POST /reparto/cadete/<cid>/liquidar` — marca `envio_liquidado=true` + `envio_liquidado_en=now` para todos los envíos entregados del cadete. Suma los envíos a su cuenta corriente liquidada.

---

## 7. Modelo de datos relevante

### 7.1 `PedidoReparto` — estados

| Estado | Cuándo |
|---|---|
| `pendiente` | Pedido manual viejo (legacy, antes de la Fase A) |
| `en_caja` | Recién creado desde `/atencion` o `/pedido/nuevo` (espera ticket fiscal) |
| `en_planilla` | Cobrado, destino=reparto, stock=hay (listo para publicar) |
| `publicado` | Publicado al grupo WhatsApp (espera que algún cadete tome) |
| `en_ruta` | Cadete tomó y salió a entregar |
| `entregado` | Entregado al cliente |
| `esperando_drog` | Cobrado pero falta stock (esperando factura de droguería) |
| `para_retiro` | Cobrado, destino=retiro (espera que venga el cliente) |
| `anulado` | Cancelado |

### 7.2 Transición de estados al cobrar

Función helper en `bot/caja.py::proximo_estado_cobrado(destino, stock_status)`:

| destino | stock | Nuevo estado |
|---|---|---|
| reparto | hay | `en_planilla` |
| retiro | hay | `para_retiro` |
| cualquiera | esperar | `esperando_drog` |
| vacío | vacío | `para_retiro` (default conservador) |

### 7.3 Columnas relevantes de `PedidoReparto`

**Pago:**
- `importe` (DECIMAL) — total bruto desde ObServer.
- `total_paciente` (DECIMAL) — efectivamente cobrado al paciente (puede ser 0 si OS cubre).
- `forma_pago` (VARCHAR).
- `paga_con` (DECIMAL) — efectivo: cuánto puso.
- `vuelto` (VARCHAR) — calculado o texto libre (ej. "NO HAY PLATA / $").
- `link_mp` (TEXT).
- `dato_pago_mp` (TEXT) — nro de operación MP / transferencia / cupón de tarjeta.
- `tarjeta_ult4` (VARCHAR 4) — solo los últimos 4, **no PAN completo**.
- `tarjeta_nombre`, `tarjeta_marca` (VARCHAR).
- `pagado` (BOOLEAN).

**Cobertura:**
- `obra_social` (VARCHAR).
- `receta_estado` (VARCHAR) — 'no'/'pendiente'/'recibida'.
- `requiere_firma` (BOOLEAN) — PAMI típico.
- `requiere_receta` (BOOLEAN, legacy) — se mantiene por retrocompat con planilla vieja.

**Stock + destino:**
- `stock_status` (VARCHAR) — 'hay'/'esperar'.
- `drogueria_id` (INTEGER FK proveedores).
- `destino` (VARCHAR) — 'reparto'/'retiro'.
- `envio_costo` (DECIMAL).

**Despacho:**
- `turno` (VARCHAR) — 'mañana'/'tarde' (asignado por operador de planilla).
- `prioridad` (VARCHAR) — 'normal'/'alta'/'urgente'.
- `cadete_id` (INTEGER FK cadetes).
- `tomo` (VARCHAR) — operador que tomó el pedido.

**WhatsApp grupo:**
- `waha_msg_id` (VARCHAR) — ID del mensaje publicado al grupo.
- `publicado_en` (DATETIME).
- `tomado_por_wsap` (VARCHAR) — quién respondió "tomo" en el grupo.
- `tomado_en` (DATETIME).

**Liquidación cadete:**
- `envio_liquidado` (BOOLEAN) — si el envío ya se le pagó al cadete.
- `envio_liquidado_en` (DATETIME).

### 7.4 `Provider.activa_ped`

Flag `BOOLEAN` que controla qué droguerías aparecen en el dropdown "Pedido a" del modal de `/atencion`.  
Default: false.  
Seeded automáticamente para: 20 de Junio, Kellerhoff. Diego cargó Monroe y Del Sud manuales.

---

## 8. Componentes y endpoints clave (índice)

### 8.1 Rutas principales
| Ruta | Método | Para qué |
|---|---|---|
| `/atencion` | GET | Bandeja de chat + chat tomado |
| `/atencion/<conv_id>/cerrar-transaccion` | POST | Crea PedidoReparto desde el modal |
| `/atencion/api/productos/buscar` | GET | Autocompletar producto (incluye flag receta) |
| `/api/clientes/buscar` | GET | Autocompletar cliente (multitoken AND) |
| `/api/proveedores/drog-activas` | GET | Droguerías para el dropdown |
| `/pedido/nuevo` | GET | Pantalla de pedido manual |
| `/reparto/pedido` | POST | Crea PedidoReparto desde `/pedido/nuevo` |
| `/caja` | GET | 3 bandejas (sobre PedidoReparto) + legacy |
| `/caja/api/bandeja/<name>` | GET | JSON de cada bandeja |
| `/caja/pedido/<id>/cobrar` | POST | Marca cobrado + transiciona estado |
| `/reparto/planilla` | GET | Cockpit del día con timers SLA |
| `/reparto/pedido/<id>/publicar` | POST | Publica al grupo WhatsApp via WAHA |
| `/reparto/pedido/<id>/ticket` | GET | JSON con datos para imprimir ticket |
| `/reparto` | GET | Control por cadete (post-despacho) |
| `/reparto/pedido/<id>/cobrar` | POST | Marca cobrado por cadete (efectivo entregado) |
| `/reparto/cadete/<cid>/liquidar` | POST | Liquida envíos no liquidados del cadete |
| `/api/reparto/pedido/<id>/actualizar` | POST | Edición inline genérica (turno, etc.) |
| `/config/envio/api/cotizar` | GET | Cotización por coords / cuadras / dirección |

### 8.2 Componentes reusables
- **`_cliente_picker.html`** + **`static/js/cliente_picker.js`** — embebido en `/pedido/nuevo`, `/atencion`, `/reparto/planilla`. Búsqueda dinámica + alta de cliente + selector de domicilio + geocoding.

### 8.3 Servicios externos
- **ObServer** — sistema de venta. AppFarmWeb NO carga productos ahí; el operador trae el total a mano.
- **WAHA** — gateway WhatsApp para mensajes al grupo de cadetes (`bot/whatsapp_grupo.py`).
- **DockerPanel local** (HTTP en `localhost:5055`) — imprime tickets ESC/POS 80mm en la PC de farmacia.

---

## 9. Lo que queda fuera de este alcance (futuro)

- **Webhook que recibe el "tomo"** del cadete desde el grupo → asignación atómica + cambio de estado a `tomado` (Fase D).
- **Chat 1:1 cadete ↔ bot** post-toma con detalle completo del pedido (privado).
- **State machine completa del cadete**: `tomado → retirado → en_camino → llegué → entregado` con timers por cada transición.
- **Métricas / ranking de cadetes / modelo predictivo de tiempos** — fuera de scope.

---

*Doc 2026-06-12, post #199-#206. Si el flujo cambia (nuevos estados, endpoint nuevo, cambio en el modal), actualizá este doc en el mismo PR.*
