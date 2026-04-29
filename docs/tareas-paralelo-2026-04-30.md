# Tareas paralelo 2026-04-30

## Objetivo de la sesión B

Actualizar la documentación y dejar claras las instrucciones para trabajar en el flujo de "match dimensional" asociado al conversor de facturas.

Se busca que el equipo pueda:

- entender cómo funciona el `match dimensional` en `catalogacion.py`
- ver cómo se integra con el conversor de facturas (`/converter`)
- revisar y mejorar la documentación de soporte en `docs/converter_flow.md`
- dejar tareas concretas para implementar ajustes y pruebas

## Contexto

El repositorio ya tiene:

- un flujo de conversor PDF basado en `/converter/<token>/pick` y `/converter/<token>/verify`
- una función de búsqueda de productos por atributos similares en `catalogacion.py` bajo el título "Match dimensional"
- un documento de flujo general `docs/converter_flow.md`

La sesión B debe enfocarse en documentar y validar el comportamiento de:

- extracción de atributos desde `descripcion`
- búsqueda de candidatos en base a `monodroga_norm`, `concentracion_mg`, `forma_farma`, `cantidad_envase`
- scoring de candidatos y umbrales
- la posible integración con el conversor para sugerir matches de producto

## Archivos clave

- `docs/converter_flow.md`
- `catalogacion.py`
- `routes/converter.py`
- `templates/converter_pick.html`
- `templates/converter_verify.html`
- `field_inference.py`
- `helpers.py`

## Instrucciones para la sesión B

1. Revisar `catalogacion.py` sección `match_dimensional_candidatos`
   - verificar cómo se extraen los atributos de la descripción
   - confirmar qué campos son usados para scoring
   - chequear los umbrales: score >= 5 = probable, score >= 7 = casi seguro

2. Confirmar el uso de `match_dimensional_candidatos`
   - buscar en el repositorio llamadas a esta función
   - si no se usa aún desde el conversor, documentar el gap y proponer un punto de integración

3. Actualizar `docs/converter_flow.md`
   - agregar un bloque breve que describa el soporte de match dimensional y el valor añadido
   - indicar qué atributos se usan para match: droga, concentración, forma farmacéutica, envase
   - dejar claro que la búsqueda devuelve candidatos ordenados por score

4. Probar manualmente el flujo del conversor
   - cargar un PDF en `/converter/upload`
   - validar que el pipeline de `/pick` divide el texto y sugiere campos
   - si es posible, verificar si el resultado del parser puede usar la búsqueda dimensional para sugerir equivalencias

5. Agregar una mini checklist de tests
   - caso básico de `match_dimensional_candidatos` con datos completos
   - caso con solo `descripcion` y extracción de atributos
   - caso con `score` exacto 5 y exacto 7
   - caso sin atributos útiles → devuelve lista vacía

## Resultado esperado

- nuevo documento `docs/tareas-paralelo-2026-04-30.md` disponible en el repositorio
- aclaración del objetivo y pasos de la sesión B
- referencias directas a las piezas de código clave
- tareas suficientemente concretas para arrancar en paralelo

## Notas finales

Si hay que derivar esto a un sprint o a un par de tickets, usar como base los items de esta lista y completar con:

- "Documentar la integración de match dimensional en el conversor"
- "Agregar pruebas unitarias para `match_dimensional_candidatos`"
- "Validar el flujo `/converter/<token>/pick` con match dimensional"

## Hallazgos de la sesión B (2026-04-30)

### Estado real del match dimensional

- Función [`match_dimensional_candidatos`](../catalogacion.py#L315) implementada con scoring 5/3/2/2 (droga/conc/forma/cantidad). Umbrales 5=probable, 7=casi seguro confirmados.
- Atributos extraídos por [`extraer_de_descripcion`](../catalogacion.py#L125) (regex) + [`enriquecer_desde_obs`](../catalogacion.py#L201) (DW.Productos cuando `producto.observer_id` está set). Merge con prioridad `obs > regex`.

### Call-sites existentes

| Lugar | Cómo se invoca |
|---|---|
| [`routes/productos.py:431`](../routes/productos.py#L431) | endpoint `GET /api/match-dimensional` |
| [`templates/order_detail.html`](../templates/order_detail.html) | botón 🔍 en panel cruce manual módulos (commit `f577102`) |
| [`templates/catalogacion.html`](../templates/catalogacion.html) | búsqueda manual por atributos |

### Gap: el conversor no usa match dimensional

`routes/converter.py` y los templates `converter_pick.html` / `converter_verify.html` **no invocan** `/api/match-dimensional`. Punto de integración propuesto en [`docs/converter_flow.md`](converter_flow.md) (sección "Match dimensional") — agregar columna "Sugerencia catálogo" en `/verify` para ítems con EAN no resoluble.

### Checklist de tests para `match_dimensional_candidatos`

Sugerencia de fixtures (PostgreSQL test DB con `producto_atributos` poblada):

- [ ] **Caso datos completos**: pasar `monodroga_norm='ibuprofeno'`, `concentracion_mg=600`, `forma_farma='CPR'`, `cantidad_envase=10`. Esperado: producto matcheante con `score=12`.
- [ ] **Solo descripción**: pasar `descripcion='IBUPIRAC 600 X 10 CPR'`. Esperado: extrae conc=600 + forma=CPR + cantidad=10 (NO droga, viene de obs); score≥7 si hay producto coincidente.
- [ ] **Score exacto 5**: solo droga matchea. Producto con misma `monodroga_norm` pero distinta concentración. Esperado: `score=5`.
- [ ] **Score exacto 7**: droga + cantidad. Esperado: `score=7` (5+2).
- [ ] **Sin atributos útiles**: descripción vacía o solo whitespace. Esperado: `[]`.
- [ ] **Solo ML en líquido**: `descripcion='LACTULON JARABE X 200 ML'`. Esperado: `forma='SUSP'`, `cantidad=200`, **NO** `concentracion_mg=200` (ML es volumen del envase, no dosis).
- [ ] **MG/ML compuesta**: `descripcion='AMOXIDAL 250 MG/5ML SUSP X 60 ML'`. Esperado: `concentracion_mg=250`, `concentracion_unidad='MG/5ML'` (no se aplasta a ML solo).
- [ ] **Concentración huérfana en CPR/CAP**: `descripcion='ACTRON 600 RAPIDA ACCION CAP X 10'`. Esperado: `concentracion_mg=600` por fallback de número entre 1-1000 + forma=CAP.
- [ ] **`limit` honorado**: pasar 50 candidatos coincidentes con `limit=10`. Esperado: lista de 10.
- [ ] **Ordenamiento**: 3 candidatos con scores 12/8/5. Esperado: orden desc.

Ubicación sugerida: `tests/test_catalogacion.py` (nuevo). Usar `pytest` + fixture `db_session` con rollback al final.