# Módulo Kellerhoff — análisis y trampas medidas (2026-09-15)

Análisis del ciclo de compra con Kellerhoff: scraping del portal →
`Invoice`/`InvoiceItem` → cuenta corriente. La primera sección está **medida
contra producción**; el resto está marcado como no verificado.

---

## ⚠️ LA TRAMPA: "arreglar" la factura fantasma rompe el saldo

**Leer antes de tocar `_crear_pago_ajuste_nc` (`routes/kellerhoff_sync.py`).**

Su docstring dice, tajante:

> NC financiera (recupero de publicidad/descuento de un anunciante) →
> PagoAjusteCC vinculado a Anunciante, **NUNCA a Invoice/InvoiceItem**.

En producción hay **321 facturas NCR con 0 renglones** que contradicen eso. Se
crean en la *segunda* corrida del sync: `nros_ya_completos()` mete el número en
el set de skip, el scraper fuerza `categoria='factura'` para todo lo skipeado, y
`_sincronizar` ya no la reconoce como `nc_financiera` → cae a
`_get_or_create_invoice` y crea una `Invoice` NCR provisional.

**Parece un bug. Es lo único que hace que el saldo dé bien.**

La cadena, verificada:

1. Las 321 `PagoAjusteCC` tienen **`proveedor_id` NULL** (usan `anunciante_id`).
2. `movimientos_proveedor` filtra por `proveedor_id`, así que **esas filas no
   entran en ningún extracto de proveedor**.
3. Entonces la `Invoice` NCR fantasma es el **único** camino por el que esas NC
   llegan al saldo de Kellerhoff.
4. `clasificar_comprobante` usa `abs(total)` y manda NCR al haber — el signo
   llega bien sin importar cómo esté guardado el total.

Medido sobre el saldo de Kellerhoff:

| | |
|---|---|
| Saldo actual | **$1.400.802.885** |
| Si se "corrige" el bug | $1.520.270.516 |
| **Diferencia** | **+$119.467.631 en contra de la farmacia** |

Regla de negocio confirmada por el dueño: **una NC siempre suma plata a favor de
la farmacia, al revés que una factura.** El haber está bien.

**Qué hacer**: no borrar las fantasma ni "arreglar" el camino. Hacerlo
**deliberado**: que la NC financiera cree la `Invoice` NCR a propósito, corregir
el docstring, y dejar un test que fije el invariante *"una NC de recupero suma al
haber del proveedor"*. Hoy no hay ningún test que cubra esto — por eso el
"arreglo" pasaría verde.

**Pendiente de negocio**: el `AJUSTE_NEG` sobre el anunciante lleva el comentario
*"Convención por defecto (sin validar con el usuario). Revisar si en la práctica
el signo esperado es el opuesto"*. Son 321 filas, $119,5M, todas del mismo signo.
Falta definir si la deuda del laboratorio con la farmacia **baja o sube** cuando
Kellerhoff acredita el recupero.

---

## Seis meses de compras sin detalle de producto

Cobertura de `factura_items` en las facturas de Kellerhoff:

| Mes | Facturas | Con detalle | % |
|---|---|---|---|
| ene–jun | 2.871 | **0** | **0%** |
| jul | 249 | 229 | 92% |
| ago | 203 | 198 | 97,5% |
| sep | 173 | 173 | 100% |

El corte es exacto, así que **no es un bug**: el backfill por PDF masivo
(`services/kellerhoff_bulk_pdf.py`) se corrió para julio en adelante y nunca para
el primer semestre. El sync del portal sólo alcanza ~60 días.

Son **$2.491.379.983** de compras sin detalle, y es la causa directa de que la
cobertura del módulo de rentabilidad dé 9%. El archivo no está en el servidor:
`/root/appfarmweb/uploads` tiene 484 KB y son sólo resúmenes semanales.

**Qué falta**: bajar del portal el export masivo de comprobantes de enero a junio
y subirlo a `/kellerhoff/backfill`. El código ya funciona.

No es sólo Kellerhoff: **20 de Junio** ($441M), **Rosfar** ($231M) y **Del Sud**
($57M) están igual, aunque para esos habría que ver si hay un camino equivalente.

---

## Lo demás (NO verificado contra producción)

El hallazgo de la factura fantasma resultó ser **lo contrario** de lo que parecía
leyendo el código. Conviene medir antes de actuar sobre cualquiera de estos.

### Scraping

- **`_ir_a_detalle` espera un selector que ya está en el DOM**
  (`kellerhoff_scraper.py:279`). Al encadenar comprobantes, `wait_for_selector`
  sobre `button[onclick*="generarPDF"]` retorna al instante porque el botón quedó
  del detalle anterior. Si el POST todavía no reemplazó la tabla, se scrapean los
  ítems del comprobante **anterior**. Falla silenciosa y no determinista: los
  datos quedan "plausibles". Además la espera busca `button` y la descarga busca
  `a[onclick*="generarPDF"]` — los dos no pueden estar bien.
- **Índices posicionales frágiles** (`t[-4]`..`t[-1]` en `_detalle_via_html`,
  `t[4]`..`t[11]` en `_listar_comprobantes`). Si el portal agrega una columna,
  `_parse_dec_ar` devuelve `0.0` en silencio y se guardan importes en cero sin
  excepción ni log. **No hay ninguna validación aritmética** del tipo
  `cantidad × precio_unitario ≈ importe`, que atajaría casi todos estos
  corrimientos (el conversor sí la tiene).
- 4 `page.screenshot('/tmp/kh_0*.png')` **incondicionales** en producción.

### Seguridad

- **`/kellerhoff/sync/ejecutar` no tiene `@login_required`**
  (`kellerhoff_sync.py:184`). El gate es condicional a `AUTO_SYNC_TOKEN`, que
  está vacío en producción (el propio comentario lo dice). Cualquiera con la URL
  dispara un Playwright headless y escrituras en DB.

### Consistencia

- **Signo de NCR en los renglones**: el sync y el backfill guardan `importe`
  crudo (positivo); `data_extract.py` y los dos caminos de `converter.py`
  multiplican por `sign`. En una NCR del portal, `Invoice.total < 0` pero
  `sum(InvoiceItem.importe) > 0`. Cualquier control que sume renglones contra el
  encabezado va a dar mal.
- **4 parsers de ítems distintos** con cobertura distinta. El sync, cuando cae al
  PDF, pierde la sección `*** PRODUCTOS GRAVADOS ***` (perfumería) que el backfill
  sí parsea. Misma factura, dos caminos, dos resultados.
- **`like '%39756490%'` repetido 6 veces** como identificador de Kellerhoff,
  conviviendo con `_kh_provider()` que lo resuelve bien por CUIT normalizado.
- **Los dos módulos de lock** (`kellerhoff_sync.py:49-93` y
  `kellerhoff_backfill.py:32-82`) son copia literal.
- `datetime.now()` (UTC del contenedor) donde el resto usa `now_ar()` → el
  "Último sync" se muestra 3 horas adelantado.

### Cobertura de tests

Sin **ningún** test: `_sincronizar`, `_get_or_create_invoice`, `_crear_items`,
`_crear_pago_ajuste_nc`, `nros_ya_completos`, `_match_pedido`, los tres
normalizadores de número de comprobante, `_detalle_via_html`,
`_listar_comprobantes`, y `routes/kellerhoff.py` entero.

`_detalle_via_html` y `_listar_comprobantes` son **testeables puros con un fixture
HTML, sin Playwright** — y son el punto más frágil del módulo.

Nota sobre `test_kellerhoff_synclock.py`: corre en SQLite y es secuencial, así que
verifica la condición del `WHERE` pero **no** la propiedad que el lock promete
(que dos workers concurrentes no lo tomen los dos).
