# Equivalencias Kellerhoff — diseño (2026-05-21)

> **Estado: IMPLEMENTADO.** Ver historial de corrección abajo.

## Problema

El pedido a Kellerhoff se importa **por EAN** (columna tipo `CodigoBarra`). Kellerhoff
NO entiende su propio `CodKellerhoff` en la importación (es solo su ID interno en el
catálogo). El problema real: **algunos EANs nuestros no coinciden con el EAN que
Kellerhoff tiene** para el mismo producto → esas filas dan "REGISTRO ERRONEO".

**Solución:** mandar el EAN que Kellerhoff reconoce. Si nuestro EAN ya está en su
catálogo, se manda igual. Si no, se corrige al EAN de Kellerhoff (`CodBarraPrinc`) del
mismo producto, vía una equivalencia. **NUNCA se manda `CodKellerhoff`** (Kellerhoff lo
rechaza — probado: manda los 243 a REGISTRO ERRONEO).

> ⚠ **Corrección 2026-05-21:** la primera versión mandaba `CodKellerhoff` en una columna
> nueva → Kellerhoff rechazó TODO el pedido. Y el matching por Alfabeta/Troquel daba
> ~31% de falsos positivos (ej. LACTATO RINGER → DAXAS) → pediría el producto
> equivocado. Ambos corregidos: se manda EAN corregido, y el matching es solo por **EAN
> alternativo del mismo producto + guarda de nombre** (descarta los incoherentes).

## Catálogo de origen (`productos.csv` de Kellerhoff)

22.383 filas. Columnas:
`Tipo;Producto;AlfaBeta;Troquel;CodBarraPrinc;Laboratorio;Precio;Neto;CadenaFrio;RequiereVale;Trazable;CodKellerhoff`

- `CodKellerhoff`: **100% presente**, único por SKU → llave estable de Kellerhoff.
- `CodBarraPrinc` (EAN): **212 vacíos** (casi todos PACKs), **17 duplicados**, largos mixtos (8/11/12/13).
- `AlfaBeta` y `Troquel`: presentes en casi todo el rubro ético (`Tipo=D`). Nosotros ya
  guardamos `ObsProducto.codigo_alfabeta` y `ObsProducto.troquel` → puente confiable.
- `Tipo`: `D` (ético, 14.296) / `P` (perfumería/OTC, 8.087).
- `Neto/CadenaFrio/RequiereVale/Trazable`: flags 0/1. (Semántica de `Neto` a confirmar.)

## Modelo de datos (tablas específicas Kellerhoff)

### `kellerhoff_catalogo` — snapshot del CSV
| columna | tipo | nota |
|---|---|---|
| `codigo_kellerhoff` | VARCHAR(20) PK | llave estable de Kellerhoff |
| `tipo` | CHAR(1) | D / P |
| `descripcion` | VARCHAR(200) | |
| `alfabeta` | VARCHAR(15) index | nullable |
| `troquel` | VARCHAR(15) index | nullable |
| `ean` | VARCHAR(20) index | nullable (212 sin EAN) |
| `laboratorio` | VARCHAR(120) | texto del CSV |
| `precio` | DECIMAL(14,2) | |
| `neto` / `cadena_frio` / `requiere_vale` / `trazable` | BOOLEAN | flags |
| `importado_en` | TIMESTAMP | |

- Se **reemplaza por completo** en cada import (truncate + insert) — es un snapshot de precios/códigos.
- `codigo_kellerhoff` es estable entre imports → las equivalencias sobreviven.

### `kellerhoff_equivalencia` — puente nuestro→Kellerhoff (solo casos NO directos)
| columna | tipo | nota |
|---|---|---|
| `id` | PK | |
| `ean` | VARCHAR(20) UNIQUE index | nuestro EAN (el del pedido) |
| `codigo_kellerhoff` | VARCHAR(20) | FK lógica a catálogo |
| `metodo` | VARCHAR(12) | `ean_alt` / `alfabeta` / `troquel` / `nombre` / `manual` |
| `confianza` | VARCHAR(8) | ALTA / MEDIA / BAJA |
| `revisado` | BOOLEAN | confirmado a mano |
| `creado_en` / `creado_por` | | audit |

- **Solo guarda los rescatados** (EAN nuestro que NO matchea directo por EAN en el catálogo).
  Los directos se resuelven al vuelo (no se persisten → tabla chica y mantenible).

## Cascada de resolución (export-time) — `corregir_eans`

Para cada ítem del pedido devuelve el **EAN a mandar**:
1. Nuestro EAN está en `kellerhoff_catalogo` → se manda **igual** (Kellerhoff lo reconoce).
2. No está, pero hay `kellerhoff_equivalencia[ean]` → se manda el **EAN de Kellerhoff**
   (`CodBarraPrinc`) de ese `codigo_kellerhoff`.
3. No hay arreglo → se manda **nuestro EAN** (falla igual que antes, nunca vacío).

La columna de la plantilla es **`ean_kellerhoff`** (chip "EAN-Kellerhoff", header
`CodigoBarra`). Reemplaza a la columna EAN en la plantilla de Kellerhoff.

## Construcción de equivalencias (batch de matching) — `_recalcular_equivalencias`

Solo **EAN alternativo + guarda de nombre**. La equivalencia se clava sobre el EAN
principal (el que el export emite). Solo se crea si el principal NO está directo en
el catálogo:

- **EAN alternativo**: otro EAN del mismo producto ObServer que SÍ está en el catálogo
  (caso ACCU-CHEK: principal `4015630980505` no está, alt `4015630981960` sí). Exige
  **candidato único** (1 solo `codigo_kellerhoff`) Y **primer token del nombre coincide**
  (descarta falsos como LACTATO→DAXAS). Confianza ALTA.
- **Alfabeta / Troquel: NO se usan.** ~31% de falsos positivos contra el catálogo de
  Kellerhoff (códigos que no alinean). Regla: preferir falso negativo a falso positivo.
- **Nombre + presentación**: manual desde Presentación.
- **Sin candidato / incoherente**: cola manual (incluye lo que Kellerhoff no trae).

No pisa `revisado=True`. Idempotente. En la corrida real: 1387 equivalencias, 155
descartadas por la guarda de nombre.

## UI

1. **Importar catálogo** (`/kellerhoff/catalogo/importar`): subir CSV → reemplaza
   `kellerhoff_catalogo` → muestra resumen (filas, sin EAN, dups).
2. **Resolución manual = tarjeta de Presentación** (decisión 2026-05-21). NO hay
   pantalla aparte de equivalencias. La resolución de los casos que el matching
   automático no cierra se hace **por producto, en la tarjeta "📦 Presentación" de
   `/productos/flags`** (la misma donde se configura fraccionado + envase). Ahí se
   agrega una sección **"Equivalencia Kellerhoff"**:
   - muestra el match automático actual (método + confianza) si existe;
   - buscador del catálogo Kellerhoff (mismo patrón multi-token) para **elegir el
     `codigo_kellerhoff` a mano**;
   - opción **"Kellerhoff no lo trae"** (hueco real de catálogo, ej. AZATIOPRINA RAFFO);
   - si el problema es el EAN nuestro mal cargado (ej. TERMOFREN), se corrige el EAN
     y vuelve a matchear directo.
   - Guarda en `kellerhoff_equivalencia` con `metodo='manual'`, `revisado=true`.
3. **Tablero de pendientes** (opcional, fase posterior): un listado de los productos
   que un pedido a Kellerhoff dejó "sin resolver", cada uno con link a su Presentación.
   Evita tener que buscarlos uno por uno.
4. **Integración con la entidad `Plantilla` (existente)**: columna nueva
   **`ean_kellerhoff`** (chip "EAN-Kellerhoff", header `CodigoBarra`). El usuario la usa
   en la plantilla de Kellerhoff en lugar de la columna EAN. Al exportar, `corregir_eans`
   le pone el EAN corregido (catálogo directo → EAN de equivalencia → nuestro EAN como
   fallback). Tocado:
   - `FIELD_DEFS` (plantilla_editor.html), `CAMPOS_SISTEMA` (database.py) — campo `ean_kellerhoff`.
   - export en `compras_dia.py`: `_HEADER_LABEL['ean_kellerhoff']='CodigoBarra'`, formato
     entero, y `r['ean_kellerhoff']=corregir_eans(...)` en el armado de filas (xlsx + txt).
   - **nunca vacío** (fallback al EAN nuestro) → no convierte filas buenas en error.

### Las 3 categorías de "sin resolver" (validadas con datos reales)

| categoría | ejemplo | cómo se resuelve en Presentación |
|---|---|---|
| (a) recuperable por nombre | ACEMUK 200 (alfabeta nuestro 46770 ≠ Kel 63667, mismo prod) | el auto-match por nombre lo propone; se confirma o se elige a mano |
| (b) dato nuestro mal | TERMOFREN (EAN `779545012988` le falta un dígito; real `7795345012988`) | se corrige el EAN → matchea directo; o se asigna CodKel a mano |
| (c) hueco real de catálogo | AZATIOPRINA RAFFO (Kel solo trae RONTAG) | se marca "Kellerhoff no lo trae" (o se elige el sustituto RONTAG) |

## Casos borde (chequear todo)

| caso | manejo |
|---|---|
| 212 catálogo sin EAN (PACKs) | solo via alfabeta/nombre/manual |
| 17 EANs duplicados en catálogo | ambiguo → manual (desempatar por lab/presentación) |
| EAN nuestro malformado (ej. `779545012988`, 12 díg) | flag de calidad de dato; corregir en nuestra ficha |
| Alfabeta nuestro desalineado (ACEMUK 200: 46770 vs 63667) | cae a nombre+presentación o manual |
| Producto que Kellerhoff no trae (AZATIOPRINA RAFFO) | "sin resolver" legítimo; no forzar match |
| Re-import del catálogo | truncate+insert; equivalencias persisten (keyed por CodKel estable) |

## Validación / reporte

Tras import + matching: reporte con totales por método de match y **lista de los sin
resolver** para accionar. Objetivo: que un pedido a Kellerhoff salga con 0 (o mínimos)
"REGISTRO ERRONEO".

## Fases sugeridas

1. Tablas + import del CSV + reporte de cobertura.
2. Cascada de matching (alfabeta→troquel→nombre) + persistir equivalencias ALTA.
3. UI de cola manual (resolver ambiguos / sin match).
4. Integrar a la plantilla de pedido (export con CodKellerhoff).
