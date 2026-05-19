## Reglas de trabajo

1. **Lee antes de escribir** — Leer el archivo completo antes de tocarlo.
2. **Sé conciso pero profundo** — Respuesta directa, sin rodeos.
3. **Edita, no reescribas** — Si solo cambia una línea, cambiar esa línea.
4. **No releas lo que ya leíste** — No releer archivos ya leídos en la conversación salvo que hayan cambiado.
5. **Prueba antes de decir listo** — Verificar que el código funciona antes de declararlo terminado.
6. **Cero relleno y saludos** — Sin openers ni cierres. Directo al grano.
7. **Soluciones simples y directas** — No sobre-diseñar.
8. **El usuario manda** — Las instrucciones del usuario siempre ganan sobre cualquier otra regla.
9. **Precisión > velocidad** — Preferir siempre configuraciones/técnicas que maximicen exactitud aunque tarden más. El tiempo ahorrado procesando rápido se pierde 10x más corrigiendo errores después. Aplica a OCR (DPI alto + preprocessing), regex (anclado y específico, no greedy), parseo numérico (aceptar todos los formatos), auto-detect (preferir falso negativo a falso positivo), validación (bloquear al siguiente paso si faltan campos críticos).
10. **Revisar backlog al iniciar sesión** — Antes de responder al primer mensaje de una sesión nueva, leer `docs/mejoras_pendientes.md`. Si hay items relacionados con lo que el usuario pide, mencionarlos brevemente al inicio de la respuesta ("hay X anotado en backlog que aplica acá"). No re-leer en mensajes siguientes de la misma sesión salvo que el archivo haya cambiado.


# Farmacia - Control de Stock y Reclamos

## Stack

- Backend: `Flask` + `SQLAlchemy`
- DB: `PostgreSQL` vía Docker (también soporta SQLite para dev local)
- Frontend: `Tailwind CSS` CDN, dark theme (`#1c1c1e` fondo, `#2c2c2e` superficie, `#EAB308` acento)
- Contenerización: `Docker` + `docker-compose`
- Server: `gunicorn` (producción)

## Flujo principal

1. Subir factura PDF + Excel ERP → `/upload`
2. **Cruce automático** en 3 pasos: código de barra exacto → descripción normalizada → tabla `barcode_mappings` del proveedor
3. **Página de cruce manual** `/invoice/<id>/compare`: dos tablas lado a lado, el usuario ingresa números para correlacionar ítems no encontrados
4. Al confirmar cruce: actualiza `stock_differences` + guarda en `barcode_mappings` para uso futuro automático
5. `/results/<id>`: seleccionar diferencias y generar reclamo
6. Al generar reclamo: se muestra la vista del reclamo y se descarga el PDF automáticamente
7. PDF nombrado `Reclamo_N{id}_{numero_factura}.pdf`, generado con `reportlab`

## Tablas y modelos (database.py)

| Tabla | Modelo | Descripción |
|-------|--------|-------------|
| `proveedores` | Provider | razon_social, cuit, domicilio, parser_file, **match_strategy** ('barcode'/'descripcion') |
| `facturas` | Invoice | numero_factura, fecha, proveedor_razon, proveedor_cuit, total, total_articulos, total_unidades, pdf_filename, **tipo_comprobante** ('FAC'/'NCR') |
| `factura_items` | InvoiceItem | codigo_barra, cantidad, descripcion, precio_unitario, **dto**, importe, lote, vencimiento |
| `erp_stock` | ErpStock | codigo_barra, descripcion, cantidad, **precio_unitario** (se reemplaza en cada carga) |
| `stock_differences` | StockDifference | diferencias por factura: codigo_barra, descripcion, cantidad_factura, cantidad_erp, diferencia, observaciones |
| `reclamos` | Claim | proveedor_id, factura_id, numero_factura, fecha, estado (ABIERTO/COMPLETADO) |
| `reclamo_items` | ClaimItem | detalle del reclamo, referencia a StockDifference |
| `barcode_mappings` | BarcodeMapping | correspondencias manuales por proveedor: codigo_barra_factura → codigo_barra_erp, UNIQUE(proveedor_id, codigo_barra_factura) |
| `productos` | Producto | tabla master: codigo_barra UNIQUE + codigo_barra_alt1/2/3 (EANs alternativos), descripcion, precio_pvp, actualizado_en |
| `pedidos` | Pedido | pedidos guardados del análisis de compra: laboratorio, farmacia, periodo, n_days, estado (PENDIENTE/etc.) |
| `pedido_items` | PedidoItem | ítems del pedido: codigo_barra, nombre, cantidad, precio_pvp, subtotal |

## Migraciones

Se hacen inline en `init_db()` con `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` (PostgreSQL) o `PRAGMA table_info` + ALTER individual (SQLite). La tabla `barcode_mappings` se crea con `CREATE TABLE IF NOT EXISTS`.

## Archivos clave

| Archivo | Rol |
|---------|-----|
| `app.py` | Flask app factory, config, template filters, registro de rutas |
| `helpers.py` | Constantes compartidas, funciones utilitarias |
| `routes/*.py` | 14 módulos de rutas con patrón `init_app(app)` |
| `database.py` | Modelos SQLAlchemy + `init_db()` con migraciones |
| `data_extract.py` | Parseo de PDF/Excel, comparación en 3 pasos, guardado de diferencias, reclamos, barcode_mappings |
| `parsers/<slug>.py` | Un parser por proveedor, implementa `parse_invoice_pdf(path) → dict` |
| `parsers/_template.py` | Plantilla para nuevos parsers |
| `templates/` | Vistas Jinja2 |

## Templates

| Template | Descripción |
|----------|-------------|
| `index.html` | Pantalla principal con sidebar de navegación izquierdo. Botones FAC/NCR para tipo de comprobante |
| `compare.html` | Cruce manual: tabla factura (izquierda, numerada, P.Unit., checkmark al hacer match) + tabla ERP (derecha, Unit.ERP, Ratio%). Ítems de factura ordenados por descripción |
| `results.html` | Diferencias con checkbox + botón generar reclamo. Badge FAC/NCR. Links a pick_fields y editar encabezado. Botón "Ver todos los artículos" |
| `claim.html` | Vista del reclamo + botón PDF + marcar completado. Auto-descarga PDF al llegar desde "Generar reclamo" |
| `claims_list.html` | Listado de todos los reclamos con filtro por estado |
| `providers.html` | CRUD de proveedores: editar (incl. match_strategy), eliminar, ver facturas, ver equivalencias |
| `provider_invoices.html` | Facturas del proveedor: columna Tipo (badge FAC verde/NCR rojo), total verde FAC/rojo NCR |
| `provider_mappings.html` | Tabla de equivalencias de códigos de barra del proveedor, con opción eliminar por registro |
| `pick_fields.html` | Field picker: texto PDF a la izquierda, campos a la derecha. Selección con mouse + `window.getSelection()` |
| `invoice_items.html` | Tabla de todos los ítems de una factura (codigo_barra, descripcion, cantidad, precio_unitario, importe, lote, vencimiento) |
| `orders_list.html` | Lista de pedidos guardados con `<details>` colapsable por pedido. Key de ítems usa `'productos'` (no `'items'` — colisiona con `dict.items()` en Jinja) |
| `order_detail.html` | Análisis de módulos/ofertas en 3 pasos (step-card): módulos → confirmar → ofertas → resumen. Export XLSX/PDF por paso. Panel "Match manual" dos columnas estilo compare.html |
| `productos.html` | Tabla master de productos con filtro por descripción/código/alts y toggle "solo con EAN alt". Ordenada por descripción |

## Rutas

### Procesamiento
- `POST /upload` → procesa PDF + Excel, redirige a `/invoice/<id>/compare`. Si parser devuelve 0 ítems → error con flash y redirect a index
- `GET /invoice/<id>/compare` → cruce manual
- `GET /invoice/<id>/items` → tabla de todos los artículos de la factura
- `POST /invoice/<id>/apply-mapping` → aplica cruce, guarda barcode_mappings, redirige a results
- `GET /results/<id>` → diferencias + generar reclamo
- `POST /invoice/<id>/delete` → elimina factura y todo lo asociado en cascada

### Encabezado de factura
- `POST /invoice/<id>/header` → editar campos manualmente
- `GET /invoice/<id>/pick-fields` → field picker (texto PDF + asignación por selección)
- `POST /invoice/<id>/pick-fields` → guardar campos del picker

### Reclamos
- `POST /claim/create` → crea reclamo, renderiza claim.html con auto_download=True
- `GET /claim/<id>` → ver reclamo
- `POST /claim/<id>/complete` → marcar completado
- `GET /claim/<id>/pdf` → genera y descarga PDF con reportlab
- `GET /claims` → listado de todos los reclamos

### Proveedores
- `GET /providers` → lista con CRUD inline
- `POST /provider/<id>/edit` → editar
- `POST /provider/<id>/delete` → elimina proveedor + claims + barcode_mappings en cascada
- `GET /provider/<id>/invoices` → facturas del proveedor
- `GET /provider/<id>/mappings` → tabla de equivalencias
- `POST /provider/<id>/mappings/<mapping_id>/delete` → eliminar equivalencia

### Pedidos y análisis de módulos/ofertas
- `GET /orders` → lista de pedidos guardados
- `POST /purchase/save-order/<uid>` → guarda pedido desde análisis de compra
- `POST /order/<id>/delete` → elimina pedido en cascada
- `GET /order/<id>` → pantalla análisis módulos/ofertas
- `POST /order/<id>/parse-modules` → parsea Excel de módulos, devuelve JSON
- `POST /order/<id>/parse-offers` → parsea Excel de ofertas, devuelve JSON
- `POST /order/<id>/save-module-matches` → recibe `{matches: [{module_ean, pedido_barcode, pedido_nombre}]}` y crea equivalencias en tabla Producto
- `POST /order/<id>/export/<step>/<fmt>` → export XLSX (step: modules|offers|summary)
- `GET /productos` → tabla master con filtro

### API
- `POST /api/upload`, `GET /api/invoice/<id>/differences`, `POST /api/claims`, `GET /health`

## Parsers activos

| Archivo | Proveedor | CUIT | match_strategy |
|---------|-----------|------|----------------|
| `droguer_a_kellerhoff_s_a.py` | Droguería Kellerhoff | — | barcode |
| `pharmos.py` | Pharmos | 30-64266156-2 | descripcion (usa refs internas como barcode) |
| `20_de_junio.py` | 20 de Junio | 23-17460511-4 | barcode |

### ⚠ OBLIGATORIO: normalización de texto PDF

**Todos los parsers (nuevos y existentes) deben usar `_normalize_quadrupled` de `helpers.py`** al extraer texto con pdfplumber. A pesar del nombre, hace **3 limpiezas** de artefactos comunes:

1. **Caracteres cuadruplicados** (fuentes en negrita en 20 de Junio, etc.) — `"TTTTOOOOTTTTAAAALLLL"` → `"TOTAL"`
2. **Letter-spacing** (cada carácter separado por un espacio) — `"G r a v a d o I V A"` → `"GravadoIVA"`. Dispara cuando hay ≥10 tokens cortos en secuencia.
3. **Rellenos de puntos** (separadores visuales) — `"Base Exenta..........$"` → `"Base Exenta $"`. Dispara con 4+ puntos consecutivos.

Las 3 son idempotentes en líneas normales (no afectan decimales como `1.234,56`).

Pattern en **todos los parsers**:

```python
from helpers import _normalize_quadrupled

def parse_invoice_pdf(pdf_path):
    pages_text = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            pages_text.append(_normalize_quadrupled(page.extract_text() or ''))
    full_text = '\n'.join(pages_text)
    # ... regex sobre full_text
```

- El template del parser auto-generado (`_generar_codigo_parser` en `routes/converter.py`) ya lo incluye → cualquier parser nuevo lo hereda
- Si escribís un parser manual, **no te olvides de agregar el import y llamarlo por cada página**
- Si migrás un parser viejo que no lo tenía, agregalo — es seguro por default

**Si descubrís otro artefacto recurrente de pdfplumber** (ej. rotaciones de caracteres, comillas raras, etc.), agregarlo como paso nuevo en `_normalize_quadrupled` en `helpers.py` — así automáticamente se propaga a todos los parsers que ya llaman a esta función.

### Formato Kellerhoff
`BARCODE CANT DESC PRECIO_PUB %DTO PRECIO_UNIT IMPORTE` — regex con grupos.

### Formato Pharmos
Sin barcodes reales. Usa códigos internos tipo `79-65` como codigo_barra. Regex: `^(\d{2}-\d+)\s+...`

### Formato 20 de Junio
`CANT DESC LABO [OBS] BARCODE SUGG [DTO NETO] [FLAG] IMPORTE`
- IMPORTE siempre tiene puntos de miles (ej: 34.338,97); precios unitarios no tienen (34338,97)
- TOTAL NETO en encabezado usa caracteres cuadruplicados (TTTT...) → unduplicate cada 4 chars
- precio_unitario = penúltimo número; importe = último

## Lógica de compare_invoice_vs_erp

- `match_strategy='barcode'`: barcode exacto → descripción normalizada → barcode_mappings
- `match_strategy='descripcion'`: descripción normalizada → barcode exacto → barcode_mappings
- El Ratio en compare.html: `(unitErp - pUnit) / unitErp * 100` — positivo verde (ERP mayor), negativo rojo

## Notas importantes

- `app.config['TEMPLATES_AUTO_RELOAD'] = True` activo → los templates se recargan sin restart al cambiar archivos.
- El volumen Docker está montado (`./:/app`) → cambios en `.py` sí requieren `docker-compose restart web`. Templates no.
- `docker-compose.yml` tiene healthcheck en `db` con `pg_isready` para evitar race condition al arrancar.
- Al eliminar proveedor: borrar en orden → ClaimItems → Claims → BarcodeMapping → Provider.
- `parse_invoice_pdf` depende del parser del proveedor. Cada proveedor tiene su `parsers/<slug>.py`.
- El field picker usa `window.getSelection().toString()` para capturar texto seleccionado del PDF.
- Validación post-parseo: si `items` vacío → flash error y redirect a index (no se guarda nada en DB).
- `tipo_comprobante='NCR'` → sign=-1, los montos se guardan negativos en DB. `tipo_comprobante='FAC'` → sign=1 (default).
- `dto` en `InvoiceItem` es DECIMAL(6,2), se guarda solo si el parser lo extrae.

## Tabla Producto (master)

- Se puebla automáticamente desde: `process_upload` (ErpStock + InvoiceItems), `apply_mapping` (upsert ERP + add alt), `purchase_save_order` (ítems del pedido), `order_save_module_matches` (match manual).
- Helpers en `helpers.py`: `_upsert_producto()` y `_add_alt_barcode()` (no duplica alts, no mete el mismo barcode).
- Equivalencias multi-barcode: al buscar un EAN contra pedido, si ese EAN está en alt1/2/3 de un producto del pedido, se encuentra.

## Match manual módulos (order_detail.html)

- Panel de dos columnas: izquierda ítems del módulo sin match (dedup por EAN, alfa), derecha ítems del pedido numerados.
- Usuario tipea N° y `guardarMatches()` POST a `/order/<id>/save-module-matches` → server upserta + devuelve equiv → JS re-propaga pedidoQty/pedidoNom y re-procesa módulos.
- Scroll: `max-height:520px` + `overflow-y-auto` en cada columna (NO en el grid padre — no se aplica a las hijas). Headers sticky con `z-10`.

## Gotchas

- En Jinja, `dict.items` resuelve al método `items()` del dict, no a la key `'items'`. Renombrar la key a `'productos'` u otra.
- `order_save_module_matches` recibe `{matches: [...]}` como JSON, no array directo: usar `body.get('matches', [])`.

## Estado de features — order_detail.html (Abril 2026)

### Implementado
- **Paso 3 ofertas c/mín**: muestra TODOS los grupos del Excel Bernabó (no solo los con saldo). Fila individual se pone verde + ✓ al llegar al mínimo. Botón "Completar" por producto individual.
- **Auto-carga ofertas c/mín**: al llegar al paso 3 carga automáticamente desde DB via `/api/laboratorio/<lab_id>/ofertas-minimo` (campo `lab_id` en `Pedido` resuelto en `order_detail`).
- **Gráfico histórico**: botón 📊 (`CHART_BTN(ean)`) en todas las tablas del análisis. Modal Chart.js v4. Proyección mes actual suavizada: `pf = ps + (avg/dim)*(dim-de)`.
- **Plantilla export por laboratorio**: modelo `ExportTemplate` en DB, ruta `/laboratorio/<lab_id>/export-template`, template `export_template.html` con drag-and-drop. Botón "Exportar plantilla" en resumen usa ese formato vía `/order/<id>/export/summary/xlsx`.
- **Ofertas c/mín guardadas**: modelo `OfertaMinimo` en DB. API `GET/POST /api/laboratorio/<lab_id>/ofertas-minimo`. Botón "Guardar en sistema" en `laboratorios.html`.
- **Plantilla modulo_packs**: columnas correctas (NOMBRE MÓDULO / EAN / DESCRIPCIÓN / CANT. MÓDULO / DESC. %), sin título ni instrucciones extra.
- **Fix `cargarModuloActivo`**: keys corregidas a `ean`, `cant`, `desc_pct` (antes eran `ean_modulo`, `cantidad`, `descuento` → todo salía "undefined").
- **Resumen paso 4**: columna Producto angosta (`min-width:150px`). Badge verde `2u/pk` cuando el producto forma parte de un pack (`UNIT_TO_PACK` reverse map de `MODULO_PACKS`).

### Modelos DB nuevos
| Modelo | Tabla | Descripción |
|--------|-------|-------------|
| `ExportTemplate` | `export_templates` | PK=laboratorio_id, columns_json, custom_header |
| `OfertaMinimo` | `ofertas_minimo` | ean, descripcion, codigo, unidades_minima, descuento_psl, rentabilidad, plazo_pago, grupo_id, laboratorio_id FK |

### Pendiente
- Revisar si la plantilla de **módulos de descuento** (la que sube descuentos, distinta a modulo_packs) está desactualizada.

## Arquitectura híbrida local ↔ Render

La app corre en Render. Para operaciones que requieren acceso al filesystem de la farmacia (ej. PDFs en carpetas locales), usamos una app de escritorio local: `DockerPanel/`. Dos mecanismos complementarios:

| | Agente pendientes (push) | HTTP Helper (pull) |
|---|---|---|
| Dirección | Local → Render | Render → Local |
| Trigger | Botón "Subir PDFs a Render" en el panel | Fetch desde frontend Render |
| Endpoint | POST `{render}/docs-pendientes/upload-api` | GET `localhost:5055/ping`, `/folder-files`, `/read-pdf` |

Archivos en `DockerPanel/`:
- `docker_panel.py` — GUI tkinter (comandos Docker, backup/restore, agente, helper HTTP embebido en thread)
- `agente_pendientes.py` — script standalone CLI (subprocess desde el panel)
- `agente_config.txt` — config local (carpeta/url/mover), **ignorado en git**

Los bloques del HTTP helper dentro de `docker_panel.py` están marcados con `# === BEGIN HELPER HTTP (copy to unified panel) ===` / `# === END HELPER HTTP ===` para facilitar copiar entre máquinas. Son 5 bloques.

## Plantillas de exportación — Laboratorio vs Proveedor

Dos sistemas separados, **intencionalmente NO unificados**:

| | Lab | Proveedor |
|---|---|---|
| Modelo | `ExportTemplate` | `PlantillaExportacion` + `PlantillaCampo` |
| Formato | XLSX (columnas custom) | TXT ancho fijo (col_inicio, longitud, alineación, relleno) |
| Config UI | `/laboratorio/<id>/export-template` | `/provider/<id>/plantilla` |
| Export | `/order/<id>/export/plantilla` | `/order/<id>/export-prov-plantilla` |

En order_detail.html (resumen) aparecen botones separados "Plantilla laboratorio" y "Plantilla proveedor" solo cuando la plantilla correspondiente existe.

`CAMPOS_SISTEMA` (database.py) y `EXPORT_FIELDS` (routes/laboratorios.py) tienen el mismo set de campos (ean/codigo_barra, nombre/descripcion, total/cantidad, cant_modulo, cant_oferta, cant_oferta_min, cant_nodeal, precio/precio_pvp, erp_qty, rotacion, avg_monthly + fijo/espacio en proveedor).

## Conversor de facturas (learn + verify)

Flujo de aprendizaje de parsers PDF sin código, con validación matemática inline y edición row-by-row. Ver `docs/converter_flow.md` para el detalle completo.

Highlights:
- `/converter/<token>/pick` — aprendizaje: divide PDF en 3 secciones, tokeniza filas, asigna campos por chips o dropdown, 🧮 auto-detecta por math (cant×unit=importe, pub×(1-dto%)=unit, IVA = gravado × 21%)
- `/converter/<token>/verify` — validación pre-import: row-by-row con badges ✓/⚠/✗, auto-recálculo al editar, desglose fiscal editable (Exento/Gravado/IVA10,5/IVA21/Percep/Otros), sugerencias automáticas para campos faltantes
- Backend: `_build_item_pattern` (inferencia regex) + `_guardar_factura_desde_aprendizaje` (persist Invoice + breakdown fiscal)
- Campos nuevos en `facturas`: `monto_exento, monto_gravado, iva_105, iva_21, percepciones, otros`

Fixes críticos históricos:
- `\s*$` al final del pattern generado (evita absorber EAN de fila siguiente)
- `\s+` forzado entre capturas numéricas adyacentes sin literal
- Segunda pasada con regex fallback para gravados (5 cols sin pub/dto)
- Corte del texto antes de `*** PRODUCTOS EN FALTA` para no parsear faltantes

## Deploy Render — fix pg_type stale

Antes de `Base.metadata.create_all(engine)` en `init_db` limpiamos tipos huérfanos en `pg_type` para tablas nuevas agregadas recientemente (lista whitelist en database.py). Esto evita `UniqueViolation` cuando un deploy anterior falló mid-stream con `CREATE TABLE`. Al agregar un modelo nuevo, sumá su `__tablename__` a esa lista.

## Deploy Render — gotchas

Reglas duras aprendidas el 2026-05-19 (ver [docs/lecciones_deploy_render.md](docs/lecciones_deploy_render.md) para el detalle):

- **NUNCA agregar `--preload` al CMD de gunicorn**. `init_db()` cuelga al master y Render cancela el deploy con "No open ports detected".
- **NO migrar a `--worker-class gthread`** sin staging completo — rompió port binding sin razón clara.
- **`render.yaml` puede ser pisado por config manual del dashboard** (ej. healthCheckPath). Verificar ambos.
- **Health check**: usar `/ping` (sin DB) y no `/health` (con SELECT 1). Si los workers se cuelgan en queries lentas, /health queda en cola → Render mata el instance.
- **Plan free**: 512MB RAM. Query patterns como `Producto.all()` en pantallas que iteran ~30k filas → OOM.
- Si deploy falla con "Port scan timeout" sin output de gunicorn → es preload colgado, no error de código. Rollback inmediato y debug después.

## LLM matcher (queue de pendientes)

`services/llm_matcher.py` llama a Claude Haiku 4.5 para sugerir match en items que el matcher Python no resolvió. UI on-demand en `/productos/pendientes-revision` (botón "🤖 Analizar con IA").

**Setup:**
- Env var `ANTHROPIC_API_KEY` (en Render → Environment). Sin la var seteada el botón se deshabilita y la API tira `RuntimeError` claro.
- API key generada en console.anthropic.com (clave actual: `xls-converter-farm`). Spending limit USD 5/mes.

**Costo:** ~$0.0015/item (Haiku 4.5: $1 input / $5 output por 1M toks). Batch de 230 items ≈ $0.35. El system prompt está cacheado (`cache_control: ephemeral`) pero por debajo del mínimo de Haiku 4.5 (4096 toks) el caché silenciosamente no se activa — por ahora pagamos input completo cada llamada. Se puede engordar el prompt con más ejemplos si vale la pena.

**Endpoints:**
- `POST /productos/pendientes-revision/analizar-ia` — body `limit` (default 5), `forzar_reanalisis` (default false). Itera items con `llm_analizado_en IS NULL`, persiste sugerencia en columnas `llm_*`.
- `GET /productos/pendientes-revision/estimar-costo-ia?limit=N` — pre-flight, devuelve costo USD sin llamar API.
- `POST /productos/pendientes-revision/<id>/aplicar-ia` — aplica sugerencia (vincular/descartar). 'crear_nuevo'/'ambiguo' redirigen al flujo manual.
- `POST /productos/pendientes-revision/aplicar-ia-bulk` — aplica todas las sugerencias con `confidence ≥ min_conf` (form, default 0.9) y action vincular/descartar.

**Columnas en `productos_pendientes_revision`:** `llm_analizado_en`, `llm_pick_producto_id`, `llm_pick_observer_id`, `llm_confidence`, `llm_reasoning`, `llm_action` ('vincular'|'crear_nuevo'|'descartar'|'ambiguo'), `llm_modelo_usado`. Migración inline en `init_db()` con ALTER TABLE IF NOT EXISTS por columna.

**Operador:** las acciones disparadas por IA se loggean como `usuario_resuelve='IA→<usuario_actual>'` para audit trail.
