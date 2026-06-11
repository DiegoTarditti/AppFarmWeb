# Próximos pasos — roadmap por horizonte

> Roadmap vivo del flujo venta → cobro → reparto. La arquitectura/detalle está en
> [`flujo_reparto.md`](flujo_reparto.md); acá va el **orden y la prioridad**.
> Convención: Claude orquesta/revisa · Cline ejecuta · se revisa contra el repo real.

---

## ✅ Qué se cerró (en el repo, verificado)
- **Filtro droguería multifarmacia** — config data-driven (`Config.farmacia_cuit` +
  campos en `Provider`), defaults por nombre de droguería, aviso guiado. (PR #191)
- **Perfiles de operador + home standalone** — registro `PERFILES`, gating unificado
  (1 guard reemplaza 5), `/home`, checks en `/usuarios`, `/rend-recetas` por `?perfil`. (PR #192)
- **Rediseño del bot** — Consultar Precio/Stock = 1 paso · Compra Farmacia guiada
  (stock→encargo→OS→receta→deriva) · Magistral aparte. (PR #193)
- **Docs maestros** — `flujo_reparto.md` (arquitectura) + este roadmap.

> ⚠️ Los ~7 commits que reportó **Cline** (proximos_pasos.md, refactor `/config/envio`,
> `/api/clientes/*`) **NO están en este repo** (ni local ni remoto). Hasta que Cline
> haga `git push`, no se pueden revisar ni mergear. Ver §📍 Notas.

---

## 🔴 Inmediato (5-10 min)
- [ ] **Mergear #194** (re-aplica 3 fixes perdidos: botón Editar, ocultar permisos
  para operador, quitar botón matriz del filtro). Después `git pull` y confirmar con
  `git log --graph` que `80209b0` quedó en el tronco.
- [ ] **Que Cline pushee su rama** a este remoto → recién ahí reviso /config/envio +
  /api/clientes + el doc de Cline.
- [ ] **Valores de PROD en el `.env` de la LAN** antes de salir en serio:
  `ATENCION_AUTO_BOT_MINUTOS` 30→180 · `ATENCION_REENGANCHE_MINUTOS` 1→5.
- [ ] **Regenerar el token de Telegram** (quedó expuesto en chats de desarrollo).
- [ ] **Reiniciar `bot`** para que tome el rediseño del menú (`docker-compose restart bot`).

## 🟡 Corto plazo (1-2 sesiones)
- [ ] **Verificar el namespacing de Cline** (`/config/envio` redirect 301, `/api/clientes/*`
  redirect 308) — confirmar que los POST sobreviven el redirect y la CI verde.
- [ ] **Fase A — Transacción en /atencion**: pantalla de cierre con `forma_pago`
  inteligente (link MP / alias transf / vuelto efvo / ult4 tarjeta) + destino (2 ejes:
  stock × salida) + OS/receta. Arma el pedido con los campos de `flujo_reparto.md §3`.
- [ ] **Migrar URLs viejas** que andan por redirect (`reparto.html`, `pedido_nuevo.html:234`,
  `tests/test_reparto.py`) a las nuevas — sacar la deuda.
- [ ] **Tests E2E** del flujo de compra del bot + del cierre de transacción.
- [ ] **Ficha real de Badia** en `bot/info.py` (hoy datos de prueba).

## 🟢 Medio plazo (Tier 2 — logística)
- [ ] **Campos DB nuevos en `PedidoReparto`** (tabla completa, ver `flujo_reparto.md §3`):
  pago (link_mp, dato_pago_mp, paga_con, vuelto, tarjeta_ult4…), cobertura
  (obra_social, requiere_receta, requiere_firma), logística (stock, pedido_a_drogueria_id,
  destino mutable, prioridad), timestamps por evento (`ts_*`).
- [ ] **Fase B — Caja + despacho**: vista filtrada del cajero, copiar nro op, marcar
  RETIRADO, recibir droguería (destrabar "Pedido a X").
- [ ] **Fase C — Planilla live**: timers/SLA (20'/40'), prioridad, colores, push.
- [ ] **State machine del cadete** (Fase D): TOMAR (inline grupo), chat 1:1 + feedback
  + link mobile.
- [ ] **Caja → contabilidad**: enganchar cobros con `flujo_fondos`/`cuentas`.

## 🔵 Largo plazo
- [ ] **Cuenta corriente del cadete** + liquidación (`cadete_cta_cte`, `flujo_reparto.md §9`).
- [ ] **Ticket térmico 80mm** (ESC/POS) del cadete (`§8`).
- [ ] **Grupo cadetes Telegram → WhatsApp** (Fase 2 WhatsApp: número dedicado, Cloud API).
- [ ] **Analytics + estimación de tiempos + incentivos** (`§12`).
- [ ] **Horarios / turnos** (mañana/tarde, orden en ruta).

---

## 📍 Notas operativas
- **Coordinación Cline**: para que YO pueda revisar, su trabajo tiene que estar en
  `C:\AppFarmWeb` (commiteado y, mejor, pusheado). Lo que no esté en el repo, no existe
  para la revisión. Regla: Cline termina → pushea → reviso.
- **Merge limpio**: no mergear una rama mientras se le sigue agregando; después de
  mergear, la rama queda congelada (fix posterior → rama nueva). Tras cada merge:
  `git log --graph --oneline --all` y verificar que no quede nada colgando.
- **DockerPanel**: app local tkinter (agente push + HTTP helper :5055) puente con Render.
- **AppLabo**: apunta a `farmacia-db.applabo` (schema separado, DB compartida).
- **Render gotchas**: nunca `--preload` en gunicorn; health check `/ping` (sin DB);
  plan free 512MB (cuidado con `Producto.all()` en pantallas que iteran ~30k filas).
- **ObServer manda**: la venta (productos + caja fiscal) es de ObServer; AppFarmWeb no
  duplica el carrito (ver `flujo_reparto.md §0`).
