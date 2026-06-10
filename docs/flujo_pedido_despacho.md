# Flujo operativo end-to-end — Chat → Pedido → Reparto → Cierre

> Sesión de análisis del 2026-05-28 entre Diego y Claude. Captura el flujo
> completo de un pedido desde que el cliente lo solicita hasta que cierra
> con el cadete devolviendo todo lo que corresponde. Pensado como mapa
> para volver mañana y arrancar la implementación sin perder contexto.

---

## 🎯 Visión general

```
┌─────────────────────────────────────────────────────────────────────┐
│                                                                       │
│  ENTRADA           ARMADO         COBRO        FISCAL    REPARTO     │
│  ─────────         ──────         ─────        ──────    ───────     │
│                                                                       │
│  /atencion ──┐                                                       │
│  (bot)       ├──→ Define qué    →  Forma   →  Caja   →  Planilla    │
│  /pedido/nuevo                  pago        (ticket    (cadete sale) │
│  (manual)    ┘   - cliente                  fiscal)                  │
│                  - dirección                                          │
│                  - cobertura                                          │
│                  - destino                                            │
│                  - stock                                              │
│                                                                       │
│                  + Carga de venta en ObServer (productos + envío)    │
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
              ┌──────────────────────────┐
              │  ObServer es la fuente de │
              │  verdad del ticket fiscal │
              │  AppFarmWeb es la capa   │
              │  operativa alrededor      │
              └──────────────────────────┘
```

**Principio clave**: AppFarmWeb **no carga productos**, eso vive en ObServer. AppFarmWeb maneja: entrada del cliente (chat/manual), forma de pago, coordinación de caja/cadete/cliente, métricas, y registro operativo.

---

## 📥 Entradas — cómo llega un pedido

Dos canales, mismo modelo de datos abajo:

### 1. Vía bot → `/atencion`
- Cliente escribe por Telegram (mañana WhatsApp)
- Bot deriva siempre al operador (handoff)
- Operador toma la conversación y arma el pedido

### 2. Manual → `/pedido/nuevo`
Casos típicos:
- **Retira express** — llega al mostrador, paga rápido y se lleva
- **Llamada telefónica** — operador toma datos por teléfono
- **WhatsApp por canal no integrado** — el cliente escribió a un número fuera del bot
- **Mostrador para terceros** — "vengo a buscar para mi mamá, mandámelo a su casa"

**Campo `canal`** (ya existe `pCanal` en el HTML):
- `bot_telegram`, `bot_whatsapp` (entradas automáticas)
- `mostrador`, `telefono`, `whatsapp_manual`, `otros` (entradas manuales)

**Detalle "para tercero"**: quien viene al mostrador ≠ destinatario del envío. El form tiene que poder distinguir:
- Cliente / titular de la cuenta (la mamá)
- Quien gestiona el pedido (el hijo en mostrador, anónimo o anotado)

---

## 👤 Etapa 1 — Armado del pedido (en `/atencion` o `/pedido/nuevo`)

### Datos a capturar

| Bloque | Campos | Notas |
|---|---|---|
| **Cliente** | nombre, apellido, dni, teléfono | Buscador multi-palabra → `cliente_picker` |
| **Dirección** | calle, número, piso, depto, referencia, ciudad, coords | Geocoder + domicilios guardados |
| **Cobertura** | obra_social (NULL/PAMI/IOMA/...), requiere_receta, requiere_firma | Define qué cobra y qué documentos llevar |
| **Destino** | reparto / retiro presencial | Mutable durante todo el flujo |
| **Stock** | hay / esperar droguería | Si "esperar" → campo `pedido_a_drogueria_id` |
| **Canal** | bot / mostrador / teléfono / wsap / otros | Ya capturado en `pCanal` |
| **Prioridad** | normal / alta / urgente | Afecta SLA, visualización y notificaciones |
| **Envío** | costo (auto del cotizador), distancia_km | Auto-cotiza al elegir dirección |
| **Nota** | texto libre | Para el cadete, no fiscal |

### Pregunta clave: stock + destino (2 ejes ortogonales)

```
┌── ¿Stock? ──────────────┐    ┌── ¿Cómo sale? ──┐
│  ☐ Hay                    │    │  ☐ Reparto       │
│  ☐ Esperar droguería       │    │  ☐ Retiro         │
│     (+ dropdown drogueria) │    └─────────────────┘
└──────────────────────────┘
```

4 combinaciones:
- Sí + Reparto → sale hoy con cadete
- Sí + Retiro → preparado, espera cliente
- Esperar + Reparto → cuando entra de droguería, sale próximo reparto
- Esperar + Retiro → cuando entra, queda guardado para retiro

### Estado "esperar droguería"

```
Estado: "Pedido a Kellerhoff"   (estado base + droguería FK a proveedores)
```

**Auto-trigger al llegar la factura**: cuando se carga una factura de "Kellerhoff" en AppFarmWeb, detectar los códigos de producto y **destrabar automáticamente** los pedidos esperando esa droguería con ese producto. Notif al operador: "📦 Llegó X, hay 3 pedidos para despachar".

**Sugerencia inteligente**: al marcar "no hay stock", AppFarmWeb sugiere la droguería más probable basándose en histórico (qué droguería suele traer ese producto). Operador confirma con un click.

**Bandeja "pendientes por droguería"**: vista filtrada "todo lo que estoy esperando de Kellerhoff" para tener control.

### Cobertura — PAMI y similares

- Producto: sin cargo al paciente (cubre la OS)
- **Solo se cobra envío** si va por reparto
- Si retira, total = $0 al paciente
- Documentos extra para el cadete:
  - 📋 Receta (firmada por médico) — `requiere_receta='pendiente'|'recibida'|'no'`
  - 📝 Papel de autorización que el paciente firma al recibir — `requiere_firma=bool`
  - Probablemente troquel/etiqueta para presentar a la OS
- **ObServer probablemente ya maneja** la lógica de qué cubre y qué paga el paciente. AppFarmWeb captura la OS y propaga al ticket cadete.

### Envío — cotización automática

Al validar el domicilio (cliente_picker), se dispara:

```
Domicilio (con coords)
        │
        ▼
1. Calcula distancia (haversine farmacia ↔ destino)
        │
        ▼
2. Lee tarifas de /envio config:
   - ¿Cae en zona con tarifa fija? → usa esa
   - Si no, busca tramo por cuadras
        │
        ▼
3. Sugiere costo en pEnvio (operador puede override)
        │
        ▼
4. Suma al total que se le dice al cliente
```

**UX**: el envío se calcula solo. El campo `pCuadras` permite override manual. Botón "Cotizar" sigue para forzar recálculo.

### Destino mutable

El destino (reparto/retiro) se puede cambiar después de creado el pedido:
- Cliente pidió retiro → llamó después: "Mejor mandámelo" → pasa a reparto
- Cliente pidió reparto → llamó: "Voy a buscar yo" → pasa a retiro

Implicancias:
- `PATCH /api/pedido/<id>` para cambiar destino
- **Auditoría**: quién cambió, cuándo, motivo (campo libre opcional)
- Reacciones en cascada:
  - `retiro → reparto`: aparece en planilla del día (o próxima si pasó hora)
  - `reparto → retiro`: sale de la ruta del cadete; si ya salió, notificar al cadete
- **Cambio de total**: si pasa a reparto, se suma envío → recotizar y pedir diferencia, o farmacia se lo come (decisión política)

---

## 💰 Etapa 2 — Cobro y forma de pago

Después que ObServer dio el total (productos + envío + cobertura aplicada), se captura la forma de pago en AppFarmWeb (no en ObServer).

### Dropdown inteligente — al elegir dispara acción

| Forma | Acción automática | Datos guardados |
|---|---|---|
| 💸 **Link MP** | Genera link + lo pega en el chat | `link_mp`, `dato_pago_mp` (cuando confirma), `nro_op_mp` |
| 🏦 **Transferencia** | Manda alias por chat (botón rápido o auto) | `alias`, `nro_op_transf` |
| 💵 **Efectivo** | Pregunta "¿con cuánto paga?" → calcula vuelto | `paga_con`, `vuelto` |
| 💳 **Tarjeta crédito** | Marca el modo de cobro presencial | `marca`, `ultimos_4_digitos`, `titular` (NUNCA datos completos) |

### Link MP — flujo completo

```
1. Operador elige "Link MP" en dropdown
2. Sistema genera link y lo guarda en el campo de la transacción
3. Operador pasa link al cliente
4. Cliente paga (offline para nosotros)
5. Operador consulta panel MP manualmente
6. Si pagó: pega el número de operación en un campo libre
7. Ese dato queda asociado al pedido para conciliar después contra extracto MP
```

### Efectivo — vuelto se propaga

```
Pregunta: "¿con cuánto paga?" → $5.000
Sistema: vuelto = $5.000 - total = $400
Vuelto aparece después en:
   - Ticket térmico del cadete: "Cobrar efvo $4.600 (vuelto $400)"
   - Planilla
   - Reporte fin del día
```

### Tarjeta de crédito (presencial)

- Cero datos sensibles guardados (no PAN completo, no CVV)
- Solo:
  - últimos 4 dígitos
  - marca (Visa/Master/Amex)
  - nombre titular (opcional, para reconciliar)

### Cuando se cobró → manda a caja

Pasa de la pantalla del operador a la cola de caja con datos filtrados (lo que el cajero necesita).

---

## 🧾 Etapa 3 — Caja (ticket fiscal)

### Lo que el cajero ve

```
┌── Ticket pendiente ──────────────┐
│ #1234                              │
│ Pérez, Juan                        │
│ Forma pago: MP (link)              │
│ Total: $52.500                     │
│ Domicilio validado: Av. Córdoba    │
│ Destino: Reparto                   │
│                                     │
│ [📋 Copiar nro op] [Cobrar] [Anular] │
└────────────────────────────────────┘
```

**Lo que NO muestra al cajero**:
- Detalle de productos (eso vive en ObServer)
- Vuelto (eso es para el cadete, no para el cajero)
- Dato sensible

### Cierre fiscal

```
1. Cajero ve transacción en /caja
2. Click "📋 Copiar nro op" → al portapapeles
3. Cajero pasa a ObServer (en otra app/ventana)
4. Pega el dato en el campo TXT libre del ticket fiscal de ObServer
5. ObServer emite ticket fiscal con ese dato impreso
6. Vuelve a /caja → marca [Cobrado fiscal]
7. El pedido pasa al siguiente paso:
   - Si destino=retiro → bandeja "Para retirar"
   - Si destino=reparto → bandeja "Para repartir" (planilla)
```

### Roles del cajero (triple función)

| Rol | Acción | Pantalla |
|---|---|---|
| **Cajero** | Cobra/anula tickets fiscales | `/caja` |
| **Despachante** | Cadete viene a retirar → entrega paquete + vuelto + ticket térmico → marca RETIRADO | `/caja` con panel lateral |
| **Receptor de droguería** | Llega droguería → recibe factura → destraba pedidos en estado "Pedido a X" | Pantalla aparte o panel |

**UX**: cajero NO debe cambiar de pantalla todo el tiempo. Diseño con sidebar/panel de notificaciones:

```
┌────────── /caja ──────────┐
│  Lista de tickets a cobrar  │
│                              │
│  ───── Panel lateral ─────   │
│  📦 Cadete @Juan llega →     │
│  📦 Llega droguería →        │
│  ⚠️ #1234 sin tomar (32 min) │
│  ✅ #1230 entregado por @Mar │
│                              │
└────────────────────────────┘
```

---

## 🎫 Etapa 4 — Ticket térmico (80mm) para el cadete

Separado del ticket fiscal de ObServer. Es el papelito operativo que el cadete lleva.

### Contenido típico

```
┌────────────────────────────┐
│  # 1234                     │
│  🚨 URGENTE  (si aplica)    │
│  ⚠️ PEDIR RECETA            │
│  📝 FIRMAR AUTORIZACIÓN     │
│  💊 PAMI - SOLO ENVÍO       │
│                              │
│  Juan Pérez                  │
│  📱 11-5555-1234            │
│  Av. Córdoba 3400, piso 2 B  │
│  Ref: monoblock 4, torre B   │
│                              │
│  Cobrar: efvo $4.600         │
│  Vuelto: $400                │
│                              │
│  🌅 Mañana · Ruta 2 · #3     │
│                              │
│  Nota: cliente sordo, tocar  │
│  timbre 2 veces              │
│                              │
│  [QR para marcar entregado] │
└────────────────────────────┘
```

### Especs técnicas

- Ancho 80mm (≈32 caracteres por línea con fuente normal)
- Output: ESC/POS directo a impresora térmica o PDF render
- Trigger de impresión: al confirmar cobro en caja, o cuando el cajero arma planilla

---

## 🛣 Etapa 5 — Planilla del día y reparto

### Qué entra a planilla

```
DESTINO  +  STOCK            →  ¿VA A PLANILLA?
─────────────────────────────────────────────────
Reparto  +  Hay              →  ✅ entrega del día
Reparto  +  Esperar drog.    →  ✅ cuando llega, entra
Retiro   +  Hay              →  ❌ bandeja "para retirar" aparte
Retiro   +  Esperar drog.    →  ❌ cuando llega, "para retirar"
```

### Publicación al grupo de cadetes (Telegram primero, WhatsApp después)

**Por qué Telegram primero**:
- Ya hay infra del bot
- Botones inline ideales para "TOMAR"
- WhatsApp Business API tiene fricción real (verificación, templates pre-aprobados, costos)
- Migrar a WhatsApp cuando el flujo esté maduro

### Mensaje público al grupo (mínimo necesario)

```
📦 #1234
📍 Av. Córdoba 3400, Banfield
🌅 Mañana
[TOMAR] ← botón inline
```

**Por qué tan poca info en el grupo**:
- 🔒 Privacidad del cliente (no se filtra nombre, teléfono, productos)
- 🚫 Anti-fraude (no se publica "cobrar $50.000 efvo")
- 🧹 Menos ruido — el cadete solo decide "¿queda en mi zona?"

### Mecánica "TOMAR" (pull con override manual)

```
Planilla publica al grupo
       │
       ▼
Cadetes ven la lista
       │
       ▼
El primero que clickea TOMAR → race condition
   Resto recibe "Ya fue tomado por @Juan"
       │
       ▼
Override manual: supervisor puede asignar a cadete X
   (salta el "tomar" libre)
```

### Después de TOMAR → chat privado con el cadete

```
Bot abre chat 1:1 (si no existe ya)
       │
       ▼
Manda detalle completo:
   📍 Dirección, piso, depto, ref
   👤 Cliente, teléfono
   💰 Total, vuelto, forma pago
   ⚠️ Receta, autorización
   📷 Receta adjunta (si la mandó por chat)
       │
       ▼
Botones inline:
   [En camino] [Llegué] [Entregado] [Problema]
```

**Detalle**: el cadete tiene 1 conversación con el bot, donde van apareciendo sus pedidos uno tras otro. No 1 chat por pedido.

### Estados del pedido (state machine)

```
disponible (publicado en grupo)
    │
    │ ⏱ 20 min → warning si no toma nadie
    │ (10 min si prioridad urgente)
    ▼
TOMADO (cadete clickeó)
    │
    │ ⏱ 40 min → warning si no vino a retirar
    ▼
RETIRADO (vino a la farmacia, le entregaron
          paquete + ticket térmico + vuelto inicial +
          receta + form autorización)
    │
    ▼
EN_CAMINO (saliendo de farmacia)
    │
    ▼
LLEGUÉ (en la puerta del cliente)
    │
    │ ⏱ si pasan 10 min sin entregar → revisar
    ▼
ENTREGADO (cobrado + entregado)

(rama de error)
FALLIDO / NO_ESTABA → reagendar, vuelve a planilla próximo turno
```

### Timers visuales en planilla

```
ESTADOS POR TIEMPO (publicado → tomado):
   ⏱ 0-20 min     🟢 OK
   ⏱ 20-40 min    🟡 Warning
   ⏱ 40+ min      🔴 Crítico

URGENTES: timers a la mitad (10/20 min)
```

**UX live**:
- Update en vivo (WebSocket o polling 30s)
- Cuenta regresiva visible por pedido
- Cambio de color automático
- Push browser + beep para críticos
- Acciones del supervisor en warning: asignar manual, contactar cadete, repostear

### Prioridad

3 niveles: `normal | alta | urgente`

**Casos típicos** que disparan alta/urgente:
- 💊 Urgencia médica (insulina, oxígeno)
- ⏰ Cliente esperando hace mucho
- 🌟 Cliente VIP
- 📋 PAMI con vencimiento
- 💰 Pedido grande
- 😠 Cliente quejoso previo

**Seteo**:
- Manual (operador en `/atencion` o `/pedido/nuevo`)
- Auto-derivada por reglas (PAMI con vencimiento, monto > X, cliente flaggeado en ficha)

**Visualización**:
- Planilla: separados en bloques o badge color
- Mensaje al grupo: prefijo "🚨" o "🟡"
- Ticket térmico: banda roja
- Cuidado: timers más estrictos, escalación si supervisor no atiende warning urgente

---

## 🔁 Etapa 6 — Retorno del cadete a la farmacia

Cuando el cadete vuelve, el cajero hace checkpoint de todo lo que debía traer.

### Checklist por pedido

| Item | Cuándo aplica | Trigger |
|---|---|---|
| 📋 **Receta firmada** | Productos con receta | `requiere_receta=true` |
| 📝 **Autorización** | PAMI / OS con firma | `requiere_firma=true` |
| 💰 **Vuelto** | Cobró efvo > total | `vuelto > 0` |
| 🏷 **Troquel/etiqueta** | Reembolso a OS | depende OS |
| 📄 **Factura A** | Si cliente la pidió | `tipo_facturacion='A'` |
| 📷 **Comprobante transfer** | Algunos clientes | opcional |

### Modelo (opción simple)

Columnas en `pedido_reparto`:
```
devuelto_receta: bool (NULL si no aplica)
devuelto_autorizacion: bool
devuelto_vuelto_monto: numeric
devuelto_troquel: bool
devuelto_otros: text
fecha_retorno: ts
recibido_por: usuario_id
```

### UX del cierre

```
┌──── /caja · Cadete vuelve ────┐
│  @Juan · Pedidos pendientes (5) │
│                                  │
│  ┌─ #1234 ─────────────────┐    │
│  │ ☑ Receta                  │    │
│  │ ☑ Vuelto $400             │    │
│  │ [Cerrar] [Falta algo]     │    │
│  └──────────────────────────┘    │
│                                  │
│  ┌─ #1235 ─────────────────┐    │
│  │ ☐ Receta ⚠️ falta         │    │
│  │ ☑ Vuelto $0               │    │
│  │ [Cerrar parcial]          │    │
│  └──────────────────────────┘    │
└────────────────────────────────┘
```

Si falta algo → estado `INCOMPLETO` → escalación al supervisor.

---

## 💼 Cadete — cuenta corriente

### Regla

El costo de envío que el cliente paga es **todo para el cadete**.

```
Cliente paga $52.500 (productos $50.000 + envío $2.500)
            │
   ┌────────┴────────┐
   ▼                 ▼
PAGO EFVO        PAGO MP/TRANSF
(cadete cobra    (farmacia cobró
 en mano)         online, debe al
                  cadete)
   │                 │
   ▼                 ▼
Devuelve $50.000    +$2.500 a su
a la farmacia        cuenta cte
Se queda $2.500
```

### Tabla `cadete_cta_cte`

```
id · cadete_id · fecha · concepto · monto · pedido_id · signo
─────────────────────────────────────────────────────────────
…   12          12/06   envio_mp    +2500    1234       +
…   12          12/06   envio_pami  +2500    1240       +
…   12          12/06   envio_efvo   2500    1235       0   ← cobró cash, no acumula
…   12          15/06   liquidacion -5000     -         −   ← le pagaste
```

### Conceptos típicos

- `+envio_pendiente` — cliente pagó online, farmacia debe al cadete
- `+envio_pami` — PAMI cubrió, solo el envío al cadete
- `0 envio_efvo` — cobró cash, no genera deuda
- `−liquidacion` — vos le pagás, cuenta vuelve a 0
- `±ajuste` — corrección manual

### Vistas

**Para el cadete**:
```
@Juan · Cta Cte
─────────────────────────
Pendiente liquidar: $4.500 (3 envíos)
Cobrado efvo hoy:   $2.500 (1 envío)
Última liquidación: hace 8 días ($12.000)
[Liquidar ahora]
```

**Para vos**:
```
Liquidaciones pendientes:
  @Juan   $4.500 · 3 envíos
  @María  $2.500 · 1 envío
  @Pedro  $0 ✓
Total: $7.000
```

**Detalle**: el sistema sabe si fue efvo o no (lo tiene del pedido), entonces decide automáticamente si la cta cte se mueve. Cero input extra del cadete.

---

## 📊 Analytics y predicción

### Timestamps a persistir en `pedido_reparto`

```
ts_publicado, ts_tomado, ts_retirado, ts_salio,
ts_llegue, ts_entregado, ts_fallido,
cadete_id, distancia_km
```

Todo lo demás se calcula de ahí.

### Métricas derivadas por cadete

| Métrica | Fórmula | Para qué |
|---|---|---|
| ⏱ Tiempo en tomar | `tomado − publicado` | Velocidad respuesta |
| 🏃 Tiempo en venir | `retirado − tomado` | Disciplina |
| 🚗 Tiempo en camino | `llegue − salio` | Eficiencia logística |
| 📦 Tiempo en puerta | `entregado − llegue` | Problemas en entrega |
| ⏱ Tiempo total | `entregado − publicado` | KPI principal |
| 📊 Entregas/turno | count | Productividad |
| ✅ % on-time | vs estimación | Calidad |
| ❌ % fallidos | fallidos/total | Confiabilidad |
| 📏 Km recorridos | suma distancias | Esfuerzo |
| 💰 Recaudación envíos | suma cobrada | Económico |
| ✅ % cerrado al 1er intento | sin faltantes en retorno | Disciplina docs |

### Modelo predictivo de tiempo de entrega

**Versión 1 — simple** (rápido, valioso):
```
tiempo_estimado = distancia_km / velocidad_promedio_del_cadete
                 + buffer_promedio_en_puerta (≈3 min)

velocidad_promedio = histórico por cadete (cada uno tiene su perfil)
```

**Versión 2 — con features** (más adelante):
```
features:
  - distancia_km
  - hora_del_dia (tráfico)
  - dia_semana
  - cadete_id
  - cant_pedidos_pendientes (carga)
  - clima (lluvia → +20%)
  - barrio destino
→ regresión lineal o random forest
→ tiempo estimado + intervalo de confianza
```

**Usos del estimado**:
- Cliente: "tu pedido va a estar a las 15:30 ±10 min"
- Operador: warning si supera estimado por +15%
- Cadete: ranking según cumplimiento del estimado

### Sistema de incentivos

```
Ranking semanal/mensual por:
   - Entregas a tiempo (%)
   - Volumen (cantidad)
   - Calificación cliente (si se pide)
   - Sin fallidos / sin faltantes en retorno

Top 3 → bonus $ o reconocimiento público en grupo
```

**Calificación cliente** (opcional pero potente): después de entregado, mensaje al cliente "¿Cómo te atendió Juan? ⭐⭐⭐⭐⭐". Asociado al cadete + pedido.

### Dashboard por cadete

```
@Juan · Esta semana
──────────────────────
📦 Entregas:        47
⏱ Tiempo promedio:  38 min (récord 22 min)
✅ On-time:         91% (target 85%)
❌ Fallidos:        2
⭐ Rating cliente:   4.7 / 5
💰 Acumulado:       $24.500 pendiente liquidar
✅ % cerrado 1er intento: 96%
🏆 Ranking:          2° de 5
```

---

## 🕐 Horarios de atención y routing

### Problema

Algunos operadores son especializados (Laboratorio para recetas magistrales). Tienen horarios distintos. Si el cliente escribe fuera de horario, hay que decidir qué hacer.

### Modelo

**Tabla `operador_horario`**:
```
operador_id, dia_semana (0-6), hora_desde, hora_hasta

@laboratorista · L-V 9:00-17:00
@general1     · L-S 9:00-13:00 + L-V 16:00-20:00
@general2     · S-D 9:00-21:00 (fines de semana)
```

**Tabla `tipo_consulta`** (categorías de routing):
```
slug        · keywords_detect            · primario        · backup
─────────────────────────────────────────────────────────────────
general     · NULL (default)             · @general1       · @general2
magistral   · 'fórmula','crema','jarabe' · @laboratorista  · @general1
cobranzas   · 'devolver','factura'       · @cobranzas       · @general1
urgente     · 'urgente','insulina'       · @general1       · @owner
```

### Flujo del bot al recibir mensaje

```
Mensaje entra
    │
    ▼
Detectar tipo_consulta (keyword, canal, manual)
    │
    ▼
Buscar operador_primario del tipo
    │
    ├─ ✅ En horario → asignar
    │
    └─ ❌ Fuera de horario:
         │
         ├─ Backup en horario → asignar al backup
         │     (con nota "consulta de X, avisar a Y mañana")
         │
         └─ Nadie disponible → respuesta auto + encolar
               "Recibimos tu consulta. Te respondemos lunes 9hs ☀️"
```

### Conexión con `/atencion`

- Bandeja con **tabs por tipo de cola** (General / Magistral / Cobranzas / Urgente)
- Cada operador ve solo las colas que le tocan
- Cuando un operador toma una consulta de otra cola (porque el especialista está fuera de horario), aparece destacada con nota "Magistral — derivar a @laboratorista cuando esté disponible"
- Bot puede dejar un pinned message "📌 Consulta para @laboratorista" para que la levante al entrar

### Métricas

- Tiempo de respuesta por tipo de cola
- % consultas fuera de horario (evaluar extender)
- Carga por operador (balanceo)
- Consultas re-encoladas para el día siguiente (cuántas, qué tipo)

---

## 🧱 Modelo de datos — resumen

### Nuevas tablas / columnas a evaluar

| Tabla | Propósito |
|---|---|
| `pedido` (extender existente) | Agregar `obra_social`, `requiere_receta`, `requiere_firma`, `prioridad`, `stock_status`, `pedido_a_drogueria_id`, `total_paciente`, `total_envio`, `paga_con`, `vuelto`, `link_mp`, `dato_pago_mp`, `nro_op`, `ultimos_4`, `marca_tarjeta`, `titular_tarjeta`, `quien_gestiona`, `canal_entrada` |
| `pedido_reparto` (extender existente) | Agregar todos los `ts_*` (publicado, tomado, retirado, etc.), `distancia_km`, `tiempo_estimado_min`, `cadete_id`, `devuelto_*` |
| `cadete_cta_cte` (nueva) | id, cadete_id, fecha, concepto, monto, pedido_id, signo |
| `operador_horario` (nueva) | operador_id, dia_semana, hora_desde, hora_hasta |
| `tipo_consulta` (nueva) | slug, nombre, keywords_detect, operador_primario_id, backup_id |
| `conversacion` (extender) | Agregar `tipo_consulta_slug` |

### Componente reusable

**`cliente_picker`** (ya documentado en `mejoras_pendientes.md`): la sección Cliente+Dirección de `/pedido/nuevo` extraída como macro Jinja + JS namespaceado + endpoints en `/api/clientes/*`. Reusable desde `/atencion`, `/caja`, futuras pantallas.

---

## ⏭ Lo que decidimos descartar

### Flow engine genérico

Idea: una entidad `Flow` con stages que generalice las 6 pantallas (`/atencion`, `/pedido/nuevo`, `/caja`, `/reparto`, `/reparto/planilla`, `/envio`) bajo un motor común.

**Por qué descartado**:
- Las rutas actuales son simples; la abstracción las haría más complejas
- Regla del 3: 1 flujo claro hoy ≠ 3 repetidos. El shape se diseña con info real
- Flow engines genéricos tienen tasa de fracaso alta — desproporcionados para 1 farmacia

### Adoptado en su lugar

- **Botones de transición explícitos** pantalla-a-pantalla cuando se necesite
- **Componentes reusables** como `cliente_picker` para puntos de fricción
- Si dentro de 1-2 meses operando aparecen 3 flujos genuinos repetidos, recién ahí evaluar Flow engine

---

## 📋 Próximos pasos sugeridos (orden propuesto)

> No es para hacer todo de una. Es un mapa para retomar mañana o cuando arranques.

### Tier 1 — Quick wins (cada uno 1-2 h)

1. **Extraer `cliente_picker`** como componente reusable (`docs/mejoras_pendientes.md` ya tiene el plan)
2. **Mover endpoints** `/reparto/api/buscar-cliente` → `/api/clientes/*` con redirect 301
3. **Mover `/pedido/nuevo`** a su propio `routes/pedidos.py`
4. **Renombrar `/envio`** → `/config/envio` o `/tarifas` (es config, no operación)

### Tier 2 — Capturar los campos nuevos del pedido (medio día)

5. Migración: agregar columnas nuevas a `pedido` y `pedido_reparto`
6. UI: dropdown stock + droguería, prioridad, OS, requiere_receta/firma
7. Lógica de envío auto al elegir domicilio
8. Forma de pago como dropdown inteligente con acciones

### Tier 3 — Cerrar el loop con el cadete (1-2 días)

9. State machine con timestamps en cada transición
10. Publicación al grupo Telegram con botones inline
11. Chat 1:1 al tomar + botones de feedback
12. Live update en planilla (WebSocket o polling)
13. Timers visuales y warnings

### Tier 4 — Retorno y cierre (1 día)

14. UI checklist para el cajero cuando vuelve el cadete
15. Cuenta corriente del cadete
16. Liquidaciones

### Tier 5 — Analytics e incentivos (en paralelo, varios días)

17. Dashboard por cadete
18. Modelo predictivo simple (v1)
19. Ranking + sistema de premios
20. Calificación del cliente post-entrega

### Tier 6 — Horarios y routing (1 día)

21. Tablas `operador_horario` + `tipo_consulta`
22. Routing en el bot al recibir mensaje
23. Tabs por cola en `/atencion`
24. Respuestas automáticas fuera de horario

### Tier 7 — Ticket térmico (medio día)

25. Template ESC/POS o PDF render
26. Botón de impresión desde `/caja`

---

## 🗂 Apéndice — rutas analizadas en esta sesión

| Ruta actual | Función | Notas de mejora |
|---|---|---|
| `/atencion` | Bandeja del bot + chat humano | Routing por horarios + tabs por cola |
| `/pedido/nuevo` | Crear pedido manual | Mover a `routes/pedidos.py`, sumar campos nuevos |
| `/caja` | Cobro fiscal + recepción cadete + droguería | Sidebar de notificaciones, panel del cadete que vuelve |
| `/reparto` | Mapa del día | Sigue con su uso actual |
| `/reparto/planilla` | Vista live de la entrega | Convertir en hub central con timers, prioridad, integración Telegram |
| `/envio` | Config tarifas + cotizador | Renombrar a config; el cálculo es input automático del chat/pedido |

---

*Doc generado en sesión 2026-05-28. Cuando arranques mañana abrí este archivo y tenés el mapa completo.*
