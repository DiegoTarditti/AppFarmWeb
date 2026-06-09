# Tarea para Cline — Domicilios estructurados (separar piso/depto de calle+número)

## Problema
Hoy el domicilio se guarda como **un solo texto** (`DomicilioCliente.direccion`,
`Cliente.domicilio`, y el master `ObsCliente.domicilio_direccion`) que **mezcla
calle+número con piso/depto**: ej. `"bolivia 1614 DTO 2"`, `"San Martín 100 piso 1"`.

Esto:
1. **Rompe el geocoder** cuando la calle tiene un número en el nombre. El fallback
   actual (`bot/envio.py::_variantes_direccion`) recorta a "calle + PRIMER número":
   - `"bolivia 1614 DTO 2"` → `"bolivia 1614"` ✓
   - `"Pasaje 3 de Febrero 1614 dto 2"` → `"Pasaje 3"` ❌ (toma el "3" del nombre)
2. El cadete no tiene el piso/depto bien separado para entregar.

## Decisión de diseño (ya tomada — implementar así)
**Calle y número van JUNTOS** en un solo campo; **piso / depto / referencia van
SEPARADOS.** (Es como Google: `route`+`street_number` se muestran juntos en la
línea 1 del address; `subpremise`/`floor` aparte y NUNCA entran al geocoder. El
formato AR de Google trata "calle número" como un bloque.)
**No** separar calle de número: reintroduce el parseo frágil de "¿cuál es la altura?".

## Modelo de datos
En `database.py`, modelo **`DomicilioCliente`** (tabla `domicilios_cliente`),
agregar columnas (migración inline en `init_db` con `ALTER TABLE ... ADD COLUMN
IF NOT EXISTS`, ver patrón existente):
- `piso` `VARCHAR(20)` — ej. "1", "PB", "12".
- `depto` `VARCHAR(20)` — ej. "2", "B", "A".
- `referencia` `VARCHAR(200)` — monoblock/torre/barrio/entre-calles/aclaraciones.

`direccion` (ya existe) queda como **calle + número** (la línea geocodable). NO
agregar `calle`/`numero` separados.

> Opcional/menor: `Cliente.domicilio` se mantiene como está (texto libre, para
> compat); la estructura fina vive en `DomicilioCliente`. No tocar `obs_clientes`
> (read-only de ObServer).

## Parser de direcciones
Crear `bot/direcciones.py` (o sumar a `bot/envio.py`) con:

```python
def separar_direccion(texto) -> dict:
    """'bolivia 1614 DTO 2' -> {direccion:'bolivia 1614', depto:'2', piso:None, referencia:None}
    Separa los componentes de unidad del string de calle+número."""
```

Patrones a detectar (case-insensitive, tolerar acentos/abreviaturas), **extrayendo
la parte y dejándola fuera de `direccion`**:
- **Depto**: `DTO`, `DPTO`, `DEPTO`, `DEP`, `DEPARTAMENTO`, `UF` (+ valor: letra o número).
- **Piso**: `PISO`, `P°`, `P.`, `1°`/`2do`/`3er` cuando precede a depto, `PB`/`PLANTA BAJA`.
- **Referencia**: `MONOBLOCK`/`MB`, `TORRE`/`T°`, `BARRIO`/`Bº`, `CASA`, `MANZANA`/`MZ`,
  `LOTE`/`LT`, "entre X y Y", o cualquier sobrante no reconocido → a `referencia`.
- Formatos combinados frecuentes: `"... 1° B"`, `"... 4to A"`, `"... piso 2 dto B"`,
  `"... PB"`, `"... torre 3 dto 5"`.

Casos de aceptación del parser:
| Entrada | direccion | piso | depto | referencia |
|---|---|---|---|---|
| `bolivia 1614 DTO 2` | `bolivia 1614` | — | `2` | — |
| `San Martín 100 piso 1` | `San Martín 100` | `1` | — | — |
| `Av Pellegrini 1234 depto B` | `Av Pellegrini 1234` | — | `B` | — |
| `Mendoza 2500 PB` | `Mendoza 2500` | `PB` | — | — |
| `Pasaje 3 de Febrero 1614 dto 2` | `Pasaje 3 de Febrero 1614` | — | `2` | — |
| `Rioja 950 1° B` | `Rioja 950` | `1` | `B` | — |
| `Av Francia 2000 monoblock 4 dto 12` | `Av Francia 2000` | — | `12` | `monoblock 4` |

> Clave: el parser **NO** debe romper la calle con número en el nombre
> ("Pasaje 3 de Febrero"): solo recorta lo que matchea un patrón de unidad **al
> final** del string.

## Geocoding
- `bot/envio.py::geocodificar` debe geocodificar **solo `direccion` (calle+número)**.
  Antes de geocodificar un texto libre legacy, pasarlo por `separar_direccion` y
  usar el `direccion` resultante. Así dejamos de depender del regex de "primer número".
- (Podés simplificar `_variantes_direccion` si el input ya viene limpio.)

## UI — `/pedido/nuevo` (`templates/pedido_nuevo.html`) y edición de cliente en `/reparto`
- Inputs separados: **Dirección (calle y número)** · **Piso** · **Depto** ·
  **Referencia**.
- Al traer un domicilio de ObServer (viene mezclado) o un `DomicilioCliente`
  legacy con todo en `direccion`: correr `separar_direccion` y **prellenar** los
  inputs separados.
- Al guardar un domicilio (`/reparto/pedido` con `domicilio` nuevo, y donde se
  crean `DomicilioCliente`): persistir `direccion`/`piso`/`depto`/`referencia` por
  separado. Geocodificar con `direccion`.
- Mostrar al cadete (vista `/reparto/cadete/<token>` y planilla): la dirección +
  piso/depto/referencia legibles (ej. "Bolivia 1614 — Piso 1 Dto B").

## Backfill (una vez)
Migración/comando que recorra `DomicilioCliente` con `piso/depto/referencia` NULL y
`direccion` no vacío → `separar_direccion(direccion)` → setear los 3 campos y limpiar
`direccion`. Idempotente (solo filas sin estructurar). Loggear cuántas tocó.

## Tests (`tests/`, SQLite in-memory, sin red)
- `test_separar_direccion_*` — uno por fila de la tabla de aceptación (incluido el
  caso crítico "Pasaje 3 de Febrero 1614 dto 2" → direccion intacta).
- `test_separar_direccion_sin_unidad` — "Bolivia 1614" → direccion igual, resto None.
- `test_geocode_usa_solo_calle_numero` — mock del geocoder, verificar que recibe
  "calle número" sin el depto.
- `test_domicilio_guarda_campos_separados` — crear DomicilioCliente con los 4 campos.
- `test_backfill_estructura_legacy` — fila legacy mixta → queda estructurada.

## NO hacer
- No escribir en `obs_clientes` (espejo read-only de ObServer).
- No separar calle de número (decisión tomada: van juntos).
- No romper el geocoder actual para direcciones ya limpias.
- Mantener `ruff check .` limpio.

## Archivos a tocar
- `database.py` — columnas nuevas + migración inline en `init_db`.
- `bot/direcciones.py` (nuevo) o `bot/envio.py` — `separar_direccion`.
- `bot/envio.py` — `geocodificar` usa la dirección limpia.
- `routes/reparto.py` — persistir/leer los campos separados; backfill.
- `templates/pedido_nuevo.html` + el modal de cliente en `templates/reparto.html`
  — inputs separados + prefill con el parser.
- `tests/test_reparto.py` (o `tests/test_direcciones.py`) — los tests de arriba.
