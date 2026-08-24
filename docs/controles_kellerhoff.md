# Controles contra Kellerhoff — diseño

_Creado: 2026-08-24. Todos los números salen de correr contra el ObServer y el
portal de Badia ese día; están para saber si un cambio movió algo._

> 🗺️ Para ubicarte: [`docs/MAPA.generado.md`](MAPA.generado.md).
> Trampas del dominio ObServer: [`CLAUDE.md`](../CLAUDE.md).
> Pedidos al admin de SQL: [`observer_pedidos_admin.md`](observer_pedidos_admin.md).

---

## La idea

Cinco fuentes distintas dicen algo sobre la misma compra. Hoy no se cruzan
entre sí, así que un faltante, un cobro de más o mercadería que nunca llegó se
descubren de casualidad. La cadena:

```
1. Pedido  →  2. Factura  →  3. Cuenta corriente
                   ↓
              4. Ingreso de mercadería (ObServer)
                   ↓
              5. Resumen semanal (Kellerhoff)
```

### Estado de cada eslabón

| # | Eslabón | Estado | Dónde vive |
|---|---|---|---|
| 1 | **Pedido** | ✅ existe | `PedidoEmitido`; `/kellerhoff/sync` lista los ABIERTO sin factura |
| 2 | **Factura** | ✅ existe | scraper Playwright sobre `/ctacte/ConsultaDeComprobantes` → `Invoice` + items, NC financieras, `FacturaFaltante`, liga a pedido con `_match_pedido` |
| 3 | **Cuenta corriente** | ✅ existe | [`services/cuenta_corriente.py`](../services/cuenta_corriente.py), `/cuentas-corrientes` |
| 4 | **Ingreso de mercadería** | ❌ falta | `DW.Recepciones` ya disponible; la UI está escrita y gateada |
| 5 | **Resumen semanal** | ❌ falta | nada todavía |

Tres de cinco están. Faltan los dos extremos.

---

## Las dos mitades no se tocan

Es lo que más ordena el diseño:

| | recorrido | fuente que cierra |
|---|---|---|
| **Unidades** | pedido → ítems de factura → recepción | `DW.Recepciones` |
| **Plata** | total de factura → cuenta corriente → resumen semanal | resumen semanal |

`DW.Recepciones` **no sirve para plata**: `PrecioUnitario` viene en 0 en las
187.555 filas (ver abajo). Y el resumen semanal es la **única** fuente con
vencimientos, que es lo que la cuenta corriente necesita para proyectar pagos.

**La factura es el único punto donde conviven unidades e importes** → es el eje
de la cadena, no un eslabón más.

---

## Eslabón 4 — Ingreso de mercadería

### Dónde está el dato

| tabla | filas | |
|---|---|---|
| `Gestion.IngresosEgresosMercaderia` | 108.268 | cabecera |
| `Gestion.IngresosEgresosMercaderiaRenglones` | 781.953 | detalle |
| **`DW.Recepciones`** | **187.555** | **cabecera + renglón ya desnormalizados** |

Usar **`DW.Recepciones`**. Es la vista que pedía
[`observer_pedidos_admin.md` §4](observer_pedidos_admin.md) — ya existe, no hay
que pedirla. Ventana de 2 años exactos (24/8/2024 → hoy), 19.661 recepciones.

> ⚠ **Es lo único portable.** `Gestion.*` y `Proveedores.*` requieren SA, y en
> Badia tenemos SA por excepción. `DW.Recepciones` vive en `DW` → funciona con
> `usuarioDW` en cualquier farmacia. Leer de ahí y no de `Gestion.*`.

### Cobertura real de los campos (187.555 renglones)

| campo | cobertura | |
|---|---|---|
| `CantidadRecepcionada` | ✅ | el dato bueno |
| `NumeroFactura` | 80,1% | pero sucio, ver abajo |
| `CantidadPedida` | 36,7% | |
| `CantidadEnFalta` | 27,7% | se usa de verdad |
| **`PrecioUnitario`** | **0,0%** | **cero en las 187.555** |
| `CantidadDevuelta` | 0,0% | |

O sea: **sirve para cantidades, no para precios.** El ítem de
`Producto.ultima_compra` ([`mejoras_pendientes.md:539`](mejoras_pendientes.md))
no se resuelve por acá. `Proveedores.ComprobantesProveedores` sí tiene
`ImporteTotal`, pero **está muerta desde octubre 2024** (3.469 filas, 0 de
Kellerhoff en agosto 2026).

### Prueba: factura Kellerhoff 0046-00255798 (18/08/2026)

Cruce EAN por EAN contra `Gestion.ProductosCodigosBarras`:

```
Factura PDF   137 renglones · 199 unidades
ObServer      138 renglones · 199 unidades   (IdRecepcion 207663)

137 de 137 matchean, con la cantidad exacta. Cero diferencias.
Los 14 items de "PRODUCTOS EN FALTA MOMENTANEA" NO figuran → bien registrado.
El renglón 138 que sobra es RIVOTRIL 2 mg con TODO en cero (renglón abierto sin usar).
```

Las 199 unidades coinciden con el pie de la factura (`Cant Un: 199`). El cruce
por unidades funciona.

### ⚠ El número de comprobante en ObServer es texto libre y está sucio

Sobre ~400 recepciones de Kellerhoff de los últimos 60 días:

```
solo NumeroRemito,  PV 0047, letra R    116
solo NumeroFactura, PV 9003, letra A     72   ← no es una factura
solo NumeroFactura, PV 0047, letra R     46   ← remito en el campo factura
solo NumeroFactura, PV 9003, letra S     45
solo NumeroFactura, PV 0047, letra A     30   ← remito, con letra A
ninguno                                  22
solo NumeroFactura, PV 0046, letra A      7   ← el único formato "correcto"
```

**Solo 7 de 400 traen el número de factura real.** El resto son remitos o cosas
raras. El operador carga lo que tiene a mano y el campo lo acepta.

Encima, **Kellerhoff y ObServer ponen la letra en lugares distintos**:

```
Kellerhoff   0046A00279207      (PV, letra, número)
ObServer     A004600279207      (letra, PV, número)
```

**Reglas para cualquier cruce:**

1. **Anclar en el remito (PV 0047), no en la factura.** El resumen semanal trae
   la columna `Nro. Remito` al lado del comprobante — es el puente natural.
2. **Matchear por dígitos**, ignorando la letra y su posición.
3. **Buscar en los dos campos** (`NumeroFactura` y `NumeroRemito`): el remito
   aparece indistintamente en cualquiera de los dos.

Sin esto el cruce da falsos negativos en masa: la primera pasada del S34 dio "12
de 23 sin recepción" y era mentira — con las 3 reglas aplicadas quedaron 6.

---

## Eslabón 5 — Resumen semanal

PDF que emite Kellerhoff (`descargaResumenSemanalSap.pdf`), un cierre por semana.
Chico, tabular y **autoconsistente**: los 23 comprobantes del S34-2026 suman
`13.230.715,38` = el TOTAL RESUMEN impreso, y los 3 vencimientos suman lo mismo.
Parsearlo es barato.

Columnas: `Fecha · Comprobante (FAC/NCR) · Nro Cpbte. · Nro. Remito · Total Cpbte.`
más el bloque de vencimientos por fecha y el 1° vencimiento.

Es el **único** control que:
- cierra la **plata** (ninguna otra fuente tiene importes confiables),
- trae **vencimientos** (que la cuenta corriente necesita),
- detecta **una factura que te cobran y nunca recibiste**.

### Prueba: S34-2026 (22–28/08) contra `DW.Recepciones`

| | comprobantes | $ |
|---|---|---|
| Con recepción | 17 | 8.112.595,93 |
| Sin recepción | 6 | 5.118.119,45 |

De los 6 sin recepción: **5 son facturas del 24/08 y el resumen se generó el
24/08 17:51** — la mercadería todavía no se había recepcionado. El sexto es la
NCR, que por definición no genera ingreso.

> ⚠ **El control necesita una ventana de gracia (48–72 h) y excluir las NCR**, o
> marca falsos faltantes todas las semanas. Un comprobante del mismo día del
> corte no es un faltante, es una recepción que todavía no pasó.

---

### El tilde por renglón y el cierre de la semana

`/kellerhoff/resumen/<id>` marca cada renglón con ✓ / ○ y el listado dice si la
semana está **cerrada** o cuántos renglones quedan **sin tildar**. Es el mismo
control que la columna de la cuenta corriente, pero mirado desde el otro lado:
la columna contesta *"¿esta factura la cobraron?"* y el tilde *"¿terminé de
controlar la semana?"*.

El tilde **no es un booleano en la DB**: sale de `estado_item()`, que devuelve un
check por control (`comprobante`, `ingreso`, `arca`, `pago`). Hoy sólo está
implementado el primero; los otros devuelven `None` = *no evaluado*, que **no es
lo mismo que False**, y por eso no bloquean el tilde. Cuando exista el eslabón 4
se completa `ingreso` y el tilde se endurece solo — sin migración y sin tocar la
UI.

> ⚠ **Las NC financieras no crean `Invoice`.** Los recuperos de publicidad los
> manda el sync a `pagos_ajustes_cc` con `anunciante_id`, y con `proveedor_id`
> **NULL** — o sea que ni siquiera aparecen en la cuenta corriente del proveedor.
> Si el tilde sólo mirara `facturas`, **un resumen con un recupero quedaría
> pendiente para siempre** por un comprobante que está perfectamente bien
> procesado. Por eso `ResumenProveedorItem` tiene `pago_ajuste_id` además de
> `factura_id`, y el cruce mira los dos lados.
>
> **Ojo con confundirlas con las NC de mercadería**, que sí son `Invoice` con
> `tipo_comprobante='NCR'` y ligan por el camino normal. En el S34 la NCR de
> −69.124,56 es de mercadería: el resumen liga **23 de 23** contra los datos
> reales de Badia. Las financieras se reconocen por el renglón único
> `RECUPERO NC …` sin barcode (ver `services/kellerhoff_analizador.py`).

---

## Orden recomendado

**1° el eslabón 4 (ingreso).** El más barato: implementar
`observer_source.get_recepciones_factura` leyendo `DW.Recepciones`, y la UI que
ya existe se enciende sola — hoy está gateada con `hasattr`
([`routes/observer.py:34`](../routes/observer.py), `routes/invoices.py:958`,
`templates/compare.html:337`). Reemplaza la subida manual del Excel del ERP y de
paso saca del medio el lío de `erp_carga_id`.

**2° el eslabón 5 (resumen).** Cierra la plata y trae los vencimientos.

> **Hacerlo por `IdProveedor` desde el principio.** Los eslabones 2, 3 y 5 son
> específicos de Kellerhoff (portal y formatos propios), pero **el 4 es
> genérico**: `DW.Recepciones` tiene todos los proveedores. Construirlo
> Kellerhoff-only obliga a rehacerlo para 20 de Junio, y el costo de generalizar
> ahora es casi nulo.

---


## Pendientes

Ordenados por lo que cuesta si no se hace. Todo lo de acá salió de la sesión del
2026-08-24; lo marcado con PR ya está en revisión.

### P0 — Se pueden disparar dos scrapings en paralelo

**El sync de Kellerhoff usa un `threading.Lock` y un dict global**
(`_sync_lock`, `_sync_estado` en [`routes/kellerhoff_sync.py`](../routes/kellerhoff_sync.py)),
y gunicorn corre con **`--workers 2`** ([`Dockerfile:39`](../Dockerfile)). Esas
variables son **una por proceso**, así que:

- El `POST /kellerhoff/sync/ejecutar` cae en el worker A, que arranca el thread y
  escribe el log en **su** copia del estado.
- El polling `GET /kellerhoff/sync/estado` se reparte entre los dos. Cuando cae
  en B devuelve `{corriendo: False}` → **la pantalla no muestra el log ni el
  progreso**. Es el síntoma que se ve.
- El segundo click, si cae en A, devuelve 409 "ya hay un sync en curso". **Si cae
  en B, arranca un SEGUNDO scraping en paralelo**: dos Chromium logueándose al
  portal y escribiendo la misma DB. Eso es el problema real; la UI muda es sólo
  lo visible.

**Ya está resuelto para el otro sync.** [`SyncLock`](../database.py) es un
singleton en DB con `acquire` por UPDATE atómico (`rowcount == 1` = lo tomé) y
timeout de 60 min para locks abandonados; su docstring describe exactamente este
bug. El de Kellerhoff quedó con el patrón viejo.

- [ ] Portar `SyncLock` al sync de Kellerhoff (lock **y** progreso en DB, para
      que los dos workers vean lo mismo). Hoy `SyncLock` asume `id=1`
      hardcodeado para ObServer: hace falta discriminar una fila por sync.
- [ ] Mientras tanto: **no clickear dos veces Sincronizar**.

### P1 — El eslabón 4 sigue sin existir

- [ ] **`observer_source.get_recepciones_factura`** leyendo `DW.Recepciones`. La
      UI ya está escrita y gateada con `hasattr`
      ([`routes/observer.py:34`](../routes/observer.py),
      `routes/invoices.py:958`, `templates/compare.html:337`) — se enciende sola.
      Reemplaza la subida manual del Excel del ERP y saca del medio el lío de
      `erp_carga_id`. **Hacerlo por `IdProveedor`, no Kellerhoff-only.**
- [ ] Al implementarlo: **las NC no tienen remito** (se ve en el S34: es la única
      fila con `—` en esa columna). Como el ancla del cruce es el remito, su
      check de `ingreso` tiene que quedar en **"no aplica"**, no en "falta" — una
      NC no genera ingreso de mercadería. Si no, aparecen como falsos pendientes.

### P1 — Faltantes que no se capturan

- [ ] El camino rápido del scraper (tabla HTML) **no captura la sección
      `*** PRODUCTOS EN FALTA MOMENTÁNEA ***`** — hay un TODO en
      [`_detalle_comprobante`](../services/kellerhoff_scraper.py). Sólo se leen
      por el camino del PDF, que hoy se usa únicamente cuando el HTML no trae
      ítems. La factura 0046-00255798 tiene **14 faltantes** que por ese camino
      se pierden, y los faltantes son justamente lo que se le reclama a la
      droguería. Falta una muestra HTML de una factura con faltantes para saber
      si el dato está en la tabla o hay que bajar el PDF igual.

### P2 — El corte de resúmenes asume continuidad

- [ ] `corte_resumenes` devuelve `max(periodo_hasta)`. Si se importan el S32 y el
      S34 pero se saltea el S33, el corte queda al final del S34 y **toda la
      semana del S33 se pinta ámbar** — el modo de falla que el corte existe para
      evitar. Peor: el tooltip afirma "los resúmenes ya cubren hasta el 28/08",
      que ahí es falso.
      Se detecta comparando el `periodo_desde` de cada resumen contra el
      `periodo_hasta` del anterior. Con eso se excluyen los rangos con hueco, o
      mejor, se muestra un tercer estado: *"falta importar el resumen de esa
      semana"* — que es una acción distinta a *"reclamale a la droguería"*.
      _(De la review del PR #325. No bloqueante hasta el primer hueco real, pero
      el día que pase va a parecer un bug del cruce.)_

### P2 — Columnas que ya se pueden llenar

El tilde de cada renglón (`estado_item`) tiene los checks `ingreso`, `arca` y
`pago` en `None` = *no evaluado*. Dos de los tres no necesitan modelo nuevo:

- [ ] **ARCA**: `Invoice.origen == 'arca'` / `Invoice.cae`, del import de Mis
      Comprobantes.
- [ ] **Pago**: sumar `PagoAplicacion.monto` por factura contra `Invoice.total`
      → impaga / parcial / paga.
- [ ] **Ingreso**: depende del eslabón 4.

Cuando se completen, el tilde se endurece solo — sin migración ni cambios de UI.

### P2 — La pantalla de control de comprobantes

Diseño conversado y **no implementado**: una tabla con **una fila por
comprobante y una columna por fuente que lo confirma** (OBS · RESUMEN · ARCA ·
PAGO), separada del extracto contable —que es una fila por *evento*, con saldo
acumulado— porque responden preguntas distintas y mezclarlas arruina las dos.

Lo que hay que respetar si se construye:

- [ ] **Cada columna con su propio corte**, o marca falsos pendientes y nadie la
      mira más: OBS = hoy − 72 h · RESUMEN = último `periodo_hasta` importado ·
      ARCA = último período importado · PAGO = fecha de vencimiento. Vacío
      **después** del corte es "todavía no toca" (gris), no "falta" (ámbar).
- [ ] La fila **expande** en vez de navegar: el control de ingreso son ~137
      renglones, no entran en una celda, y mandar al operador a otra pantalla por
      cada factura es peor.
- [ ] El botón de control por fila **y** uno masivo. Nadie aprieta 23 botones.

### P2 — Chico y suelto

- [ ] **Card en el home.** Los resúmenes se llegan hoy por Home → Ingresos → 🔄
      Portal Kellerhoff → *Resúmenes de cuenta* (3 clicks). El home tiene su
      inventario propio en [`lista_cards_home_ux.md`](lista_cards_home_ux.md);
      decisión de Diego/Lisandro.
- [ ] **Facturas que se re-navegan siempre.** Tras arreglar `skip_nros` (PR #327),
      una factura cuyo detalle falle *de verdad* se vuelve a bajar en cada sync.
      Es lento, no incorrecto — y ahora es visible en la columna de detalle en vez
      de silencioso. Si molesta, hace falta un contador de intentos.

## Pendiente de verificar

- [ ] **El `.env` apunta a un ObServer que ya no existe.** Migró a `SRV-2K22`,
      **SQL Server 2019** (la doc decía 2014), puerto dinámico **54200**; el
      `.env` local tiene `54572` → conexión rechazada. Confirmar qué tiene el
      `.env` del server de la app (Debian, `192.168.1.220:5000`): si arrastra el
      puerto viejo, el sync está caído ahí también. Es exactamente el riesgo que
      anticipa el punto 1 de [`observer_pedidos_admin.md`](observer_pedidos_admin.md)
      — puerto dinámico, se mueve en cada reinicio.
- [ ] `Gestion.PedidosIGMSinProveedor` + `...Renglones`: por el nombre, el pedido
      **antes** del ingreso. Podría cubrir el eslabón 1 desde ObServer.
- [ ] Si el portal expone el resumen semanal para **descarga automática** (el
      scraper hoy sólo va a `ConsultaDeComprobantes`). Hoy el PDF se sube a mano.
- [ ] Qué son los comprobantes con `PV 9003` letra `A`/`S` (117 casos, el segundo
      formato más común en las recepciones). No son facturas de Kellerhoff.
