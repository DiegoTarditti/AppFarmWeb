# Fase A — "Cerrar transacción" en /atencion (task)

> Spec autocontenido. Es el **seam** entre el chat/operador (AppFarmWeb) y el resto
> (ObServer + caja + reparto). Detalle de arquitectura en
> [`flujo_reparto.md`](flujo_reparto.md); acá va la pantalla concreta y su backend.

## Objetivo
En `/atencion`, cuando el operador termina de definir la compra, una pantalla
**"Cerrar transacción"** que captura **pago + destino + OS/receta + total** y manda
a caja. Reemplaza/extiende el botón actual que solo "confirma pedido → cola de caja".

## Contexto (lo que YA existe — reusar, no reinventar)
- `routes/atencion.py::atencion_crear_ticket` (`POST /atencion/<conv_id>/ticket`) →
  hoy hace `caja.crear_ticket(conv_id, items, user_id, cliente_nombre)`. **Este es el
  punto a extender.**
- **cliente_picker ya armado**: ficha (`/atencion/api/<id>/cliente`), buscar/vincular/
  crear cliente, domicilios (`/atencion/api/<id>/domicilios`, `/atencion/<id>/domicilio`),
  ciudades. La dirección validada (con coords) ya está disponible.
- Cotización de envío: `bot/envio.py` (`cotizar_por_coords`, `cotizar_por_direccion`) +
  config en `/config/envio`.

## Principio
**La venta (productos) vive en ObServer.** El operador la carga allá y trae el
**total a mano**. AppFarmWeb NO arma carrito — captura pago/destino/cobertura y la
logística. (`flujo_reparto.md §0`, decisión #1 = re-registro manual.)

## La pantalla "Cerrar transacción"
Panel (modal o columna) sobre la conversación tomada:

```
┌─ Cerrar transacción ───────────────────────────────┐
│ Total (de ObServer):  [ $ ______ ]   ← manual       │
│                                                      │
│ Forma de pago: [ dropdown ▾ ]  → dispara acción (↓) │
│ Obra social:   [ ____ (opcional) ]                  │
│ Receta:        ○ No  ○ Pendiente  ○ Recibida        │
│                                                      │
│ ¿Stock?        ○ Sí   ○ Esperar droguería [drog ▾]  │
│ ¿Sale por?     ○ Reparto   ○ Retiro                 │
│   └ si Reparto → Envío: [ $ __ ] (auto, editable)   │
│                                                      │
│ Prioridad: ○ Normal ○ Alta ○ Urgente               │
│                                                      │
│            [ Cerrar y mandar a caja ]               │
└──────────────────────────────────────────────────────┘
```

### Forma de pago — dropdown inteligente (al elegir, dispara)
| Opción | Acción | Captura |
|---|---|---|
| **Link MP** | muestra campo para pegar el link generado → botón "pegar link al chat" (lo manda al cliente) → después campo para el **nro de operación** MP | `link_mp`, `dato_pago_mp` |
| **Transferencia** | botón "mandar alias" (lo pega al chat) → campo para el comprobante/nro | `dato_pago_mp` |
| **Efectivo** | campo "¿con cuánto paga?" → calcula y muestra **vuelto** = paga_con − total | `paga_con`, `vuelto` |
| **Tarjeta crédito** | campos: últimos 4 · nombre · marca (Visa/Master/Amex). **Cero datos sensibles.** | `tarjeta_ult4`, `tarjeta_nombre`, `tarjeta_marca` |

### Destino — 2 ejes ortogonales
- **¿Stock?** `Sí` | `Esperar droguería` (+ dropdown `drogueria_id` = `Provider` tipo=drogueria).
- **¿Sale por?** `Reparto` | `Retiro`.
- Si **Reparto** → mostrar/cotizar **envío automático** desde el domicilio validado
  (reusar `bot/envio.cotizar_por_coords`); editable por el operador.
- Combinaciones → ver `flujo_reparto.md §5`. `destino` debe quedar **editable después**
  (mutable) — ver endpoint PATCH abajo.

### Cobertura
- `obra_social` (texto/dropdown, opcional). Si tiene cobertura → el total puede ser
  solo envío (lo decide el operador con el total que trae de ObServer).
- `requiere_receta`: `no | pendiente | recibida`. `requiere_firma` (bool, ej. PAMI).

## Backend
- **Persistencia**: estos campos van en el pedido (`flujo_reparto.md §3`).
  ⚠️ **Coordinar con Cline**: él está agregando columnas a `PedidoReparto`. **Verificar
  primero qué columnas ya existen** (cuando su rama esté pusheada) y agregar solo las
  que falten, con `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` (patrón del repo). NO
  duplicar columnas ni inventar una tabla nueva si ya hay uno.
- **Endpoint**: extender `POST /atencion/<conv_id>/ticket` (o `/atencion/<conv_id>/cerrar-transaccion`)
  para recibir el body completo `{total, forma_pago, link_mp, dato_pago_mp, paga_con,
  vuelto, tarjeta_*, obra_social, requiere_receta, requiere_firma, stock,
  drogueria_id, destino, envio_costo, prioridad}` → crea el pedido + manda a caja
  (`caja.crear_ticket` recibe estos datos).
- **Endpoint nuevo** `PATCH /api/pedido/<id>` `{destino, ...}` para cambiar destino
  después (mutable) — con auditoría (quién/cuándo). (Puede ser de la Fase B/C; dejar
  el campo editable es lo mínimo de Fase A.)

## NO hacer
- NO armar carrito de productos (eso es ObServer).
- NO duplicar columnas que Cline ya haya agregado a `PedidoReparto` (coordinar).
- NO guardar datos sensibles de tarjeta (solo últimos 4 + nombre + marca).
- NO romper el `atencion_crear_ticket` actual (extenderlo con campos opcionales,
  default al comportamiento de hoy si no vienen).
- `ruff check .` limpio + tests.

## Aceptación
1. Operador en una conversación tomada → abre "Cerrar transacción", tipea el total,
   elige forma de pago (y el dropdown dispara la acción correcta), destino, OS/receta.
2. "Cerrar y mandar a caja" → crea el pedido con todos los campos + entra a la cola
   de caja (y a la planilla si destino=reparto + stock=sí).
3. El vuelto se calcula bien (efvo); el envío se cotiza solo (reparto).
4. El cajero ve la transacción con lo que necesita (sin el vuelto). Tests + ruff.

## Seam (quién hace qué)
- **AppFarmWeb /atencion (esta task)**: la pantalla + captura + el endpoint de cierre.
- **Cline**: columnas del pedido (coordinar), caja/despacho, planilla, cadete, cta cte.
