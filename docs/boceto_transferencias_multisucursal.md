# Boceto — Transferencias multi-sucursal (registro en tabla)

> Estado: **Fase 1 implementada** (branch `feat/transferencias-multisucursal`):
> tabla `sucursales`, service que lee el registro, UI con dropdown + etiquetas
> dinámicas, y pantalla admin `/sucursales`. **Fase 2 pendiente**: propagación
> automática vía `/compartido` (hoy se cargan las sucursales por instancia).
> El doc original de diseño queda abajo como referencia.
> Disparador: el modelo actual (local + `BADIA_DATABASE_URL`) está pensado para
> 2 sucursales y van a aparecer más. Cualquier nombre singular ("remota",
> "badia") miente apenas haya una tercera. El problema no es el nombre, es el
> modelo: hay que pasar de "local + la otra" a un **registro de N sucursales**.

## Objetivo

- Soportar **N sucursales**, cada una con su DB (obs_* + `codigo_alfabeta`).
- **Agregar una sucursal = una fila en una tabla**, no tocar código ni env de las demás.
- Etiquetas siempre correctas (nunca invertidas, nunca un nombre que miente).

## Modelo: tabla `sucursales`

Una tabla con las conexiones de cada sucursal, identificada por el nombre de la
app/DB de Render, guardando **ambas** URLs (interna y externa).

| Columna | Ejemplo | Para qué |
|---------|---------|----------|
| `slug` (PK) | `badia` / `pieri` | identificador estable |
| `nombre` | `Badia` / `Pieristei` | display en la UI |
| `app_name` | `farmacia-web` / `appfarmpieri` | servicio Render |
| `db_name` | `farmacia_yhvp` / `db_pieri` | base Render |
| `url_interna` | `postgresql://…@dpg-xxx-a/db` (sin dominio) | conexión **dentro** de Render (rápida, sin SSL, sin egress) |
| `url_externa` | `postgresql://…@dpg-xxx-a.oregon-postgres.render.com/db` | conexión desde afuera (local/dev) o cross-region (con SSL) |
| `activa` | `true` | apagar una sin borrarla |

### ¿Interna o externa? (decisión automática)
- Si la app corre **en Render** (Render setea `RENDER=true` / `RENDER_SERVICE_*`) → usa `url_interna` (rápida, gratis, sin SSL).
- Si corre **local** (dev) → usa `url_externa` (con SSL).
- El engine elige el `sslmode` según la URL (las `render.com` → require; las internas → sin SSL).

### ¿Cuál soy yo?
- Una sola env mínima: `SUCURSAL_LOCAL=badia` (slug de esta instancia), **o** detectar matcheando `db_name` contra `DATABASE_URL`.
- El resto de las filas activas = las sucursales remotas.

## ¿Dónde vive la tabla y cómo la ve cada instancia?

Dos opciones (decisión a tomar):

- **(A) Replicada en cada DB** — la tabla vive en la DB de cada sucursal. Se edita
  en una pantalla admin y se **propaga vía el hub de `/compartido`** (un tipo nuevo
  `'sucursales'`). En runtime cada instancia lee su **copia local** (`DATABASE_URL`)
  → sin dependencia de red, rápido. **Recomendada** — reusa infra que ya existe.
- **(B) Central en el hub** — una sola tabla en la DB del hub; cada instancia la
  pide por HTTP (`HUB_BASE_URL`, que Pieri ya tiene). Una sola fuente de verdad,
  pero depende del hub en runtime.

> Bootstrap: siempre hace falta UN dato para arrancar. En (A) es `DATABASE_URL`
> (que ya está). En (B) es `HUB_BASE_URL` + `HUB_TOKEN` (que Pieri ya tiene).

## Admin: pantalla `/sucursales`

CRUD simple (solo admin): slug, nombre, app_name, db_name, url_interna, url_externa, activa.
- Passwords **enmascaradas** en pantalla; solo admin.
- Botón "Propagar al grupo" (reusa `/compartido`) si vamos por (A).
- Reemplaza el setear URLs a mano en env de cada servicio.

## UI de /transferencias

- Mismo layout de 2 columnas, pero con **dropdown para elegir contra qué sucursal comparar**:
  > Comparar **Badia** (local) ↔ [ **Pieri** ▾ ]  · con 2 el dropdown tiene 1 opción; con N las lista
- Selección por query param: `/transferencias?otra=pieri`. Default = primera activa.
- **Encabezados y direcciones dinámicos** por nombre de sucursal → nunca se invierten ni mienten.

### Futuro (fuera de alcance)
- Vista **matriz N-way**: por producto, quién tiene excedente y quién necesita entre TODAS a la vez. Otro diseño de UI. Anotado.

## Cálculo

Sin cambios de fondo: misma lógica cobertura/excedente/necesita, generalizada a
"origen (excedente) → destino (necesita)" con nombres reales.

## Migración desde el estado actual

- Hoy: hardcode local="Pieri", `BADIA_DATABASE_URL`=remota.
- **PR #104 abierto** (`TRANSFER_LOCAL_ES` + remapeo por instancia) = parche 2-sucursales. Bajo el modelo de tabla queda **obsoleto** → conviene **cerrarlo** e ir directo al registro (salvo que quieras Badia↔Pieri andando YA como interino).
- Backcompat: si la tabla `sucursales` está vacía, caer a `DATABASE_URL` + `BADIA_DATABASE_URL` → nada se rompe en la transición.

## Seguridad

- Las URLs llevan password → guardarlas en tabla = credenciales en la DB (igual que hoy en env). Pantalla admin enmascara y restringe a admin.
- Si se propagan por `/compartido`, viajan por HTTP con token. Anotarlo. (Las DBs de sucursales hermanas son confiables.)
- Rotación: cambiar password en Render → actualizar la fila (un solo lugar) → propagar.

## Prerrequisito de datos (cualquier sucursal)

Su DB necesita el espejo `obs_*` sincronizado y `codigo_alfabeta` poblado (clave
del cruce). Verificado: Badia y Pieri tienen ~43.800 alfabetas cada una.

## Decisiones a confirmar

1. **Dónde vive la tabla**: (A) replicada por DB + propagación vía /compartido [recomendada], o (B) central en el hub vía HTTP.
2. **PR #104**: cerrar e ir al registro, o mergear como parche interino mientras se construye.
3. **Identidad de la instancia**: env `SUCURSAL_LOCAL`, o autodetectar por `db_name` vs `DATABASE_URL`.
4. **Interna/externa**: autodetectar por `RENDER` env, o flag explícito.

## Plan incremental (cuando se apruebe)

1. Modelo `Sucursal` + migración inline en `init_db()` (tabla `sucursales`).
2. `services/transferencias.py`: leer registro, elegir interna/externa, comparar local vs `otra`, con fallback a lo viejo.
3. `routes/transferencias.py` + `templates/transferencias.html`: dropdown de sucursal + encabezados/direcciones por nombre; export con nombres dinámicos.
4. Pantalla admin `/sucursales` (CRUD) + (si va por A) tipo `'sucursales'` en `/compartido`.
5. Cargar las 2 filas (Badia, Pieri) y migrar; borrar `BADIA_DATABASE_URL`/`TRANSFER_LOCAL_ES`.
