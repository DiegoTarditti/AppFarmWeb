# Backlog — Robot Rowa

_Creado: 2026-08-24. Sólo el módulo del robot. Lo transversal de AppFarmWeb vive
en [`backlog_urgente.md`](backlog_urgente.md)._

Orden: **P0** = está roto o se pierde plata AHORA · **P1** = correctitud o
capacidad sin usar · **P2** = deuda que acumula.

> 🗺️ Para ubicarte: [`docs/MAPA.generado.md`](MAPA.generado.md).
> Código: [`routes/rowa.py`](../routes/rowa.py),
> [`services/rowa_client.py`](../services/rowa_client.py) (WWKS2),
> [`services/rowa_analisis.py`](../services/rowa_analisis.py) (lógica pura),
> [`services/rowa_observer.py`](../services/rowa_observer.py) (cruce por EAN).

---

## Números de referencia (24/8/2026)

Todo lo de acá abajo sale de correr contra el robot y ObServer de Badia. Sirven
para saber si un cambio movió algo.

```
Robot                3.510 artículos · 10.344 packs · 26 snapshots en ~4 días
Cruzan con ObServer  3.454 (98,4%)
Packs NotAvailable   9  → descartan 2 artículos enteros, en silencio
Capacidad definida   3.451 de 3.454 artículos
Lugar libre          11.458 packs en 2.987 artículos
Exceden su cupo      1 (TALPRAM 20mg: 8 contra 6)
Robot > ObServer     24 artículos · 34 packs · 5 con ObServer en CERO
```

---

## ✅ Hecho (agosto 2026)

**`/rowa` mostraba el 13% del stock.** La tabla iteraba sobre `accion`
(`al_deposito > 0`): 454 de 3.508 artículos. Los otros 3.053 se leían del robot,
se cruzaban con ObServer, se calculaban enteros y se descartaban al dibujar. La
lectura estaba bien —`stock_info()` pide `<Criteria/>` vacío y trae todo—, era la
vista. Ahora hay interruptor: default como antes, `?todo=1` para el inventario
completo. Por URL y no client-side porque las ~3.500 filas son unos 4 MB de HTML.
_(PR #316)_

**El cálculo de salidas subestimaba 3,4×.** `_calcular_salidas_diarias`
comparaba el **primer** snapshot contra el **último** y le sumaba las cargas
registradas. Un artículo que bajó de 10 a 2 y se repuso a 10 daba **cero
salidas**: los extremos son iguales. Ahora suma las **bajas entre snapshots
consecutivos**, que además no depende de que nadie registre las cargas.

```
por extremos      427 unidades   3.060 artículos con 0 salidas (90%)
sumando bajas   1.469 unidades   2.631 artículos con 0 salidas (77%)
```

Efecto: los críticos pasaron de 126 a 222 (+76%). Era demanda real tapada. _(PR #317)_

**`/rowa/carga` ordenada por laboratorio + export PDF/Excel.** Ordenaba por
urgencia y cobertura; para ir a buscar la mercadería al depósito conviene
recorrer un laboratorio a la vez. En las exportaciones el laboratorio **deja de
ser columna y pasa a ser título de sección** — en papel, una columna repetida 300
veces no separa nada. 3.500 items en 180 grupos: xlsx 158 KB en 2,2 s, pdf 369 KB
en 1,9 s. _(PR #317)_

**`/rowa/carga` renderizaba 3.500 filas para mostrar 222.** El checkbox "Solo
críticos", tildado de fábrica, escondía el 96% del lado del navegador. Ahora el
corte lo hace el servidor. _(PR #317)_

**Filtros en `/rowa/analisis`.** Tenía sólo laboratorio; se agregaron buscador
por nombre y filtro por rotación. `porLab()` pasó a `filtrados()` y sigue siendo
el único punto de filtrado, así que el scatter y la tabla muestran siempre lo
mismo. _(PR #318)_

**Botón "Revisar diferencias".** Detecta `robot > ObServer`, que no debería pasar
nunca. _(PR #320)_

**Vencimientos cortaba en 50 en silencio.** Ahora corta en 200 y avisa. _(PR #316)_

**Primeros tests del módulo.** No había ninguno, y por eso no se detectó que
`_construir_items` había quedado fuera del alcance de `_calcular_salidas_diarias`
— `/rowa/carga` y las dos exportaciones habrían tirado `NameError`. Se agregaron
10 tests del exportador (lógica pura, sin robot ni DB). _(PR #317)_

---

## P0 — El robot está medio vacío y nada avisa

**`sug_cargar` da CERO en los 3.501 artículos.** La columna "Cargar" de
`/rowa/carga` es estructuralmente siempre cero: la pantalla que existe para
decidir qué reponer no puede responder su propia pregunta.

El motivo está en [`_recomendar()`](../services/rowa_analisis.py): sólo sabe
**reducir**. Durmiente → dejar 1, Baja → dejar la mitad, y para Alta/Media
devuelve `sug_en_robot = cantidad`, o sea "dejá lo que hay". Nunca fija un
objetivo, y como `sug_cargar = sug_en_robot − cantidad`, no puede dar positivo.

Mientras tanto hay **11.458 lugares libres**.

**Lo que falta es un mínimo del robot, que es otra cosa que el mínimo total:**

| | mínimo total | mínimo en robot |
|---|---|---|
| pregunta | ¿cuándo le compro al proveedor? | ¿cuánto de lo que tengo va adentro? |
| lo maneja | lead time del proveedor | frecuencia de recarga |
| recurso escaso | plata | **espacio** |
| dónde vive | `obs_stock.minimo` | **no existe** |

La fórmula tiene todo lo necesario ya andando:

```
minimo_robot = min(salidas_dia × dias_autonomia, CantidadMaxima)
```

Simulado, acotado por el cupo real:

| días | artículos a reponer | packs a cargar |
|---|---|---|
| 7 | 215 | 406 |
| 10 | 398 | 826 |
| 15 | 475 | 1.122 |
| 21 | 596 | 1.851 |
| 30 | 598 | 1.873 |

Entre 21 y 30 casi no cambia: ahí ya manda el techo físico y no la demanda. El
punto de rendimiento decreciente está cerca de los **21 días**.

**Bloqueado por una decisión operativa**: cuántos días de autonomía se quiere que
tenga el robot. Es de Diego/Lisandro, no técnica.

---

## P1 — ObServer tiene un módulo Rowa que no estamos leyendo

Apareció el 24/8/2026 mirando una captura de la pantalla de ObServer. Existe
todo esto y nunca lo tocamos:

```
Varios.Rowa_Configuracion
Varios.Rowa_Ingresos       + Rowa_IngresosVista
Varios.Rowa_Productos      + Rowa_ProductosDto
Varios.Rowa_Solicitudes
```

**`Varios.Rowa_Productos` tiene la capacidad por artículo**, que es justo lo que
el robot NO expone (`MaxSubItemQuantity` viene vacío en los 3.510):

| campo | | |
|---|---|---|
| `IdProducto` | | 29.419 filas |
| `CantidadMaxima` | capacidad del robot | **13.222 cargadas** |
| `NuevaCantidadMaxima` | la columna editable de la pantalla | 0 usadas |

No es un default: `FW_Version` llega hasta **180** y la cargaron **7 usuarios**
distintos entre 2023 y 2025. Es una decisión humana sostenida, aunque **gruesa**
— el 95% de los valores son sólo 6, 5 o 2, o sea clasificación por tamaño de
envase. Eso está bien para un techo (cuánto entra), no para un objetivo (cuánto
se vende).

**Trampa que ya costó una vuelta:** `obs_stock.maximo` **no es** ese campo. Lo
leemos de `DW.StockFarmaciasProductos.Maximo`, la misma fila de donde salen
`Stock` y `Minimo` (que sí coinciden con la pantalla de ObServer). Para LOSACOR
da 18/7/1/1/0/0 contra los 10 y 6 reales. Usarlo como techo pone un límite
equivocado.

- [ ] **Sumar `Varios.Rowa_Productos` al sync.** Barato, sólo lectura, y
      desbloquea el techo del P0.
- [ ] Investigar `Rowa_Ingresos` y `Rowa_Solicitudes`: los nombres sugieren que
      el flujo estaba pensado en los dos sentidos.

### Y ObServer espera datos que nosotros tenemos

En esa misma pantalla, las columnas **`Stock Robot`** y **`Próximo Venc.`**
muestran `???` y `??????????`. No es un error: son campos esperando que alguien
los complete, y nosotros tenemos los dos en vivo — stock por artículo y
vencimiento por pack, con `expiry_source` para saber cuáles son confiables.

- [ ] Evaluar escribirle a ObServer. **Es un paso serio**: hoy ObServer es de
      sólo lectura para nosotros. Antes hay que entender qué proceso consume
      `NuevaCantidadMaxima` y si la aplica sola o la revisa alguien.

---

## P1 — Nadie registra las cargas

**Cero `RowaCarga` en 14 días.** El botón de registrar carga existe y no se usa.

Eso deja sin funcionar la detección de `tipo_aumento` (el filtro "Solo con
aumentos" y la clasificación carga / carga_parcial / ingreso_detectado), que
depende de comparar snapshots contra cargas registradas.

El arreglo del cálculo de salidas ya no depende de esto —los aumentos
simplemente no cuentan como salida—, pero mientras nadie registre no se puede
distinguir "entró mercadería" de "alguien la cargó".

- [ ] Decidir si el registro de cargas vale la pena. Si no se va a usar, sacar
      el filtro que depende de él en vez de dejarlo mudo.

---

## P2 — Cosas chicas que suman

- [ ] **El cron de snapshots corre menos de lo que debería.**
      `_calcular_salidas_diarias` mira 14 días, pero sólo hay **26 snapshots en
      3,94 días** — uno cada ~3,6 h. `/rowa/snapshot/auto` está pensado para
      tomar uno si el último tiene más de **50 minutos**, o sea que en esa
      ventana deberían ser ~115. Cuantos más snapshots, mejor se miden las bajas
      (una venta y una reposición entre dos tomas se cancelan). Verificar cada
      cuánto lo llama el cron externo.
- [ ] **`n_criticos` no coincide con lo que se ve.** La ruta lo calcula con
      `urgencia < 2` y el filtro de pantalla usa `urgencia < 3`. Son dos cosas
      distintas con el mismo nombre; el KPI dice 126 y la lista muestra 222.
- [ ] **2 artículos se descartan en silencio** cuando todos sus packs están en
      `NotAvailable` ([`analizar_articulo`](../services/rowa_analisis.py) devuelve
      `None`). Son 9 packs. Habría que contarlos en el diagnóstico en vez de
      hacerlos desaparecer.
- [ ] **`obs_stock.minimo` no se usa en el mundo Rowa.** Podría alimentar el
      `sug_en_robot` junto con la capacidad.
- [ ] **Revisar las 24 diferencias `robot > ObServer` periódicamente.** Las de 1
      pack son desfasaje normal; lo que importa es si se repiten en los mismos
      artículos día tras día. Hoy hay que entrar a mirar: no avisa nada.
- [ ] **TT.AMAPOLI**: 10 en el robot contra 1 en ObServer. El 26% del problema en
      un solo producto, y no parece desfasaje sino error de carga. Mirarlo a mano.

---

## Lo que el robot NO da (verificado, no asumir lo contrario)

Medido sobre 10.340 packs el 24/8/2026:

| campo | cobertura | |
|---|---|---|
| `Depth` / `Width` / `Height` | **100%** | y de ahí el volumen |
| `MaxSubItemQuantity` | **0 artículos** | la capacidad está en ObServer, no acá |
| `IsInFridge` | **0%** | se completa desde `obs_productos.requiere_cadena_frio` |
| `machine_location` | 100%… | pero es la constante `999` (el id del robot) |
| `stock_location_id` | 100%… | pero es `'NONE'` en todos |

O sea: **el robot da el tamaño de cada envase y dónde NO está.** No hay ubicación
física de canales, así que "andá al canal 42 a buscarlo" no se puede.
