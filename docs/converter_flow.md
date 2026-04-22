# Conversor de facturas — UX y lógica

Documentación del flujo **"Enseñar formato"** (`/converter/<token>/pick`) + **"Verificar datos"** (`/converter/<token>/verify`), que permite parsear cualquier factura PDF sin escribir código.

Archivos clave:
- `templates/converter_pick.html` — UI principal (aprendizaje + preview + desglose)
- `templates/converter_verify.html` — pantalla de verificación antes de importar
- `templates/converter_detectar.html` — página inicial con resumen y CTA
- `routes/converter.py` — backend
- `helpers.py:_build_item_pattern` — inferencia del regex desde selecciones
- `parsers/_template.py` + parsers auto-generados — parsers persistentes

## Flujo general

1. Usuario sube PDF a `/converter`
2. Detección automática intenta identificar proveedor y ejecutar el parser existente
3. Dos caminos:
   - **"Verificar datos e importar"** — si el parser funciona, va directo a `/verify` con validación row-by-row + edición inline
   - **"Enseñar formato"** — si no funciona o es nuevo, entra al flujo de aprendizaje en `/pick`
4. Al confirmar (cualquiera de los caminos) → `_guardar_factura_desde_aprendizaje` crea `Invoice` + `InvoiceItem`s reales

## Pantalla /pick — Enseñar formato

### Layout (3 secciones)

**1. Texto del PDF** (panel izquierdo)

Mostrado como `<pre>` plano POR DEFAULT. Botón **"📑 Dividir en secciones"** parte el texto en 3 paneles con fondo distintivo:

- 📄 **Encabezado** (amber) — hasta encontrar `código/cantidad/ean/descripción`
- 📋 **Detalle (ítems)** (sky) — body entre markers
- 🧾 **Totales (pie)** (verde) — desde `total $/subtotal/hoja cant` hasta el fin

Si no encuentra markers → no divide (mensaje `alert`). Cada panel tiene scroll propio.

**Click en cualquier línea del Detalle** → se usa como "Fila de ejemplo" directamente (marca ✓ verde, auto-scroll al panel de ejemplo, tokeniza).

**2. Panel derecho con 4 cards:**
- **Encabezado (opcional)** — chips para `razon_social / cuit / numero / fecha`
- **Fila de totales (pie)** — nueva card para capturar Cant.Un/Exento/Gravado/IVA/Percep/Total
- **Fila de ejemplo** — donde pegás un renglón de ítem y asignás campos a tokens
- **Inferir y probar** — corre el regex y muestra tabla de ítems

### Sistema de tokens + groups + checkboxes

Tanto la "Fila de ejemplo" como la "Fila de totales" usan el mismo sistema:

- `tokenize(line)` → lista de `{text, start, end}` separando por whitespace
- `tokensToGroups(tokens)` → cada token inicialmente es un group individual
- Render vertical con checkbox por group + hint automático (`EAN?`, `$`, `%`, `cant?`, `📅`)
- **"⤏ Unir marcados"** fusiona groups contiguos marcados en uno solo (útil para descripción)
- **"↺ Separar"** vuelve a partir en palabras individuales
- **Ctrl+click** en un group → extiende el rango de selección desde el último clickeado

### Asignación de campos

Cada group no asignado muestra a la derecha un **dropdown compacto "asignar a…"** con los chips disponibles (filtra los ya usados). Click → se asigna y queda verde.

Complementario: chips arriba también funcionan si marcás checkboxes + chip.

### 🧮 Auto-detectar (magia matemática)

**En Fila de ejemplo:**
- Clasifica tokens: EAN (12-14 dígitos) / int chico / moneda / % / texto
- Busca triplete `cant × unit = importe` (tol 0.5%)
- Busca triplete `pub × (1 - dto/100) = unit` (tol 0.5%)
- Asigna todo + descripción = tokens entre cantidad y pub/unit

**En Fila de totales:**
- `IVA = Gravado × 21%` o `× 10.5%`
- `Total = suma de los demás moneys`
- `Exento` = mayor de los que quedan
- `Percepciones` = menor de los que quedan
- `Cant. Un.` = primer entero chico

Un click y quedan los 6-7 campos asignados automáticamente.

### Inferir patrón

Backend `POST /converter/<token>/infer` genera el regex con `_build_item_pattern`:

- Usa posiciones de las selecciones en `example_line` para reconstruir literales + placeholders
- Tipos: `[\d.,]+` para numéricos, `\d+` para enteros, `\S+` para tokens no-texto, `.+?` para descripciones
- Ancla al final de línea (`\s*$`) para evitar que el último `[\d.,]+` absorba el primer número de la fila siguiente (bug crítico de PHARMAMERICAN)
- Si dos capturas del mismo tipo quedan adyacentes sin literal entre ellas, inserta `\s+` forzado

**Segunda pasada:** sobre `items_text` (ya cortado antes de `*** PRODUCTOS EN FALTA MOMENTANEA ***`), corre un regex fallback para gravados (5 columnas: `ean cant desc unit importe`) y agrega filas no matcheadas por la primaria.

### Tabla de preview con validación

Después de inferir, cada fila muestra:

- **✓/⚠/✗** estado de la fila:
  - ✓ verde: `cant × unit ≈ importe` y `pub × (1-dto%) ≈ unit`
  - ⚠ amber: cant×unit OK pero dto no cierra
  - ✗ rojo: valores fuera de rango o cant×unit no cierra
- **Tooltip** con detalle del error
- **Todas las celdas editables** inline
- **Auto-recálculo** al editar:
  - Si cambia `cantidad` o `precio_unitario` → recalcula `importe`
  - Si cambia `precio_publico` o `dto` → recalcula `precio_unitario` → recalcula `importe`
  - Editar `importe` manual **no** pisa nada
- **×** borra fila individual
- **"Descartar filas con ✗"** → elimina en masa las inválidas
- **"↓ Ir al error"** → scroll al primer ✗ (útil con 100+ filas)

Debajo de la tabla: **Desglose de totales** (6 inputs editables con formato moneda AR) + totales calculados + comparación con Total esperado. Fondo mint-soft para destacar.

En el encabezado: controles `Σ desglose` (suma fiscal vs total), `Σ productos` (count), `Σ unidades` (sum cantidades vs `cantidad_total` declarada) — se replican en el footer para no tener que scrollear.

### Guardado

**"💾 Guardar parser"** → persiste el regex como `parsers/<slug>.py` (auto-generado) asociado al proveedor.

**"📄 Guardar como factura"** → `_guardar_factura_desde_aprendizaje` crea Invoice + Items.

Antes de persistir, valida en backend:
- `precio_unitario` ≤ 10¹¹
- `importe` ≤ 10¹¹
- `dto` en [-100, 100]
- `cant × unit` vs `importe` con tol 2%
- Si hay filas inválidas → error con detalle de las 5 primeras

También guarda breakdown fiscal en campos nuevos de `facturas`:
`monto_exento, monto_gravado, iva_105, iva_21, percepciones, otros`.

El `total` guardado usa esta prioridad:
1. Suma del desglose si hay ≥2 campos cargados
2. `header.total`
3. Suma de importes de items

## Pantalla /verify — Validación antes de importar

Ruta alternativa al /pick cuando el parser ya existe y funciona. Muestra directamente la tabla de items + desglose editable + math check, sin paso de aprendizaje.

Misma lógica de validación + sugerencias (ej: si falta `precio_publico`, calcula desde `unit / (1 - dto%)` con botón "aplicar").

## Bugs importantes arreglados

1. **PHARMAMERICAN absorbiendo EAN de fila siguiente** — `_build_item_pattern` ahora ancla con `\s*$`.
2. **Cantidad capturada mal por selecciones sin literal entre capturas** — si dos capturas numéricas quedan adyacentes sin literal, se inserta `\s+` forzado.
3. **Gravados (5 cols) ignorados por el regex primario** — segunda pasada con regex fallback.
4. **Productos en falta momentánea parseados como ítems** — texto cortado antes del marker `*** PRODUCTOS EN FALTA`.
5. **Caracteres cuadruplicados en negrita** (pdfplumber artifact) — `TTTTOOOOTTTTAAAALLLL` → `TOTAL`. Resuelto con `helpers._normalize_quadrupled`.

## ⚠ Normalización obligatoria para parsers

**Todos los parsers (nuevos y existentes) DEBEN usar `_normalize_quadrupled`.**

```python
from helpers import _normalize_quadrupled

def parse_invoice_pdf(pdf_path):
    pages_text = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            pages_text.append(_normalize_quadrupled(page.extract_text() or ''))
    full_text = '\n'.join(pages_text)
```

### Qué hace la función (3 pasos)

A pesar del nombre, aplica 3 limpiezas de artefactos comunes de pdfplumber:

1. **Caracteres cuadruplicados** — `TTTTOOOOTTTTAAAALLLL` → `TOTAL` (chars repetidos ×4 en fuentes bold de ciertos PDFs como 20 de Junio)
2. **Letter-spacing** — `G r a v a d o I V A` → `GravadoIVA` (dispara con ≥10 tokens de len ≤ 2 seguidos)
3. **Rellenos de puntos** — `Subtotal Bruto................................$` → `Subtotal Bruto $` (4+ puntos consecutivos → espacio)

Las 3 son **idempotentes en líneas normales**. No tocan `1.234,56` (decimal argentino) porque solo tiene 1 punto consecutivo.

### Política para nuevos artefactos

**Si aparece un artefacto nuevo de pdfplumber** (rotación de letras, comillas raras, guiones largos extraños, etc.), **agregarlo como paso nuevo dentro de `_normalize_quadrupled`** en `helpers.py`. No crear funciones separadas — así todos los parsers que ya llaman a esta función heredan la corrección automáticamente.

### Lugares donde ya se aplica

- Template del parser auto-generado (`_generar_codigo_parser` en `routes/converter.py`)
- `converter_detectar` / `converter_pick` / `converter_infer` (los 3 lugares donde el converter lee PDF)
- `parsers/droguer_a_kellerhoff_s_a.py` (existente)

Al agregar/migrar un parser **verificá que esté el import + llamada por página**.

## Pipeline automático al cargar /pick

Al entrar a `/converter/<token>/pick`, JS corre `runAutoPipeline()` que en cascada:

1. **Divide el texto en secciones** (encabezado / detalle / totales) si los markers están
2. **Elige la 1ra línea con pinta de ítem** del detalle y la pone como fila de ejemplo
3. **Auto-detecta matemáticamente** los campos de esa fila (cant × unit = imp, pub × (1-dto%) = unit)
4. **Infiere el patrón** si quedaron al menos cantidad + importe asignados
5. **Auto-detecta la fila de totales** del pie y corre la deducción matemática

Muestra un **banner azul** arriba del panel derecho listando qué pasos funcionaron. El usuario revisa, corrige si algo quedó raro, y confirma.

Si cualquier paso falla, corta la cascada sin romper la pantalla — el usuario completa a mano desde ese punto.

### Campos que SÍ se auto-extraen por etiquetas (bajo riesgo)

- `total`, `monto_exento`, `monto_gravado`, `iva_21`, `iva_105`, `percepciones` — labels inequívocos
- `cantidad_total` — de "Unidades en comprob: N"
- `numero`, `fecha` — de "FACTURA Nº:" / "FECHA:"

### Campos que NO se auto-extraen (riesgo alto)

- `razon_social` — en la factura aparecen DOS razones sociales: la del proveedor Y la del cliente (la farmacia). El usuario debe elegir con el chip cuál corresponde.
- `cuit` — mismo problema: hay CUIT del proveedor + CUIT del cliente.

Si algún día se quiere auto-extraer con más inteligencia, hay que buscar la razon_social que esté cerca del CUIT del header (primeras ~20 líneas) y explícitamente saltear líneas con "Cliente:", "Sr.", "Factura para", etc.

## Shortcuts / atajos de UI

- **Ctrl+click** en group → selecciona rango contiguo desde el último clickeado
- **Triple-click** en línea del PDF → selección de línea entera (nativo del browser)
- **Click en línea del Detalle** (panel dividido) → la usa como fila de ejemplo directamente
- **Enter en input** → blur automático que re-formatea y re-valida
