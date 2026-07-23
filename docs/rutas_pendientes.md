# Rutas pendientes — flujo pedido + reparto

> Solo las **Fases B/C/D** planificadas el 2026-06-10. Fase A ya tiene su spec
> propia en [`fase_a_transaccion.md`](fase_a_transaccion.md). Arquitectura
> general en [`flujo_reparto.md`](flujo_reparto.md). Roadmap por horizonte en
> [`proximos_pasos.md`](proximos_pasos.md).

---

## 🅱 Fase B — Caja (rol triple) + despacho

Hoy `/caja` lista tickets cobrables. Tiene que ser el **cockpit del cajero**
con 3 funciones simultáneas en una pantalla:

```
┌─ /caja ──────────────────────────────────────────┐
│  🔔 Por cobrar     🚚 Cadetes      📦 Drogerías   │
│  ──────────────                                   │
│  (lista)        + panel lateral con notificaciones│
└──────────────────────────────────────────────────┘
```

### Rutas a programar

| Ruta | Método | Para qué |
|---|---|---|
| `/caja` | GET | Vista consolidada con 3 sub-bandejas |
| `/caja/<id>/marcar-cobrado` | POST | Cajero confirma cobro fiscal hecho en ObServer |
| `/caja/<id>/despachar` | POST | Cadete viene, retira paquete + vuelto + ticket térmico → marca RETIRADO |
| `/caja/recepcion-drogueria` | POST | Llega factura de droguería → destraba pedidos en estado "Pedido a X" |
| `/caja/retorno` | POST | Cadete vuelve: checklist (receta firmada, autorización, vuelto, troquel) |
| `/caja/api/notificaciones` | GET | Polling para el panel lateral (cadetes que llegan, etc.) |

### Archivos a tocar
- `routes/caja.py` (extender)
- `templates/caja.html` (rediseñar con tabs/sub-bandejas)
- `services/caja.py` (lógica de destrabar pedidos)

### Antes de codear
Spec corta `docs/fase_b_caja.md` con:
- Wireframe de las 3 bandejas
- Reglas de cuándo "destrabar" un pedido cuando llega factura de droguería
- Reglas de checklist retorno (cuáles items son obligatorios)

### Esfuerzo
5-7 h.

---

## 🅲 Fase C — Planilla live

Hoy `/reparto/planilla` es estática. Tiene que ser el **cockpit del día** con
timers/SLA + publish al grupo Telegram + update en vivo.

### Rutas a programar

| Ruta | Método | Para qué |
|---|---|---|
| `/reparto/planilla` | GET | Cockpit con timers/SLA/colores por prioridad |
| `/reparto/planilla/api/live` | GET | Polling cada 30s con cambios |
| `/reparto/pedido/<id>/publicar` | POST | Manda al grupo Telegram (mensaje con botón inline TOMAR) |
| `/reparto/pedido/<id>/asignar` | POST | Override manual (skip "tomar libre") |

### Subfeatures que faltan en la pantalla

| Feature | Decisión |
|---|---|
| Update live | Polling 30s (Render free no banca WebSocket) |
| Timer 20 min publicado sin TOMAR → 🟡 warning | parametrizable |
| Timer 40 min tomado sin retirar → 🟠 warning | parametrizable |
| Color por prioridad (normal=verde, alta=amarillo, urgente=rojo) | hardcoded |
| Banda urgentes arriba | sí |
| Botón "📢 Publicar" por pedido | con confirm modal |

### Archivos a tocar
- `routes/reparto.py` (extender planilla + API live)
- `templates/reparto_planilla.html` (rediseño)
- `bot/telegram_grupo.py` (**nuevo** — webhook + send)

### Antes de codear
Spec `docs/fase_c_planilla.md` con:
- Mockup de la planilla con timers/colores
- Texto exacto del mensaje al grupo Telegram (privacidad: SOLO domicilio + nº pedido)
- Anti-race del botón TOMAR (primero gana, atómico)

### Esfuerzo
6-10 h. Depende de Fase D (state machine).

---

## 🅳 Fase D — State machine del cadete

Transversal a Fases C y E (cta cte). Persiste el momento de cada transición
para que la planilla sepa los timers y las métricas tengan datos reales.

### Estados

```
pendiente → publicado → tomado → retirado → en_camino → llegue → entregado
                                                              ↘  fallido
```

### Rutas a programar

| Ruta | Método | Para qué |
|---|---|---|
| `/reparto/pedido/<id>/transicion` | POST | Cambio de estado con validación de orden |
| `/telegram/webhook/grupo` | POST | Recibe el TOMAR (asignación atómica) |
| `/telegram/webhook/cadete` | POST | Recibe feedback 1:1 del cadete |
| `/reparto/cadete/<token>` | GET | Mobile: pedidos + botones de transición |
| `/reparto/cadete/<token>/transicion` | POST | Versión cadete (autorización por token) |

### DB migration (lo más importante)

```sql
ALTER TABLE pedido_reparto ADD:
  ts_publicado timestamp,
  ts_tomado timestamp,
  ts_retirado timestamp,
  ts_salio timestamp,
  ts_llegue timestamp,
  ts_entregado timestamp,
  ts_fallido timestamp,
  fallido_motivo text;
```

### Archivos a tocar
- `database.py` + `alembic/versions/<nueva>.py`
- `routes/reparto.py` (transicion endpoints)
- `templates/vista_cadete.html` (mobile con botones rápidos)
- `bot/telegram_grupo.py` (webhooks)
- `services/reparto.py` (validación de transiciones)

### Esfuerzo
3-4 h (state machine + migración) + 4-6 h (cadete mobile completo) + 6-8 h (Telegram).

---

## 🧱 Modelo de datos — la migración Alembic

Una sola migración cubre B/C/D:

```sql
-- Para Fase A (si no se hizo antes en esa fase):
ALTER TABLE pedido_reparto ADD
  obra_social text,
  requiere_receta bool default false,
  requiere_firma bool default false,
  stock_status text default 'hay',
  pedido_a_drogueria_id int FK proveedores,
  total_paciente decimal(14,2),
  total_envio decimal(14,2),
  paga_con decimal(14,2),
  vuelto_calc decimal(14,2),
  link_mp text,
  dato_pago_mp text,
  nro_op_transfer text,
  tarjeta_ult4 char(4),                             -- NUNCA PAN
  tarjeta_marca text,
  tarjeta_titular text;

-- Para Fase D (state machine):
ALTER TABLE pedido_reparto ADD
  ts_publicado timestamp,
  ts_tomado timestamp,
  ts_retirado timestamp,
  ts_salio timestamp,
  ts_llegue timestamp,
  ts_entregado timestamp,
  ts_fallido timestamp,
  fallido_motivo text;

-- Para cta cte cadete (post-D):
CREATE TABLE cadete_cta_cte (
  id serial PK,
  cadete_id int FK cadetes,
  fecha timestamp not null,
  concepto text not null,
  monto decimal(14,2) not null,
  pedido_id int FK pedido_reparto nullable,
  signo char(1) not null default '+',
  notas text
);
```

Hacerla **una sola migración** evita versiones intermedias raras. Si Fase A
ya hizo la primera parte, Fase D solo agrega los `ts_*`.

---

## 📅 Orden y dependencias

```
Fase A (en curso)
  └─→ DB migration (Fase A + Fase D + cta cte)
        ├─→ Fase B (caja)        independiente
        ├─→ Fase D (state machine)
        │     ├─→ Fase C (planilla live, usa timers)
        │     ├─→ Telegram grupo cadetes
        │     ├─→ Cadete mobile completo
        │     └─→ Retorno cadete (Fase B + state machine)
        └─→ Cta cte cadete (Fase D + cierre pedido)
```

**Camino crítico**: A → migración → D → C → Telegram. Fase B se puede meter en
paralelo (no depende de D).

---

## 🔑 Decisiones cerradas (no abrir de nuevo)

| Tema | Decisión |
|---|---|
| Update live | Polling 30s (no WebSocket por ahora) |
| Privacidad mensaje grupo cadetes | SOLO domicilio + nº pedido |
| Forma_pago tarjeta | Solo últimos 4 + marca + titular |
| Mensajería | Telegram primero, WhatsApp después |
| Tabla de pedido | Una sola (`PedidoReparto`), no `Transaccion` aparte |

---

*Doc 2026-06-11. Empezar por la migración Alembic — destraba A, B, C, D
en paralelo.*
