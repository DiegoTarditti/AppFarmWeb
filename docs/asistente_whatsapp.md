# Asistente de atención por WhatsApp — Farmacia Badia

Proyecto para automatizar la atención de la farmacia por WhatsApp (y otros
canales), reemplazando a futuro el software **Trii** que hoy se usa solo para
WhatsApp con ~8 usuarios. Recomendación del dueño (Lisandro): arrancar parecido
al bot de **Farmacia Central Oeste** y luego agregar.

> Estado: **Fase 0 en marcha** (prototipo en Telegram, local, con data real).
> Vive en `bot/` dentro de AppFarmWeb (reusa la data de la farmacia).

---

## 1. Visión

Un asistente que atiende a los clientes por WhatsApp con un **modelo híbrido**:
menús para lo estructurado + **IA (Claude)** para lenguaje libre, conectado a la
**data real de la farmacia** (stock, precios, equivalencias). Cuando hace falta,
**deriva a un operador humano**. Atiende **varias líneas** (números) en una sola
bandeja.

**Diferencial vs. los bots del mercado** (Central Oeste / chatbotfarmacias.com):
ellos son genéricos y semi-automáticos (toman el pedido y derivan). El nuestro
**consulta el stock/precio real** y **lee recetas** — eso no lo tiene nadie.

---

## 2. Estado actual (Fase 0 — hecho)

Bot conversacional funcionando en **Telegram** (local, con la data real de Badia):

| Capacidad | Cómo |
|---|---|
| 🔎 Consultar producto | Búsqueda directa por nombre → precio + stock (`product_analytics`) |
| 💬 IA conversacional | Claude entiende lenguaje natural + busca en el stock (tool use). Texto libre va directo a la IA (híbrido) |
| 📸 **Leer receta** | Claude visión lee la foto (incl. **manuscrita**), extrae los medicamentos, cruza con stock |
| 🕐 Horarios · 🙋 Derivar | Menú |
| Ficha de la farmacia | El bot solo afirma lo que está en la ficha; lo que no, deriva (anti-invención) |

Formato chat limpio (botones inline, sin Markdown). Las respuestas respetan
reglas: no diagnostica, deriva al farmacéutico, aclara cuándo se necesita receta.

---

## 3. Arquitectura

**Capa de canal abstraída** — el cerebro es agnóstico del canal:

```
Telegram / WhatsApp ──► adaptador ──► CEREBRO ──► respuesta ──► adaptador ──► canal
                                         │
       flujo.py (nodos del menú) ────────┤
       acciones.py (consulta data) ──────┤
       ia.py (Claude + visión + tool) ───┤
       info.py (ficha de la farmacia) ───┘
```

| Archivo (`bot/`) | Rol |
|---|---|
| `flujo.py` | Nodos del menú (Fase 0 a mano; Fase 1 a DB + UI) |
| `acciones.py` | Acciones que tocan la data |
| `data.py` | Búsqueda de productos (compartida) |
| `ia.py` | IA conversacional + lectura de recetas (Claude) |
| `info.py` | Ficha de la farmacia (lo único que el bot afirma) |
| `cerebro.py` | Router + estado de conversación |
| `telegram_bot.py` | Adaptador Telegram (long polling) |

Pasar a WhatsApp = reemplazar solo el adaptador; el cerebro no cambia.

---

## 4. Handoff y panel de operadores ✅ (Fase 1 hecha)

Es el componente que reemplaza el core de Trii (la bandeja de agentes).
Ya está implementado: `routes/atencion.py` + `templates/atencion.html` +
`bot/store.py` (estado en DB) + `bot/canales.py` (envío saliente) + rol
**operador** en `routes/auth_routes.py`. Ruta: **`/atencion`**.

**Modelo de handoff** — cada conversación tiene un estado de atención:

```
   BOT atiende ──(cliente pide humano / el bot deriva)──► EN COLA
                                                            │
                                          operador la "toma" ▼
                                                        OPERADOR Juan
                                          (el bot se calla en ese chat)
                                                            │
                                              cierra / devuelve ▼
                                                        BOT otra vez
```

Mientras un operador tiene la conversación, **el bot no responde** ahí: los
mensajes del cliente quedan para el operador, que responde a mano desde el panel.

**Panel de atención** (pantalla web en AppFarmWeb):
- Se accede desde **cualquier PC con navegador**: URL de la app → login con
  usuario (rol **operador**) → bandeja. **Cero instalación.**
- Varias PC = varios operadores logueados sobre la **misma bandeja** (DB
  compartida). Uno toma, los demás lo ven tomado.
- Tiempo real por **polling** (3-5 s) al inicio; WebSocket/SSE después si hace falta.
- Lo que el operador escribe se envía al cliente por el canal (Telegram/WhatsApp).

```
┌─ Panel de atención ──────────────────────────────────┐
│ Filtrar: [Todas ▾] [Ventas] [Recetas] [Turnos]       │
│ PENDIENTES (3)        │  Conversación con +54 9 341... │
│ 🟢Ventas · Juan       │  cliente: tienen ibuprofeno?   │
│ 🔵Recetas · María 📸  │  bot: sí, $3.000 en stock      │
│ 🟡Turnos · +54 341..  │  cliente: quiero hablar c/algn │
│ MÍAS (1) · Pedro      │  [TOMAR]  > tu respuesta… [↵]  │
└───────────────────────┴────────────────────────────────┘
```

---

## 5. Multi-línea (varios números de entrada)

Omnicanal: cada número/línea es un canal de entrada; **todas caen en una bandeja**.

```
📱 WhatsApp Ventas    ─┐
📱 WhatsApp Recetas   ─┼─► UN webhook ─► TU APP ─► UNA bandeja (etiquetada por línea)
📱 WhatsApp Turnos    ─┤    (cada mensaje trae        + filtro por número
📱 Telegram (pruebas) ─┘     por qué línea entró)
```

- En **WhatsApp Cloud API**, una app de Meta maneja varios números (cada uno un
  `phone_number_id`), todos al mismo webhook. Cada mensaje trae por qué número entró.
- Cada conversación se guarda **etiquetada con su línea**; el panel filtra por número.
- Se puede dividir el trabajo: un operador mira "recetas", otro "ventas".
- **Requisito**: el modelo de conversaciones lleva un campo "canal/línea" desde el día 1.

---

## 6. Restricciones de Meta (importante / legal)

Política oficial de WhatsApp Business:

| Surface | Medicamentos |
|---|---|
| **Commerce** (catálogo, pagos nativos) | ❌ Prohibido todo lo farmacéutico |
| **Messaging** (bot conversacional) | ✅ Excepción solo para **OTC** (venta libre), cumpliendo ley local |
| **Recetados** | ❌ No se puede facilitar la venta |

**Reglas de diseño que cumplimos:**
- El bot informa/consulta y **toma pedidos como conversación**; el **pago y la
  entrega son fuera de WhatsApp** (pasás a buscarlo). Nunca catálogo/pago nativo.
- Recetados: solo info + derivar; la dispensación legal requiere **receta física**.
- Foto de receta: se recibe para **coordinar/preparar** (no para vender por el canal).
- Zona gris (precio/pedido de OTC por mensajería): tolerada en la práctica
  mientras no se use Commerce y la transacción cierre fuera de WhatsApp.

---

## 7. Plataformas / stack

| Pieza | Elegido |
|---|---|
| Canal WhatsApp | **Meta WhatsApp Cloud API** (oficial; número dedicado + verificación de negocio) |
| Canal de prueba | **Telegram** (gratis, sin trámites, para prototipar) |
| IA | **Claude API** (Sonnet 4.6) — conversación, tool use, visión para recetas |
| Hosting | **Render** (el webhook vive en la app Flask) |
| Estado / conversaciones | **Postgres** (la misma DB de la app) |
| n8n | **No** — la lógica vive mejor en Flask, que ya tiene la data y Claude |

---

## 8. Plan por fases

| Fase | Qué | Dónde |
|---|---|---|
| **0 — Prototipo** ✅ | Bot en Telegram con menú + IA + data + recetas + ficha | Local |
| **1 — Handoff + panel** ✅ | Conversaciones en DB + panel `/atencion` + multi-línea + rol operador | App (local; falta subir a Render) |
| **2 — WhatsApp** | Conseguir número + verificación Meta + adaptador WhatsApp Cloud API | Render |
| **3 — UI de flujos** | Editor para que el equipo edite el menú sin código | App |
| **4 — Migración** | Bajar Trii, subir esto al número principal | Producción |

---

## 9. Pendientes / próximos

- **Ficha real de Badia** (hoy hay datos de PRUEBA en `bot/info.py`): horarios,
  servicios (inyecciones, etc.), obras sociales, formas de pago, delivery.
- **Hacer pedido** (toma producto + cantidad + datos → módulo de pedidos).
- **Mejorar búsqueda** (por síntoma/droga, no solo nombre literal).
- **Regenerar el token** de Telegram de prueba (quedó expuesto en chat).
- **Subir a Render** lo de Fase 1 (hoy corre local): commit del módulo `bot/` +
  panel `/atencion`, y definir cómo corre el proceso de Telegram en Render
  (worker aparte vs. dejar Telegram solo para dev y arrancar WhatsApp por webhook).

---

## 10. WhatsApp: ¿Twilio o Cloud API directo?

**No hace falta Twilio.** Para WhatsApp hay dos caminos sobre la **misma** API
oficial de Meta (WhatsApp Business Platform):

| | **Cloud API (directo)** ← elegido | **BSP** (Twilio, 360dialog, Gupshup…) |
|---|---|---|
| Qué es | Te conectás directo al webhook de Meta | Un revendedor que envuelve la API de Meta |
| Costo | Tarifa de Meta, sin más | Tarifa de Meta **+ markup por mensaje** del BSP |
| Infra | El webhook vive en nuestra app Flask (Render) | La maneja el BSP |
| Control | Total (un Meta app, varios números, un webhook) | Atado al panel/SDK del BSP |
| Onboarding | Lo hacés vos en Meta Business (más trámite inicial) | El BSP te simplifica la verificación |

**Por qué Cloud API directo para nosotros:**
- Ya tenemos servidor propio (Flask) y el bot está con **capa de canal abstraída**
  → WhatsApp es solo escribir `bot/whatsapp_bot.py` (otro adaptador). El cerebro
  no cambia.
- Las conversaciones que **inicia el cliente** (el 95% en una farmacia: te escriben
  preguntando) entran en la ventana de servicio de 24 h → tier gratuito/barato de Meta.
- No pagamos markup de un intermediario para siempre.

**Twilio convendría solo si:** no quisiéramos lidiar con la verificación de Meta
nosotros mismos, o si necesitáramos unificar varios canales (SMS + WhatsApp) bajo
una sola API. No es el caso.

> Si algún día se elige Twilio igual, el cambio es solo el adaptador: su webhook
> tiene otro formato (form-encoded en vez del JSON de Cloud API), pero el cerebro
> y el panel siguen igual.

---

## 11. Alta en WhatsApp Cloud API — paso a paso (cuando tengas el número)

Requisito previo: un **número de celular nuevo**, dedicado al bot, que **no esté
registrado en WhatsApp** (ni el personal ni el que usa Trii). Puede ser un chip
nuevo o un número virtual que reciba SMS/llamada para el código.

1. **Meta Business** — crear/usar una cuenta en [business.facebook.com](https://business.facebook.com).
   Cargar datos del negocio (Farmacia Badia, CUIT, dirección). La **verificación
   de negocio** puede tardar días → arrancar este trámite con tiempo.
2. **App de Meta** — en [developers.facebook.com](https://developers.facebook.com)
   → crear app tipo *Business* → agregar el producto **WhatsApp**.
3. **Número** — en el panel de WhatsApp de la app, *Add phone number* → cargar el
   número nuevo → verificar con el código SMS/llamada. Queda con un
   **`phone_number_id`** (lo usa la API para enviar).
4. **Token** — generar un **token de sistema permanente** (System User en Business
   Settings, con permisos `whatsapp_business_messaging` + `whatsapp_business_management`).
   El token temporal de 24 h es solo para probar.
5. **Webhook** — en *Configuration* → Callback URL = `https://<nuestra-app>/wa/webhook`
   y un **Verify Token** inventado por nosotros. Suscribir el campo `messages`.
   (Meta hace un GET de verificación con `hub.challenge` → el endpoint lo devuelve.)
6. **Variables de entorno** (Render + docker-compose), análogas a `TELEGRAM_BOT_TOKEN`:
   - `WHATSAPP_TOKEN` — token permanente.
   - `WHATSAPP_PHONE_NUMBER_ID` — el del paso 3 (uno por línea/número).
   - `WHATSAPP_VERIFY_TOKEN` — el del paso 5.
7. **Adaptador** — `bot/whatsapp_bot.py`:
   - `GET /wa/webhook` → responde `hub.challenge` si `hub.verify_token` coincide.
   - `POST /wa/webhook` → parsea el JSON de Meta, extrae texto/imagen + `wa_id` del
     cliente + `phone_number_id` (= línea), llama `cerebro.procesar('whatsapp', wa_id,
     …, linea=<nombre de la línea>)` y envía la respuesta con
     `bot.canales.enviar('whatsapp', wa_id, texto)`.
   - Agregar el envío real en `bot/canales.py` (`_enviar_whatsapp`): POST a
     `https://graph.facebook.com/v21.0/<phone_number_id>/messages`.
   - A diferencia de Telegram, **no es long polling**: Meta hace push al webhook.
     Por eso WhatsApp no necesita un proceso aparte como `telegram_bot.py`; vive
     dentro de la app Flask (Render).
8. **Restricciones** — recordar la sección 6: nada de catálogo/pago nativo de
   medicamentos; la foto de receta sirve para **coordinar/preparar**, no vender;
   la transacción cierra fuera de WhatsApp.

> Multi-línea: varios `phone_number_id` apuntan al **mismo** webhook; el payload
> trae cuál fue → se mapea a la "línea" que ya filtra el panel de atención.
