# Próximos pasos — checkpoint sesión 2026-06-10

> Punch list corta de qué falta después de la sesión del 10/06.
> Para roadmap largo ver [`flujo_pedido_despacho.md`](flujo_pedido_despacho.md).
> Para backlog vivo completo ver [`mejoras_pendientes.md`](mejoras_pendientes.md).

---

## ✅ Qué se cerró hoy

7 commits (branch +7 ahead de origin):

```
f4100d1 refactor: migrar JS de /reparto a cliente_picker (opcion 3)
8dd3114 test: cobertura para los 8 endpoints /api/clientes/* (20 tests)
214e330 docs(backlog): marcar Alembic como hecho + limpiar comentario obsoleto
87b1051 docs(backlog): cerrar cliente_picker + pendientes /reparto y namespacing
f94d915 chore: borrar import 'redirect' huerfano de reparto.py
ed7c0fb refactor: borrar redirects 308 legacy /reparto/api/* → /api/clientes/*
f0eca4e refactor: cliente_picker + /api/clientes + /config/envio + deep-link
```

**Cambios visibles:**
- Componente `cliente_picker` reusable (macro Jinja + JS) — usado por
  `/pedido/nuevo` y `/reparto`.
- Endpoints `/api/clientes/*` con namespace propio (antes `/reparto/api/*`).
- `/envio` → `/config/envio` (redirect 301 para no romper bookmarks).
- Botón "📝 Pedido" en `/atencion` que abre `/pedido/nuevo?observer_id=X`
  con cliente precargado.

**Métricas:**
- `pedido_nuevo.html`: 787 → 366 líneas (-53%)
- `reparto.html`: 509 → 413 líneas (-19%)
- `routes/reparto.py`: -130 líneas
- 20 tests nuevos para `/api/clientes/*` (todos pasan)

---

## 🔴 Inmediato (próxima vez que abras la app)

### 1. Probar en browser (5 min)
- [ ] `/atencion` con conversación abierta + cliente vinculado → click "📝 Pedido"
      → debe abrir `/pedido/nuevo` con cliente, dirección, ciudad, domicilios precargados.
- [ ] `/reparto` → buscar cliente, "＋ nuevo cliente", "✏️ editar", agregar pedido — todo
      igual que antes (no debe haber regresión visible).
- [ ] `/pedido/nuevo` sin params → flujo normal de toma de pedido.
- [ ] `/envio` → debe redirigir a `/config/envio` (verificar en barra de URL).

### 2. Bajar a Render (cuando estés conforme)
- [ ] `git push` (rama main, +7 commits)
- [ ] Verificar deploy OK en Render (logs sin errores, /ping responde)
- [ ] Probar las mismas 4 cosas en producción

### 3. Cline pendiente
Cuando termine la task de tests para `/config/envio` que le tiré:
- [ ] Revisar el resultado (22 tests esperados, mismo patrón que test_clientes_api.py)
- [ ] Si pasan todos, `git add tests/test_envio_api.py && git commit`

---

## 🟡 Corto plazo (1-2 sesiones próximas)

### Mejoras al `cliente_picker` (cuando aparezca caso)
- **Namespacing multi-instancia** — solo si necesitás 2 buscadores en
  la misma pantalla. Ver entry en `mejoras_pendientes.md`.
- **Unificar visual entre `/pedido/nuevo` y `/reparto`** — hoy usan el
  mismo JS pero distinto HTML (grid vs inline). Las opciones 1 y 2 del
  backlog quedaron sin hacer. No urgente.

### Aplicar el deep-link cliente a otras pantallas
- **`/caja`** — al cobrar, mostrar/editar ficha del cliente via picker.
- **Otras** que aparezcan.

### Tests E2E del deep-link
- Hoy se prueba a mano (paso 1 de arriba). Si rompe algo, no avisa.
- Test sugerido: que `GET /pedido/nuevo?observer_id=X` renderiza el HTML
  con el script de precarga visible.

---

## 🟢 Medio plazo (Tier 2 — pedido completo)

Pendiente desde el análisis del 28/05 (ver
[`flujo_pedido_despacho.md`](flujo_pedido_despacho.md) sección "Próximos
pasos sugeridos / Tier 2").

### Campos nuevos en `pedido` + `pedido_reparto` (DB migration)

| Campo | Tabla | Tipo | Por qué |
|---|---|---|---|
| `obra_social` | pedido | text/FK | Para PAMI (solo cobra envío) y similares |
| `requiere_receta` | pedido | bool | Documento que el cadete debe traer |
| `requiere_firma` | pedido | bool | Autorización PAMI |
| `stock_status` | pedido | enum('hay','esperar_drogueria') | Eje ortogonal a destino |
| `pedido_a_drogueria_id` | pedido | FK proveedores | Si stock=esperar |
| `prioridad` | pedido | enum('normal','alta','urgente') | SLA + visualización |
| `total_paciente` | pedido | decimal | Lo que cobra al cliente |
| `total_envio` | pedido | decimal | Separado para liquidar al cadete |
| `paga_con` | pedido | decimal | Si efectivo |
| `vuelto` | pedido | decimal | Calculado |
| `link_mp` | pedido | text | Generado al elegir Link MP |
| `nro_op` | pedido | text | Pegado del panel MP por el operador |
| `ultimos_4` | pedido | text(4) | Tarjeta crédito (NUNCA PAN completo) |
| `marca_tarjeta` | pedido | text | Visa/Master/etc |

**Esfuerzo:** 1 sesión completa.
- Crear migración con Alembic (ya está adoptado, ver `alembic/versions/`).
- Sumar campos al modelo en `database.py`.
- Adaptar UI en `pedido_nuevo.html` (dropdown OS, prioridad, etc.).
- Adaptar payload del POST `/reparto/pedido`.

### Estado del pedido — state machine

Pendiente del análisis (`flujo_pedido_despacho.md` etapa 5):
```
disponible → tomado → retirado → en_camino → llegué → entregado/fallido
```

Con `ts_*` por transición. Permite:
- Timers escalonados en planilla (20 min sin tomar → warning, 40 min sin retirar).
- Métricas de cadete (tiempo promedio por etapa).
- Modelo predictivo simple (distancia / velocidad_cadete).

**Esfuerzo:** 1-2 sesiones.

---

## 🔵 Largo plazo (cuando haya demanda)

Pendientes del análisis del flujo operativo (`flujo_pedido_despacho.md`):

- **Cuenta corriente del cadete** (`cadete_cta_cte`) — envío entero al
  cadete, efvo en mano no acumula, online sí.
- **Ticket térmico 80mm** para el cadete (ESC/POS).
- **Publicación al grupo Telegram** con botón inline TOMAR + chat 1:1
  post-tomar.
- **Sistema de incentivos** con ranking semanal/mensual.
- **Checkpoint retorno** del cadete con checklist (receta, autorización,
  vuelto, troquel).
- **Horarios de operadores + routing por tipo de consulta**
  (`operador_horario` + `tipo_consulta` con keywords + backup +
  re-encolar).

---

## 📍 Notas operativas

### Lo que el bot helper local NO sabe
- `DockerPanel/` (ver CLAUDE.md sección "Arquitectura híbrida local ↔ Render").
  Si tocamos algo que afecte sync local/Render, verificar que el helper
  HTTP sigue funcionando.

### AppLabo (las 17 tablas extra en Badia DB)
- Existe `c:\appLabo`, comparte tablas (productos/medicos/clientes) con
  AppFarmWeb. Lee, no escribe. Si algún refactor toca esos modelos,
  verificar AppLabo en paralelo.

### Render — gotchas
- Healthcheck en `/ping` (sin DB) no `/health` (con SELECT 1).
- Plan free: 512MB. Cuidado con `Producto.all()` en pantallas grandes.
- NUNCA `--preload` al gunicorn (cuelga el master).
- Ver `docs/lecciones_deploy_render.md`.

---

*Doc generado 2026-06-10 al cierre de la sesión. Cuando vuelvas, abrí
este archivo y arrancás por la sección 🔴 Inmediato.*
