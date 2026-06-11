# Flujo de venta → cobro → reparto (doc maestro)

> Doc de arquitectura del flujo completo: cliente → chat/operador → ObServer →
> caja → reparto → cadete. Es el mapa contra el que se revisa lo que se va
> construyendo (Claude orquesta/revisa, Cline ejecuta). **Vivo**: se actualiza
> con cada decisión.

## 0. Principio rector
**AppFarmWeb NO hace el carrito ni el checkout de productos.** La venta real
(selección de productos, precios, ticket fiscal) vive en **ObServer** (sistema
externo). AppFarmWeb capta la intención, **captura el pago y el destino**, y
maneja toda la **logística de entrega** (reparto, cadetes, cta cte).

---

## 1. Mapa de componentes

```
Bot Telegram ─→ /atencion ──→ [ObServer] ──→ /caja ──→ /reparto/planilla ─→ grupo cadetes ─→ chat 1:1 cadete
  (capta +       (operador     (venta real:   (cajero    (queue del día,      (pull "TOMAR",   (seguimiento +
   handoff)       cierra la     productos +    rol triple) timers/SLA,         datos mínimos)   feedback live)
                  transacción)  caja fiscal)              publica al grupo)
```

| Componente | Rol |
|---|---|
| **Bot Telegram** | Capta al cliente, lo deriva al operador. Deja el encargo (notas). |
| **/atencion** | El operador define la compra, **captura pago + destino + OS + receta** → arma la transacción. NO carga productos. |
| **ObServer** (externo) | La venta: productos, envío como ítem, **ticket fiscal**. El operador tipea ahí. |
| **/caja** | El **cajero = rol triple**: ① cobra/anula tickets · ② **despacha** al cadete (entrega paquete + vuelto + ticket térmico → marca RETIRADO) · ③ **recibe** la factura de droguería (destraba los "Pedido a X"). |
| **/pedido/nuevo** | Equivalente **manual** de /atencion (retira express, teléfono, wsap no integrado, mostrador). Mismo modelo de datos. |
| **/reparto/planilla** | Vista consolidada = **queue del día**. Timers/SLA, publica al grupo. Los de *retiro* van a bandeja aparte. |
| **Grupo cadetes** (Telegram → WhatsApp después) | Recibe 1 mensaje por pedido **solo con domicilio** (privacidad/anti-fraude). El cadete **TOMA** (pull). Override manual del supervisor. |
| **Chat 1:1 cadete** | Tras TOMAR, privado con detalle completo + link único mobile. Canal de **feedback** (Llegué/Entregado/Cobrado/Problema). |

---

## 2. Flujo end-to-end

```
1. Cliente → bot Telegram (o entra manual por /pedido/nuevo)
2. Bot deriva → operador en /atencion ve el encargo
3. Operador define la compra y la carga en OBSERVER (productos + envío)
4. ObServer devuelve el TOTAL → el operador se lo dice al cliente
5. /atencion captura el PAGO (dropdown forma_pago inteligente, ver §4)
6. Operador captura DESTINO (2 ejes: stock × salida, ver §5) + OS/receta (§6)
7. "Manda a caja" → el cajero ve la transacción filtrada
8. Cajero copia el nro de operación al campo TXT de ObServer → ticket fiscal
9. Si destino=reparto y hay stock → entra a la PLANILLA del día
10. Planilla publica al grupo → cadete TOMA → viene a la farmacia (RETIRADO)
    → entrega (ticket térmico + paquete + vuelto + receta/autorización)
11. Cadete reporta por su chat 1:1 → se refleja en /atencion + /caja + planilla
12. Cierre: cta cte del cadete se mueve según forma de pago (§9)
```

---

## 3. Modelo de datos del Pedido / Transacción

Campos que el flujo introduce (sobre `PedidoReparto` y/o tablas nuevas):

```
Identidad
  cliente_id / ficha           comprador ≠ destinatario ("para mi mamá")
  canal/origen                 bot_telegram | bot_whatsapp | mostrador | telefono | otro
Venta
  total_observer               (los productos viven en ObServer, acá solo el total)
Pago
  forma_pago                   link_mp | transferencia | efectivo | tarjeta | ...
  link_mp                      link generado (caso MP)
  dato_pago_mp                 nro de operación MP/transf (para conciliar + TXT fiscal)
  paga_con / vuelto            (caso efectivo)
  tarjeta_ult4 / nombre / marca  (caso tarjeta — SOLO últimos 4, cero PCI)
Cobertura
  obra_social                  NULL | PAMI | IOMA | ...
  total_paciente               lo que cobra acá (0 si OS cubre 100% + retiro)
  total_envio                  aparte, siempre se cobra si reparto
  requiere_receta              no | pendiente | recibida
  requiere_firma               bool (autorización extra, ej. PAMI)
Logística
  stock                        hay | esperar_drogueria
  pedido_a_drogueria_id        FK proveedores(tipo=drogueria), si stock=esperar
  destino                      reparto | retiro      ← MUTABLE durante todo el flujo
  prioridad                    normal | alta | urgente
  envio_costo / distancia_km   calculados al validar la dirección (§7)
Estado + timestamps
  estado                       disponible→tomado→retirado→en_camino→llegue→entregado/fallido
  ts_publicado / ts_tomado / ts_retirado / ts_salio / ts_llegue / ts_entregado / ts_fallido
  cadete_id
```

Tabla nueva **`cadete_cta_cte`**: `cadete_id · fecha · concepto · monto · signo · pedido_id` (§9).

---

## 4. Pago — dropdown `forma_pago` inteligente

Al elegir, dispara la acción correspondiente:

| Forma | Acción que dispara | Persiste |
|---|---|---|
| **Link MP** | Genera link → lo pega en el chat → cliente paga (offline) → operador consulta panel MP → pega el nro de operación | `link_mp`, `dato_pago_mp` |
| **Transferencia** | Manda el alias (botón rápido o auto al elegir) | `dato_pago_mp` (nro/comprobante) |
| **Efectivo** | Pregunta "¿con cuánto paga?" → calcula **vuelto** | `paga_con`, `vuelto` |
| **Tarjeta crédito** | Solo últimos 4 + nombre + marca (riesgoso, confirmación extra) | `tarjeta_ult4/nombre/marca` |

El `dato_pago_mp` se copia (botón "📋 copiar nro op") al **campo TXT de ObServer**,
que lo imprime en el **ticket fiscal**, y queda acá para **conciliar** contra el
extracto de MP más adelante.

---

## 5. Destino — 2 ejes ortogonales (NO un solo dropdown)

```
¿Tenemos stock?              ¿Por dónde sale?
  ☐ Sí                         ☐ Reparto
  ☐ Esperar ingreso droguería  ☐ Retiro
```

| Stock × Salida | Resultado |
|---|---|
| Sí + Reparto | sale hoy con cadete → **planilla** |
| Sí + Retiro | preparado, espera cliente → **bandeja "para retirar"** |
| Esperar + Reparto | "Pedido a [droguería]" → al ingresar la factura, entra a planilla |
| Esperar + Retiro | "Pedido a [droguería]" → al ingresar, queda para retiro |

- **`destino` es MUTABLE** toda la vida del pedido (retiro↔reparto). Endpoint
  `PATCH /api/pedido/<id>` + **auditoría** (quién, cuándo, motivo). Cambiar a
  reparto **agrega** envío al total (recotizar o tolerancia) y lo mete al mapa/
  planilla; cambiar a retiro lo saca y **notifica al cadete si ya salió**.
- **"Esperar droguería"** = estado `pedido_a` + `drogueria_id` (dropdown). Al
  ingresar la factura de esa droguería en AppFarmWeb → **auto-destrabar** los
  pedidos que esperaban ese producto (mejora futura). Bandeja "pendientes por
  droguería".

---

## 6. Cobertura / Obra Social (PAMI)

- Producto **sin cargo** al paciente (cubre la OS); solo se cobra **envío** si va
  por reparto. Si retira → total paciente = $0.
- Docs extra que lleva el cadete: 📋 **receta firmada**, 📝 **autorización** que el
  paciente firma al recibir, (troquel/etiqueta del envase).
- `requiere_receta`: **no** (venta libre) | **pendiente** (cadete la pide en la
  puerta) | **recibida** (ya la mandó por chat). El atributo "con receta" del
  producto suele venir de ObServer; la decisión operativa es manual del operador.

---

## 7. Cálculo de envío (engancha con `/config/envio`)

Al validar la dirección (cliente_picker, con coords):
```
1. distancia = haversine(farmacia, destino)
2. lee tarifas de /config/envio:
     ¿cae en zona con tarifa fija? → usa esa
     si no → tramo por cuadras (0-10, 10-20, 20+)
3. sugiere costo en pEnvio (operador puede override con pCuadras)
4. suma al total
```
Debe dispararse **automático** al elegir domicilio (hoy hay botón Cotizar manual).

---

## 8. Ticket térmico del cadete (80mm, ESC/POS, ~32 col)

Separado del ticket fiscal de ObServer — es el papelito operativo:
```
# 1234
⚠️ PEDIR RECETA            ← grande si requiere_receta=pendiente
💊 PAMI — cobrar SOLO ENVÍO ← si obra_social con cobertura
📝 FIRMAR AUTORIZACIÓN      ← si requiere_firma
Juan Pérez · 11-5555-1234
Av. Córdoba 3400, piso 2 dpto B, Banfield
Cobrar efvo: $2.500  (vuelto $400)   |  PAGADO (MP)
🌅 Mañana · orden 3
[QR para marcar entregado]
```

---

## 9. Cuenta corriente del cadete

**El envío entero es del cadete.** Se mueve **automático** según `forma_pago`:

| Caso | Movimiento |
|---|---|
| Efvo (cobra en mano) | **no acumula** (se queda el envío cash; devuelve solo productos) |
| MP / transferencia / PAMI | **+envío** a la cta cte (la farmacia le debe) |
| Liquidación | **−acumulado** → cuenta a 0 |

`cadete_cta_cte`: conceptos `envio_pendiente | envio_pami | envio_efvo(0) |
liquidacion | ajuste`. Vistas: por cadete (pendiente de liquidar) + resumen del
período (total a liquidar). Cero input extra del cadete: el sistema ya sabe la
forma de pago del pedido.

---

## 10. Planilla — timers / SLA / estados

```
disponible (publicado)
   ⏱ 20 min sin tomar → 🟡 warning   (urgentes: 10 min)
tomado (cadete clickeó TOMAR en el grupo)
   ⏱ 40 min sin venir a la farmacia → 🟡 warning
retirado (pasó por la farmacia: paquete + vuelto + ticket + receta/autorización)
en_camino → llegue → entregado / fallido
```
- Live (polling/WS), cuenta regresiva por pedido, color por threshold, push al
  supervisor en 🟡/🔴, beep opcional.
- Acciones del supervisor en warning: asignar manual, contactar cadete,
  despublicar/repostear.
- **Prioridad** `normal|alta|urgente`: bloques/badge en planilla, prefijo 🚨 en el
  grupo, timers más estrictos, escalado si no se atiende.

---

## 11. Grupo de cadetes + chat 1:1

- **Grupo** (Telegram primero, WhatsApp después): 1 mensaje por pedido con **solo
  el domicilio** + botón inline **[TOMAR]** (atómico, primero gana; "ya lo tomó
  @X"). Override manual del supervisor.
- **Chat 1:1** tras TOMAR: bot manda detalle completo (privado) + **link único con
  token** a la pantalla mobile del pedido (se actualiza si algo cambia, a
  diferencia del mensaje estático). Un solo chat por cadete; los pedidos son
  hilos dentro del chat.
- **Feedback** del cadete (botones): 📍 Llegué · ✅ Entregado · ❌ No estaba · 💵
  Cobrado efvo $X · 📷 Foto · 💬 Problema → se refleja en vivo en /atencion + /caja
  + planilla. El operador puede intervenir.

---

## 12. Analytics / eficiencia / incentivos

Persistir los `ts_*` por evento ⇒ métricas por cadete/período: tiempo en tomar,
en venir, en camino, en puerta, total, entregas/turno, % on-time, % fallidos, km,
recaudación de envíos. **Tiempo estimado de entrega** v1: `distancia /
velocidad_promedio_del_cadete + buffer`. Sistema de **ranking/premio** (on-time,
volumen, sin fallidos).

---

## 13. Seams (dónde se tocan los frentes)

| Seam | Detalle | Quién |
|---|---|---|
| Bot → /atencion | el bot deja el encargo en notas; falta la **pantalla de cerrar transacción** (pago/destino/OS) | Claude (engancha con el bot) |
| /atencion ↔ ObServer | el operador tipea la venta en ObServer; el `dato_pago_mp` se copia al TXT fiscal (manual) | manual / integración futura |
| /caja → planilla | cobrar + destino=reparto + stock=hay → entra a la planilla | — |
| "Pedido a droguería" → planilla | al ingresar la factura de la droguería → destraba | mejora futura |
| Logística completa | planilla, grupo, chat cadete, ticket térmico, cta cte, analytics | Cline |

---

## 14. Plan por fases (borrador)

- **Fase A — Transacción en /atencion**: pantalla de cierre (forma_pago inteligente
  + destino 2 ejes + OS/receta) → arma el pedido con todos los campos de §3.
- **Fase B — Caja + despacho**: vista filtrada del cajero, copiar nro op, marcar
  RETIRADO, recibir droguería.
- **Fase C — Planilla live**: timers/SLA, prioridad, publicar al grupo.
- **Fase D — Grupo + chat cadete**: TOMAR (inline), chat 1:1 con feedback + link mobile.
- **Fase E — Ticket térmico** (ESC/POS 80mm).
- **Fase F — Cta cte cadete + liquidación**.
- **Fase G — Analytics + estimación de tiempos + incentivos**.

---

## 15. Decisiones abiertas

- ¿La venta de ObServer se **re-registra a mano** en /atencion, o hay/habrá
  **lectura/integración** que la trae? (define si el reparto arranca de cero o hereda).
- Cambio de destino que agrega envío: ¿se le pide la **diferencia** al cliente o la
  farmacia **absorbe** (tolerancia)?
- Ticket térmico: ¿cuándo imprime (al cobrar / al cerrar planilla / botón manual)?
- Caso "para mi mamá": confirmar el modelo **comprador ≠ destinatario** en el cliente_picker.
