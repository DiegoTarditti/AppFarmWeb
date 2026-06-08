# Tarea para Cline — Clientes en /reparto (registro de pedidos que NO entran por WhatsApp)

## Objetivo
En **`/reparto`** (panel de reparto, `routes/reparto.py` + `templates/reparto.html`)
se registran pedidos a mano (los que NO llegan por el bot de WhatsApp). Hoy el
formulario de "nuevo pedido" tiene un campo de nombre de cliente con búsqueda,
pero falta:

1. **Búsqueda multitoken UNIFICADA** de cliente (ObServer + clientes locales).
2. **Precargar los datos** del cliente al elegirlo (teléfono, DNI, domicilio, ciudad, OS).
3. **Botón "＋ Nuevo cliente"** para dar de alta uno que no está en la base.
4. **Editar / agregar datos** del cliente elegido (teléfono, domicilio, ciudad, etc.).

> Importante: el pago/entrega son presenciales; esto es solo registro. No tocar
> nada de pagos online.

## Contexto técnico (leer antes de codear)
- **Modelo `Cliente`** (tabla `clientes`, en `database.py`): capa unificada/editable.
  Campos: `id, observer_id (FK obs_clientes, nullable), nombre, apellido, dni,
  domicilio, telefono, ciudad, notas, tags, whatsapp, email, fecha_nacimiento,
  creado_por, creado_en, actualizado_en`. Un Cliente puede ser **local** (sin
  `observer_id`) o estar **linkeado a ObServer** (`observer_id` seteado).
- **`ObsCliente`** (tabla `obs_clientes`): master sincronizado desde ObServer
  (read-only). Campos: `observer_id (PK), apellido_nombre, documento_numero,
  telefono, domicilio_direccion, localidad, ...`. **No se escribe** acá.
- **`get_or_create_cliente(s, observer_id=None, lead=None, creado_por=None)`**
  (en `database.py`): **único punto de entrada** para obtener/crear un `Cliente`.
  Usar SIEMPRE esto para resolver el `cliente_id` (no crear `Cliente()` a mano).
  Leer su implementación (línea ~458) para saber qué espera en `lead`.
- **`store.buscar_clientes(query, limite=10)`** (en `bot/store.py`): hoy es
  multitoken (AND sobre `apellido_nombre`) o documento exacto, **pero solo busca
  `obs_clientes`**. Devuelve `[{observer_id, nombre, documento, telefono}]`.
- **`DomicilioCliente`** (tabla): domicilios por cliente/conversación. Ya hay
  endpoint `/reparto/api/<oid>/domicilios`.
- Rutas existentes en `routes/reparto.py`:
  - `GET /reparto/api/buscar-cliente?q=` → `store.buscar_clientes`.
  - `GET /reparto/api/<oid>/domicilios`.
  - `POST /reparto/pedido` → crea `PedidoReparto`; resuelve `cliente_id` con
    `get_or_create_cliente(observer_id=_oid)`.
- UI: `templates/reparto.html`, el form de pedido usa `#pCliente` (nombre),
  resultados de búsqueda, `#pDom` (domicilios), y `window._oid` (observer_id elegido).
- Convenciones del repo (ver `CLAUDE.md` + memoria): sesiones SQLAlchemy SIEMPRE
  con `with database.get_db() as s:`; nada de `Producto.all()` masivo; tests con
  SQLite in-memory en `tests/`; ruff limpio (`ruff check .`).

## Qué hay que hacer

### 1. Búsqueda multitoken unificada (ObServer + locales)
**Crear una función NUEVA `store.buscar_clientes_unificado(query, limite=12)`.**
NO modificar `store.buscar_clientes` → la usa el bot (telegram/whatsapp) para
vincular clientes; dejarla **intacta por compatibilidad**.
- La tokenización multi-token AND **ya existe** en `buscar_clientes`
  (`bot/store.py`: `tokens = q.split()` + `and_(*ilike...)`, doc exacto si numérico).
  **Reusá esa lógica, no la reinventes.**
- Buscar en `obs_clientes` (como hoy) **y además** en la tabla `clientes` locales
  (por `nombre`/`apellido`/`dni`/`telefono`, mismo multi-token AND).
- **Dedup**: un `Cliente` local con `observer_id` NO debe aparecer dos veces
  (preferí la fila local; ocultá la de ObServer con ese mismo `observer_id`).
- Cada resultado: `{observer_id, cliente_id, nombre, documento, telefono, ciudad}`
  (cualquiera de los dos ids puede ser `null`). **Sin sistema de `ref`**: el front
  usa `cliente_id` si existe, si no `observer_id`.
- Apuntar `GET /reparto/api/buscar-cliente` a esta función nueva.

### 2. Endpoint de ficha (precarga al elegir) — REUSAR lo que existe
**Ya existe `store._ficha_de_cliente(s, cliente_id)`** (`bot/store.py`): devuelve
la ficha uniforme mergeando ObServer + local
(`{cliente_id, observer_id, fuente, nombre, documento, telefono, domicilio,
notas, tags, whatsapp, email}`). **Toma una sesión `s` abierta** (no abre una
propia). No reinventarla.
- Crear `GET /reparto/api/cliente?cliente_id=Y` (o `?observer_id=X`).
- Resolución: si viene `observer_id` y no `cliente_id`, resolver con
  `database.get_or_create_cliente(s, observer_id=X, creado_por=current_user.id)`
  (crea/linkea y devuelve `cliente_id`) y commitear; después
  `_ficha_de_cliente(s, cliente_id)`.
- Sumar al dict los `DomicilioCliente` del cliente (`domicilios:[...]`).
- Front: al clickear un resultado, llamar esto, **rellenar** inputs (nombre,
  teléfono, dirección, ciudad, dropdown de domicilios) y guardar el `cliente_id`
  para el POST del pedido.

### 3. Alta de cliente nuevo
- Botón **"＋ Nuevo cliente"** en el form de pedido de `reparto.html`.
- Mini-form: **nombre, apellido, DNI, teléfono, domicilio, ciudad** (ciudad =
  dropdown del catálogo `Ciudad`, ya existe; ver `store.listar_ciudades`).
- `POST /reparto/cliente` → usar **`database.get_or_create_cliente(s, lead={nombre,
  apellido, dni, domicilio, ciudad, telefono}, creado_por=current_user.id)`
  DIRECTO** (devuelve `cliente_id`; hace `flush`, commitea el caller).
  **NO usar `store.crear_cliente_local`** → esa exige `conv_id` y en reparto no
  hay conversación.
- Devolver el `cliente_id` y dejarlo seleccionado en el form.

### 4. Editar / agregar datos del cliente
- Botón **"Editar datos"** sobre el cliente elegido.
- **Merge logic (importante):** para el form de edición, exponer los **campos
  CRUDOS de la fila `Cliente`** (`nombre, apellido, dni, telefono, domicilio,
  ciudad, notas`) — NO el dict mergeado de `_ficha_de_cliente` (ese, cuando el
  cliente está linkeado a ObServer, muestra `nombre/documento` del **master**,
  no los campos locales). Así el usuario edita los valores reales de la capa local.
- Si está linkeado a ObServer: mostrar el dato del master como **referencia
  read-only**, dejar los campos locales editables; si un campo local está vacío,
  se puede prellenar con el de ObServer como ayuda. **Al guardar, solo se escribe
  en `clientes`** — NUNCA en `obs_clientes` (espejo read-only de ObServer).
- `POST /reparto/cliente/<cliente_id>` → update SOLO de esos campos en `clientes`
  (+ `actualizado_en`).

## Criterios de aceptación
- Buscar "perez juan" (o "juan perez") encuentra al cliente esté en ObServer o
  sea local, sin duplicados.
- Al elegirlo, el form queda precargado con teléfono/domicilio/ciudad si existen.
- "＋ Nuevo cliente" crea el cliente y queda seleccionado; el pedido se guarda
  con su `cliente_id`.
- "Editar datos" persiste los cambios en `clientes` y se reflejan al re-buscar.
- `POST /reparto/pedido` sigue funcionando y guarda el `cliente_id` correcto
  (reusando `get_or_create_cliente`).
- `ruff check .` limpio + **tests** (SQLite in-memory, sin IA ni red) en
  `tests/test_reparto.py`:
  - `test_buscar_clientes_unificado_obs_only` — solo resultados de ObServer.
  - `test_buscar_clientes_unificado_local_only` — solo clientes locales.
  - `test_buscar_clientes_unificado_mixed_dedup` — mismo cliente en ambas fuentes
    (Cliente local con observer_id) → **un solo resultado**.
  - `test_buscar_clientes_unificado_doc_exacto` — búsqueda por DNI numérico.
  - `test_reparto_crear_pedido_con_cliente` — `POST /reparto/pedido` con `cliente_id`.
  - `test_reparto_editar_cliente` — `POST /reparto/cliente/<id>` actualiza la fila
    `clientes` (y NO toca `obs_clientes`).

## Archivos a tocar
- `bot/store.py` — `buscar_clientes_unificado`, helpers de ficha/edición.
- `routes/reparto.py` — endpoints `buscar-cliente` (reapuntar), `cliente` (ficha,
  alta, edición).
- `templates/reparto.html` — UI del picker (resultados, precarga, nuevo, editar).
- `tests/test_reparto.py` (ya existe) — sumar casos.

## NO hacer
- No escribir en `obs_clientes` (es espejo read-only de ObServer).
- No crear `Cliente()` directo: usar `get_or_create_cliente`.
- No tocar el flujo del bot/WhatsApp ni el de caja.
