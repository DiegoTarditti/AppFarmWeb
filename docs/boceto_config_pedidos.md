# Boceto — `/config/tipos-pedido` simple + presets + PRUEBA

_Creado: 2026-05-21. Estado: BOCETO acordado, pendiente de implementar._
_Objetivo: que la config de comportamiento de pedidos la entienda gente NO técnica._

## Problema

`/config/tipos-pedido/<slug>/edit` hoy expone jerga (`piso_ideal`,
`target_horizonte`, `universo`, `override_producto`, `redondeo`). Hay que ser
pro para entenderlo. Ya tiene un **simulador en vivo** (lo más fuerte), pero
con inputs abstractos (daily_rate=4, factor_h=0.5).

## Modelo (decisión: B)

Los **tipos de pedido** (REPOSICION, COMPRA_LAB, PRUEBA) siguen siendo la unidad
estructural. Los **presets** son un **quick-fill**: elegís un preset → rellena
los campos del tipo que estás editando → guardás. No reemplazan a los tipos.

## 1. Presets (quick-fill)

Desplegable "Aplicar preset" que llena la config. Valores propuestos:

| Preset | Qué hace | Config que setea |
|---|---|---|
| **Pedido Chico** | Solo cubrir faltantes (subir al mínimo) | piso=mínimo del producto · sin target · base=u3m |
| **Pedido Semanal** | Cubrir ~7 días | piso=venta diaria × **7** · base=u3m |
| **Pedido Quincenal** | Cubrir ~15 días | piso=venta diaria × **15** · base=u3m |
| **Pedido Mensual** | Cubrir ~30 días | piso=venta diaria × **30** · base=u12m (más estable) |

(PRUEBA estacional no es un preset de cobertura fija → se configura aparte con
base=u12m_estacional.)

## 2. UI legible (preguntas en castellano, no enums)

Cada knob técnico → una pregunta humana. Básico (3 preguntas) + ⚙ Avanzado colapsado.

**Básico:**
- **¿Qué productos entran?** (`universo`) → "Los que están bajo mínimo" / "Los de un laboratorio" / "Los de un módulo" / "Selección manual".
- **¿Cuánto querés tener en stock?** (`piso_ideal`) → "Lo que marca el mínimo" / "Lo que se vende en N días" / "Nada (solo el horizonte)".
- **¿Para cuántos días comprás?** (`dias_cobertura_fijo`) → número, o el slider del armado.

**Avanzado (colapsado):**
- **¿Qué ventas miro para calcular?** (`base_demanda` — NUEVO) → "Recientes (últimos 3 meses)" / "Año completo (12m)" / "Estacional (12m × índice de la droga)".
- **Si el producto tiene cantidad fija seteada** (`cant_fija_efecto` — NUEVO) → "Gana (pide esa cantidad)" / "Es un piso" / "Ignorar".
- **Si hay mínimo de oferta** (`oferta_min_efecto` — NUEVO) → "Subir al mínimo" / "Solo avisar (chip)" / "Ignorar".
- Buffer % (`buffer_pct`), redondeo (`redondeo`).

## 3. Simulador con PRODUCTO REAL + "ejemplo en palabras"

- En vez de tipear daily=4/min=40: **buscador de producto** → trae stock/ventas/
  mínimo reales de ObServer (vía `producto_metrics`).
- Muestra: *"Para AMOXIDAL DUO (vende ~4/día, stock 10, mínimo 40) → **pedís 30 u**, cubrís ~30 días."* — traduce el número a una frase.
- Comparador de presets opcional: *"Chico → 12u · Semanal → 28u · Mensual → 95u"*.

## 4. Red de seguridad: "Restaurar a base"

Botón **"Restaurar a base"** que revierte la config del tipo a su preset/seed
conocido-bueno. Si alguien mete la pata con valores raros → restaurar → vuelve a
la base. Nadie puede dejar la config inservible de forma permanente.

## 5. Variables nuevas a agregar (config_json)

| Eje | Opciones | Default (= comportamiento actual) |
|---|---|---|
| `base_demanda` | u3m / u12m / u12m_estacional | u3m (REPOSICION/COMPRA_LAB), estacional (PRUEBA) |
| `cant_fija_efecto` | override / piso / ninguno | override |
| `oferta_min_efecto` | piso / indicador / ninguno | piso (pero PRUEBA → indicador, por decisión del usuario) |

`base_demanda` lo lee el **caller** (cómo calcula daily_rate); los otros dos, el
motor (`calcular_a_pedir`) + el helper del planificador (`aplicar_overrides_planificador`).

## 6. Alta de PRUEBA

Nuevo tipo `PRUEBA` (categoría 'pedido'), seed con config = comportamiento actual
de `/pedido/prueba`: base=u12m_estacional, oferta_min_efecto=indicador,
cant_fija_efecto=override. Después `/pedido/prueba` lee su `TipoPedidoConfig`.

## Plan de implementación (incremental, cada paso commiteable + testeado)

1. **Variables nuevas en config + motor** (default=actual, no cambia nada):
   `base_demanda`, `cant_fija_efecto`, `oferta_min_efecto` en ENUMS + edit POST
   + `calcular_a_pedir`/`aplicar_overrides_planificador` los leen.
2. **Alta de PRUEBA** (seed) + wire `/pedido/prueba` a leer su config.
3. **UI legible**: preguntas en castellano + básico/avanzado + presets quick-fill
   + "restaurar a base" + simulador con producto real + ejemplo en palabras.

Riesgo: bajo si los defaults preservan el comportamiento actual y se verifica
paridad con la suite. La UI es aditiva.
