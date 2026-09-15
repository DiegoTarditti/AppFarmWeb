# Robot expendedor (TCN) — conexión y acceso a su base de datos (2026-09-15)

> Nota: distinto del "robot Rowa" (dispensador de farmacia, ver
> `docs/circuito_robot.html` y el backlog en `docs/mejoras_pendientes.md`).
> Este documento es sobre el robot expendedor de perfumería (vending machine).

> ⚠️ Las credenciales SSH reales del equipo NO están en este documento.
> Están guardadas aparte (preguntarle a Diego). Acá sólo queda la forma de
> conectarse y lo que se encontró.

## Objetivo

Poder actualizar precios (y potencialmente otros datos) del robot expendedor
desde una app propia (AppFarmWeb u otra), en vez de cargarlos a mano en el
equipo.

## Arquitectura del equipo

El robot expendedor es una tablet Android que corre:

- **`com.tcn.vending`** — la app comercial de venta (plataforma **TCN**, muy
  usada en hardware de vending chino). Es la que hay que leer/escribir para
  cambiar precios, stock, etc.
- **Termux** (`com.termux`) instalado como app Android normal — es la puerta
  de entrada. Corre bajo un UID de app distinto al de `com.tcn.vending`, por
  eso todo acceso a los datos de la app de venta se hace vía `su 0` (root).
- **Dropbear** (SSH server liviano) corriendo dentro de Termux, escuchando en
  el puerto **8022** de la red local del equipo.
- **cloudflared** corriendo también, probablemente para exponer el SSH (u
  otro servicio) hacia afuera sin abrir puertos en el router — no se
  investigó el túnel en detalle en esta sesión.
- El equipo tiene acceso root de fábrica (`/system/xbin/su`), habilitado para
  la cuenta de Termux.
- Repo `Vending_TermuxScripts` (github.com/InfasSRL/Vending_TermuxScripts)
  clonado en `~/Vending_TermuxScripts` dentro del equipo, con `install.sh`
  que instala/configura Dropbear, cloudflared y demás. Es el mecanismo ya
  usado por la empresa para dejar el equipo administrable remotamente.
- También hay un setup de **Frida** en `~/frida` con scripts propios
  (`run-action.sh opendoor`, `dooropen`, `doorclose`, `service-manage.sh`)
  que hookean la app para forzar acciones físicas (abrir puerta, etc.) — no
  se tocó en esta sesión, pero está ahí como precedente de instrumentación
  más profunda de `com.tcn.vending`.
- Apps de acceso remoto visual ya instaladas en el equipo: **TeamViewer
  QuickSupport** y **RustDesk** (`com.carriez.flutter_hbb`) — sirven para ver
  la pantalla en vivo al hacer cambios.

## Cómo conectarse

```
Puerto: 8022  (SSH / Dropbear)
```

Host y credenciales: pedirle a Diego (no van en el repo).

Conexión de prueba con Python + `paramiko`:

```python
import paramiko

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(
    hostname="<ip-local-del-equipo>",
    port=8022,
    username="<usuario>",
    password="<password>",
    timeout=5,
    banner_timeout=5,
    auth_timeout=5,
    look_for_keys=False,
    allow_agent=False,
)
stdin, stdout, stderr = client.exec_command("uname -a")
print(stdout.read().decode())
client.close()
```

Resultado esperado: `Linux localhost 4.4.143 #26 SMP PREEMPT ... armv7l Android`.

### Obtener root

El usuario SSH es una app sandboxeada sin permisos sobre
`/data/data/com.tcn.vending`. Para leer/escribir sus datos hace falta
escalar con `su`:

```
su 0 <comando>
```

Ejemplo: `su 0 id` → `uid=0(root) gid=0(root) ...`

No usar `su -c "<comando>"` ni `su root -c ...` — el binario `su` de este
equipo (`/system/xbin/su`) **no** soporta el flag `-c` de sudo-like, sólo el
formato `su <uid> <comando>`.

## La base de datos de precios

Ruta: `/data/data/com.tcn.vending/databases/TcnVending.db` (SQLite, sólo
accesible con `su 0`).

`sqlite3` (v3.9.2) ya está disponible en el equipo — no hace falta instalar
nada, alcanza con `su 0 sqlite3 <db> "<query>"`.

Tablas relevantes:

| Tabla | Contenido | Notas |
|---|---|---|
| `Coil_info` | **Una fila por canaleta física.** Precio, nombre, stock, código de producto. | Es la tabla que hay que tocar para precios. |
| `Goods_info` | Pensada como catálogo de productos únicos | **Vacía** en este equipo — no se usa. |
| `WmCoil_info` | Nombres/contenido multi-idioma por canaleta (50+ columnas de idiomas) | No se tocó. |
| `Order_Transaction`, `Sell` | Historial de ventas/transacciones | No explorado en detalle. |
| `KEY`, `ServerData`, `Advert`, `Feeding_statistics` | Config, publicidad, estadísticas | No explorado. |

### Columnas clave de `Coil_info`

```sql
CREATE TABLE Coil_info(
  ID integer primary key autoincrement,
  Coil_id integer,        -- número de canaleta física
  Par_name text,          -- nombre del producto
  Extant_quantity integer,-- stock actual en esa canaleta
  Par_price text,         -- PRECIO (el que se actualiza)
  Goods_code text,        -- código de producto (clave para matchear con AppFarmWeb)
  Sale_price text,        -- visto siempre en 6553.5 en todas las filas -> parece sin uso real
  Work_status integer,
  Slot_status integer,
  ...
);
```

Ejemplo de fila real (valores de referencia, no confidenciales):

```
Coil_id=1  Par_name='Perfume Polo Green'  Extant_quantity=1  Par_price=20.00  Goods_code='PPGREEN2'
Coil_id=2  Par_name='Miss Dior'           Extant_quantity=0  Par_price=20.00  Goods_code='PMD'
Coil_id=3  Par_name='OCEAN MAN'           Extant_quantity=4  Par_price=75000.00 Goods_code='PERFOCEAN'
```

(Los `Par_price` con formato tan distinto — 20.00 vs 75000.00 — probablemente
mezclan moneda o son datos de carga histórica; **validar contra la pantalla
real del equipo antes de asumir la unidad**.)

### Query para actualizar precio

Se decidió matchear por **`Goods_code`** (no por `Coil_id`), porque
representa el producto — si el mismo producto está cargado en varias
canaletas, el mismo `UPDATE` las actualiza a todas de una:

```sql
UPDATE Coil_info SET Par_price = '<precio>' WHERE Goods_code = '<codigo>';
```

Si en algún momento se necesita fijar precio por canaleta física puntual
(no por producto), usar `Coil_id` en el `WHERE` en su lugar.

## Prueba real hecha en esta sesión

Se actualizó el precio de un producto sin stock (cero riesgo de venta real)
con:

```sql
UPDATE Coil_info SET Par_price='99.99' WHERE Goods_code='PMD';
```

Confirmado por lectura posterior desde `sqlite3` directo: el valor quedó
persistido en el archivo.

**Pendiente de confirmar**: si la UI del robot (`com.tcn.vending`) refleja el
cambio en caliente sin reiniciar la app, o si hace falta reiniciarla.

### Alternativa más segura si `sqlite3` directo no refresca la UI

La app tiene su propio `ContentProvider`:
`com.tcn.vending/com.ys.db.provider.DbProvider`. Escribir a través de
`content update` (comando Android `content`) contra ese provider, en vez de
`sqlite3` crudo sobre el archivo, dispara `notifyChange()` y es más probable
que la UI se refresque sola. No se probó en esta sesión — queda como
siguiente paso si el `UPDATE` directo requiere reinicio de la app.

Si hiciera falta reiniciar la app tras el `UPDATE`, el comando sería:

```
su 0 am force-stop com.tcn.vending
su 0 am start -n com.tcn.vending/<activity-principal>   # falta identificar la Activity de arranque
```

(No se investigó cuál es la Activity principal ni si el reinicio interrumpe
una venta en curso — **no ejecutar en horario de uso sin confirmarlo**.)

## Próximos pasos sugeridos para la app completa

1. Confirmar si `sqlite3` directo alcanza para refrescar la UI, o si hace
   falta pasar por `content update` / reiniciar la app.
2. Mapear `Goods_code` de `Coil_info` contra los productos/EANs que ya
   maneja AppFarmWeb (tabla `productos` / `codigo_alfabeta` / EAN) para saber
   cómo matchear automáticamente sin carga manual de equivalencias.
3. Decidir el mecanismo de conexión productivo: ¿SSH directo al equipo
   (requiere estar en la misma red o VPN), o aprovechar el túnel de
   `cloudflared` que ya corre en el equipo para llegar desde Render sin
   exponer la red local?
4. Si se va a automatizar desde AppFarmWeb (Render), guardar la contraseña
   SSH como variable de entorno, igual que se hizo con `ANTHROPIC_API_KEY`
   (ver sección "LLM matcher" en `CLAUDE.md`) — nunca en el código.
5. Revisar `Order_Transaction` / `Sell` si en algún momento se quiere traer
   ventas del robot hacia AppFarmWeb (reportes, stock real vs. vendido).
6. Confirmar si hay **más de un robot** desplegado — si es así, cada uno va a
   tener su propia IP local y probablemente usuario/clave Termux distintos;
   este documento describe **un** equipo puntual.
