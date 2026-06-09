# Tarea para Cline — (A) sacar cadete de /pedido/nuevo · (B) zonas de /envio por GeoJSON

Dos cambios en el módulo de reparto/envío. Independientes entre sí.

---

## A) `/pedido/nuevo` — NO pedir cadete en esta instancia

**Por qué:** el cadete se asigna **después**, no al cargar el pedido: o por la
planilla, o automáticamente cuando un cadete responde "tomo" en el grupo de
WhatsApp (ver `routes/reparto.py::reparto_whatsapp_grupo_webhook` +
`bot/whatsapp_grupo.py`). Pedirlo al crear el pedido sobra y confunde.

**Qué hacer — `templates/pedido_nuevo.html`:**
- Quitar el bloque del campo **Cadete**: `<label>Cadete</label>` + `<select id="pCadete">`
  (~líneas 188-189).
- Quitar el JS que puebla el select de cadetes (~512-514) y el reset (~736).
- Quitar `cadete_id` del body del POST a `/reparto/pedido` (~716).

**Backend — `routes/reparto.py::reparto_crear_pedido` (`POST /reparto/pedido`):**
- Que **siga aceptando** `cadete_id` opcional (no romper otros callers), pero que
  el default sea `None`. El pedido nace **sin cadete** (`estado='pendiente'`,
  `cadete_id=None`); se asigna luego por planilla o por el "tomo" del grupo.

**Aceptación:** /pedido/nuevo no muestra el selector de cadete; se crea el pedido
sin cadete y aparece "sin asignar" en la planilla/grupo, lista para que alguien la tome.

---

## B) `/envio` — definir zonas con GeoJSON (polígono), no con pin

**Estado actual:** en `/envio` las "Zonas con tarifa fija" se definen con un **pin**
(`EnvioZona.lat/lng/radio_km` = círculo geográfico, hoy NULL / "Fase 2"). Diego
quiere lo mismo que ya funciona en **`/rutas`**: zonas por **polígono** (point-in-polygon).

**Reusar lo de `/rutas` (NO reinventar):**
- `services/reparto.parse_poligono(texto)` — parsea esquinas pegadas de Google Maps → JSON.
- `services/reparto._punto_en_poligono(lat, lng, poly)` — point-in-polygon.
- Modelo `RutaReparto.poligono` (`Text`, JSON) + UI de `templates/rutas.html` como referencia.

**Qué hacer:**

1. **Modelo** `database.py::EnvioZona`: agregar `poligono` `Text` nullable
   (migración inline en `init_db`: `ALTER TABLE envio_zonas ADD COLUMN IF NOT
   EXISTS poligono TEXT`). `lat/lng/radio_km` quedan deprecados (no borrar para
   compat; dejar de usarlos en la detección).

2. **Guardado/listado** (`routes/envio.py`, endpoints de zonas):
   - Aceptar `poligono_texto` (esquinas de Google Maps) → `reparto.parse_poligono`
     → `EnvioZona.poligono = json.dumps(parsed)`. Igual patrón que
     `routes/reparto.py::rutas_guardar`.
   - Devolver `poligono` (parseado) en la API de zonas para dibujarlo en el mapa.

3. **Detección de zona** (en `bot/envio.py`, donde se resuelve la tarifa por zona
   desde la dirección del cliente): usar `reparto._punto_en_poligono(lat, lng,
   poligono)` contra cada `EnvioZona` con polígono — **reemplaza** la lógica de
   pin/`radio_km`. La detección por **nombre** en la dirección (centro, refinería…)
   puede quedar como complemento, pero la geográfica ahora es por polígono.
   - La zona nombrada **sigue pisando** a los tramos por distancia (mantener esa regla).

4. **UI `templates/envio.html`**, sección Zonas:
   - Quitar la columna **"Pin 📍"** y su flujo.
   - Sumar el mismo flujo de `/rutas` para **definir el polígono**: pegar las
     esquinas desde Google Maps (textarea → `parse_poligono`) y/o dibujar en un
     mapa Leaflet, mostrando el polígono. Reusar el patrón de `templates/rutas.html`.
   - Cada zona: nombre · monto · **[definir/editar polígono]** · activa.

**Aceptación:**
- En `/envio` se define una zona dibujando/pegando un polígono (como en /rutas), no un pin.
- Un cliente cuyas coords caen dentro del polígono de una zona cobra esa **tarifa fija**
  (y pisa los tramos por distancia).
- Coords fuera de toda zona → cae a la tarifa por cuadras (tramos), como hoy.

**Tests (`tests/test_envio.py`):**
- `test_envio_zona_poligono_detecta` — punto dentro del polígono → zona + tarifa fija.
- `test_envio_zona_poligono_fuera` — punto fuera → cae a tramos por distancia.
- `test_envio_zona_pisa_tramos` — dentro de zona nombrada pisa el tramo.

---

## NO hacer (ambas)
- No romper el flujo del grupo de WhatsApp (asignación por "tomo") ni la planilla.
- No borrar columnas existentes (deprecar `lat/lng/radio_km` de `EnvioZona`, no drop).
- Reusar `parse_poligono` / `_punto_en_poligono` de `services/reparto.py` (no duplicar).
- `ruff check .` limpio.

## Archivos a tocar
- A: `templates/pedido_nuevo.html`, `routes/reparto.py`.
- B: `database.py` (col + migración), `routes/envio.py`, `bot/envio.py`,
  `templates/envio.html`, `tests/test_envio.py`.
