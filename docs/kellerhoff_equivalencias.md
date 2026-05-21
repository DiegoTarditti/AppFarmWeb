# Equivalencias Kellerhoff — diseño (2026-05-21)

> **Estado: PROPUESTA, sin implementar.** Para revisión de Diego antes de codear.

## Problema

El pedido a Kellerhoff se exporta con **nuestro EAN** (plantilla `CodigoBarra`/`Cantidad`).
Pero Kellerhoff identifica sus productos por su **código interno `CodKellerhoff`**, y
**los EANs no siempre coinciden** aunque sea el mismo producto. Resultado: filas
"REGISTRO ERRONEO" / $0 en el pedido.

Evidencia (export real, muestra de 10 EANs que fallaron):
- 7/10 son el mismo producto en Kellerhoff con **otro EAN** → recuperables por **Alfabeta**.
- 3/10 no: Alfabeta desalineado (ACEMUK 200), producto que Kellerhoff no trae
  (AZATIOPRINA RAFFO), o PACK con EAN malformado y sin Alfabeta (TERMOFREN).

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

## Cascada de resolución (export-time)

Para cada ítem del pedido (nuestro EAN):
1. **EAN en `kellerhoff_catalogo`** → `codigo_kellerhoff`. (mayoría)
2. **`kellerhoff_equivalencia[ean]`** → `codigo_kellerhoff`. (rescatados)
3. **Sin resolver** → marca "REGISTRO ERRONEO" (no rompe el export; queda flag para resolver).

## Construcción de equivalencias (batch de matching)

Sobre los EANs que NO matchean directo por EAN, resolver vía `ObsProducto`
(alfabeta/troquel/nombre) contra el catálogo:

La equivalencia se clava sobre el **EAN principal** (el que el export emite:
`ObsCodigoBarras` orden mínimo). Solo se crea si el principal NO está directo en
el catálogo. Cada paso exige candidato único.

0. **EAN alternativo** → otro EAN del mismo producto que SÍ está en el catálogo
   (caso ACCU-CHEK: principal `4015630980505` no está, alt `4015630981960` sí →
   CodKel 1000003240). **El de mayor recuperación** (~1540 de 1701 reales). ALTA.
1. **Alfabeta** → 1 candidato: auto ALTA. Varios: ambiguo → manual.
2. **Troquel** → idem (backup cuando no hay alfabeta).
3. **Nombre + presentación** → **NO implementado aún**; manual desde Presentación
   (Fase 3), por seguridad (auto-match por nombre mete equivalencias falsas).
4. **Sin candidato** → cola manual (incluye lo que Kellerhoff no trae).

Auto-aplica solo ALTA. MEDIA/BAJA y ambiguos van a una **cola de revisión manual**.

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
4. **Integración con la entidad `Plantilla` (existente)**: NO hacer swap mágico.
   Se agrega un **tipo de columna nuevo `cod_kellerhoff`** al editor de plantillas
   (`Plantilla` / `plantilla_editor.html`), de modo que el usuario define en la
   plantilla qué columna exporta el código de Kellerhoff. Al exportar, esa columna
   resuelve EAN → `codigo_kellerhoff` por la cascada (catálogo directo →
   equivalencia → sin resolver). Tocar:
   - `FIELD_DEFS` (plantilla_editor.html), `CAMPOS_SISTEMA` (database.py),
     `EXPORT_FIELDS` (routes/laboratorios.py) — sumar `cod_kellerhoff`.
   - el resolver de export en `compras_dia.py` (`_HEADER_LABEL` + armado de filas)
     para que la columna `cod_kellerhoff` traiga el código resuelto.
   - filas sin resolver → exportan vacío o flag "REGISTRO ERRONEO" según convenga.

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
