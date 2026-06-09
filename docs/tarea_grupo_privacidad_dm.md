# Tarea para Cline — Grupo solo domicilio · ficha completa por DM privado

## Objetivo
Hoy al publicar un pedido al grupo de cadetes se manda **TODO** (nombre, dirección,
mapa, **qué medicamento**, importe, **con receta**) → fuga de datos sensibles a todo
el grupo. Cambiar a:

1. **Grupo:** publicar **solo el domicilio** (+ N° de pedido y prioridad/turno). Nada
   más: sin nombre, sin teléfono, sin producto, sin receta, sin importe, sin link de mapa.
2. **DM privado al que toma:** cuando un cadete responde "Tomo", recibe la **ficha
   completa** por mensaje directo (privado), con todo lo necesario para repartir.

> Alcance: SOLO este cambio (grupo mínimo + ficha por DM al tomar). Los estados por
> DM (salí/entregado/rebote/efectivo) son un paso posterior, NO entran acá.

## Estado actual (para ubicarse)
- `routes/reparto.py::reparto_pedido_publicar` (`POST /reparto/pedido/<pid>/publicar`)
  arma el texto rico y lo publica al grupo con `whatsapp_grupo.publicar_en_grupo(texto)`,
  guardando `p.waha_msg_id` para matchear el reply.
- `routes/reparto.py::reparto_whatsapp_grupo_webhook` recibe el "Tomo" (reply citado),
  matchea el pedido por `waha_msg_id`, setea `p.tomado_por_wsap` / `p.tomado_en` /
  `p.cadete_id` y confirma en el grupo "✅ Pedido #X tomado por *Nombre*".
- `bot/whatsapp_grupo.py::publicar_en_grupo(texto)` → `POST WAHA/api/sendText` al
  grupo (`WAHA_GRUPO_ENVIOS`).

## Qué hacer

### 1. `bot/whatsapp_grupo.py` — poder mandar DM + separar los 2 textos
- Agregar **`enviar_dm(wa_id, texto)`**: igual que `publicar_en_grupo` pero con
  `chatId = wa_id` (número personal, formato `<numero>@c.us`). Mismo `sendText`,
  mismos headers/session. Devuelve `{ok, error?}`. NO necesita matchear reply.
- Mover el armado de texto a 2 helpers (o dejar el rico en routes; lo importante es
  tener ambos):
  - **`texto_grupo(p)`** → mínimo: `🚚 *Pedido #{p.id}*` + `📍 {p.direccion}` +
    (si `p.prioridad=='urgente'` → `🔴 URGENTE`, si `p.turno` → el turno). **Nada sensible.**
  - **`texto_ficha(p)`** → completo: lo que hoy arma `reparto_pedido_publicar`
    (👤 nombre, ☎️ teléfono si lo hay, 📍 dirección + piso/depto, 🗺️ link de maps,
    💊 producto, 📋 con receta, 💰 importe/forma_pago/vuelto, 🛵 envío, 📝 observación).

### 2. `routes/reparto.py::reparto_pedido_publicar` — publicar mínimo
- Reemplazar el armado actual por **`whatsapp_grupo.texto_grupo(p)`** (solo domicilio).
- Seguir guardando `p.waha_msg_id` del mensaje publicado (el match del "Tomo" no cambia).

### 3. `routes/reparto.py::reparto_whatsapp_grupo_webhook` — DM al que toma
Después de asignar el pedido (tras el `s.commit()` que setea `tomado_por_wsap`):
- Extraer el **wa_id del que tomó** (el participante del grupo, NO `from` que es el grupo):
  ```python
  participante = (msg.get('participant') or msg.get('author')
                  or (msg.get('_data') or {}).get('author') or '')
  ```
  Normalizar a `<numero>@c.us` si viene sin sufijo.
- Si hay `participante`: `whatsapp_grupo.enviar_dm(participante, whatsapp_grupo.texto_ficha(p))`.
- **Fallback** si el DM falla o no hay participante: publicar en el grupo un aviso
  **sin datos sensibles**, ej. `📩 {push_name}, no pude mandarte el detalle por
  privado. Abrí tu link: {URL}/reparto/cadete/{token}` (usar el `token` del cadete
  matcheado si existe; si no, un genérico "pedí el detalle a la farmacia").
- La confirmación en el grupo sigue siendo `✅ Pedido #X tomado por *Nombre*` (sin datos).

## Aceptación
- Publicás un pedido → en el grupo aparece **solo** `🚚 Pedido #N — 📍 <dirección>`
  (+ urgente/turno). Ningún dato del cliente/producto/receta/pago.
- Un cadete responde "Tomo" citando → el grupo muestra "✅ tomado por X" **y** ese
  cadete recibe por **privado** la ficha completa.
- Si el DM no se pudo entregar → aviso en el grupo con el link, sin filtrar datos.

## NO hacer
- No tocar el match del "Tomo" por `waha_msg_id` (funciona).
- No agregar todavía los estados por DM (salí/entregado/etc.) — es otro paso.
- No mandar NADA sensible al grupo (ni link de maps, que revela el domicilio exacto:
  el maps va solo en el DM).
- `ruff check .` limpio.

## Archivos a tocar
- `bot/whatsapp_grupo.py` — `enviar_dm`, `texto_grupo`, `texto_ficha`.
- `routes/reparto.py` — `reparto_pedido_publicar` (texto mínimo) +
  `reparto_whatsapp_grupo_webhook` (DM al participante + fallback).

## Test (si entra fácil)
- `test_texto_grupo_no_filtra` — `texto_grupo(p)` con un pedido que tiene nombre/
  producto/receta → el string resultante **no** contiene nombre ni producto ni
  "receta"; sí contiene la dirección.
