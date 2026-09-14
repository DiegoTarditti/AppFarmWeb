# Análisis de rentabilidad — estado, decisiones y lo que falta

Módulo para responder, por producto: a cuánto lo compro, cuál fue el mejor precio
que conseguí, cuánto margen deja, y con qué obra social conviene venderlo.

**Estado al 2026-09-14**: la capa de cálculo está construida y verificada contra
producción (38 tests). Faltan las rutas y las pantallas.

- `services/inflacion.py` — índice de inflación del costo de compra
- `services/rentabilidad.py` — costeo, márgenes y cruce con ventas
- `services/precios_lista.py` — histórico de PVP y patrón de aumento por laboratorio
- Modelos: `IndiceInflacion`, `ObsPrecioListaHist`

Los tres services se corrieron contra la base de producción y reproducen
exactamente los números medidos a mano con SQL. Ver el mapa generado para
ubicarlos.

---

## Las decisiones, y por qué

Lo que sigue costó medir. Si alguien viene a cambiarlo, que sea sabiendo esto.

### Se usa costo de REPOSICIÓN (la última compra), no promedio ponderado

La pregunta que toma decisiones es *"si vendo uno más hoy y lo repongo, ¿gano?"*,
y eso lo contesta lo que cuesta reponerlo hoy. Que se hayan vendido unidades de
stock viejo no lo invalida.

Medido: elegir entre último costo y promedio ponderado mueve el número **0,52%**
(mediana sobre 1.236 productos con 2+ compras); sólo 72 difieren más de 5%. O
sea que no hay un argumento numérico para preferir uno u otro — se eligió el que
conceptualmente contesta la pregunta.

### El indicador de calidad es la ANTIGÜEDAD del costo, no la cobertura

Corolario de lo anterior, y es contraintuitivo: un costo de hace 5 días sirve
aunque se haya comprado 1 unidad y vendido 19. Uno de hace 90 días es dudoso
aunque la cobertura sea del 100%.

Importa porque la cobertura es malísima y esconder el margen por eso sería tapar
casi todo: de 35.364 unidades vendidas, **sólo 3.162 (9%) tienen cobertura
completa de compras**. Y la relación se invierte — los productos con cobertura
completa son los de baja rotación (4 u. promedio), los mal cubiertos son los que
mueven volumen (19 u.).

La cobertura sigue sirviendo, pero para otra cosa: advertir sobre la **ganancia
del período** en $, que sí queda incompleta.

Semáforo (`confianza_costo`), con la distribución real: hasta 15 días 1.356
productos, 16-30 días 837, 31-60 días 205, más de 60 días 442.

### Todo se compara en pesos de hoy

Con los costos subiendo ~1,5% mensual, comparar un precio de julio contra uno de
septiembre en pesos nominales convierte la inflación en "mala compra".

Medido sobre jul-sep 2026: el sobreprecio del trimestre daba **$4.269.436 sin
ajustar y $2.241.314 ajustado**. Los $2.028.122 de diferencia eran inflación —
669 de los 1.375 renglones marcados dejan de estarlo. Sin esto le estaríamos
marcando al comprador errores que no cometió.

El índice se mide como un IPC: variación del **mismo producto** entre meses
consecutivos, mediana de todos los pares. Un promedio de precios por mes no
sirve porque el mix cambia. Valores medidos: +1,96% jul→ago (471 pares), +1,00%
ago→sep (497 pares).

Los períodos con `origen='manual'` no los pisa el recálculo, para poder
reemplazar un mes por un índice externo de medicamentos si alguna vez conviene.

### Umbral del 2% para marcar sobreprecio

Una compra dentro del 2% del mejor precio no se marca: es redondeo, diferencia
de un día, o el resto de inflación que el índice mensual no captura. Sin umbral
el total pasa de $2.241.314 a $3.321.603 y los productos marcados de 357 a 980,
casi todo en diferencias de centavos que no son una decisión de compra.

### Nada se oculta: las malas compras se marcan, no se filtran

Decisión explícita del usuario. El sobreprecio se muestra con su patrón, no se
suaviza. Lo único que se excluye —y se declara en pantalla— son los renglones a
costo cero, y sólo del cálculo del *mejor precio*.

---

## Trampas encontradas (todas medidas contra producción)

### Notas de crédito sin signo

Los tres caminos que cargan facturas guardan el signo distinto, y en producción
los **86 renglones de NCR están TODOS en positivo**. Hay que firmar por
`tipo_comprobante`, nunca por el signo del renglón: si no, una devolución cuenta
como compra.

### Renglones a costo cero

26 renglones (0,4%) entran facturados a $0 o con 99,99% de descuento:
psicotrópicos (Clonagin, Alplax, Lextor, Neuryl), probadores de perfumería y un
medidor de glucemia. Son compras reales y su margen es legítimo, pero si
contaran como "mejor precio" toda otra compra del mismo producto figuraría con
sobreprecio de **cientos de miles por ciento** (llegué a ver +478.000.000%).

Se excluyen sólo del baseline. Siguen contando como costo de reposición: si lo
último que entró salió $0, reponerlo hoy sale $0.

### El EAN colisiona en los dos sentidos

`CLAUDE.md` avisa que el mismo EAN aparece en varios productos el 9,48% de las
veces. Medido sobre lo que compramos: **191 de 2.816 EAN (6,78%)**, hasta 8
productos por EAN, moviendo el 4,6% del volumen.

Pero no son productos distintos: es **el mismo item cargado varias veces en
ObServer** — el algodón Estrella está 8 veces, la Nivea Soft 5, los pañales
Estrella 6. Así que hay que **sumar** las ventas de todos; quedarse con uno
(lo que hace un `DISTINCT ON`) las subcuenta.

Y al revés: un producto puede tener varios EAN (10.766 tienen 2, 2.132 tienen
3). Si se compró bajo dos de ellos sus ventas se contarían dos veces — pasa en 6
productos. Se resuelve imputando cada producto a un solo EAN, el menor.

### La unidad de compra no siempre es la de venta

Caso real: `PROFIL PRIME ZERO 12 X 3` se compra de a **1 caja a $41.952** y se
vende como `PRIME ZERO ENV x 3` a **$4.500** — el margen da **−832%** cuando en
realidad es ~+22%.

El tamaño del pack no está en ningún campo, así que no se corrige: se marca
(`sospecha_unidad`). Son 2 de los 90 productos con margen negativo; los otros 83
son pérdidas plausibles y se muestran como tales.

### La mitad de la "facturación" no es plata cobrada (la trampa más grande)

**Las liquidaciones de PAMI y las obras sociales no pasan por ObServer.**
`importe_a_cargo_os` es lo que ObServer *anota* que el convenio debería pagar —
no lo que pagó. No se cobra en el mostrador y el sistema nunca se entera del
resultado de la liquidación.

Medido sobre 90 días: de **$2.015,5M** de facturación, **$985,7M (49%)** son ese
asiento. El desglose: obra social $1.479,0M (73,4%) de los cuales sólo $493,3M
es copago real; particular $536,5M (26,6%).

**Consecuencia dura: para una venta por obra social el margen no se puede
afirmar**, ni bien ni mal. Un producto puede figurar en pérdida y estar dando
ganancia, o al revés. Lo único verificado es la venta particular y el copago.

Cómo se descubrió, porque la lección importa: la ficha mostraba la insulina
NOVORAPID con **−26,1% en PAMI** y yo llegué a armar una lista de "140 productos
que pierden $15,5M por trimestre" — con el detalle verificado venta por venta,
el costo confirmado contra el total de la factura, las notas de crédito
descartadas como compensación y el corte por plan de PAMI. Todo correcto sobre
los datos disponibles, **y aun así la conclusión estaba mal**, porque el dato que
faltaba no estaba en la base: lo que PAMI efectivamente liquida. Lo corrigió
Diego con conocimiento del negocio, no los datos.

Lo que sí quedó en pie de esa investigación, y sigue siendo útil: PAMI reconoce
**89,4% del PVP** en 731 productos y **53,2%** en otros 141, comprándose todos al
mismo descuento (~65% del PVP). Esa asimetría es real y es una buena pregunta
para hacerle a PAMI — pero **no** permite concluir que se pierde plata.

Por eso `os_pct` viaja hasta las dos pantallas y se muestra como aviso, la
columna se llama **"margen aparente"**, y las filas de convenio no se pintan de
rojo: afirmar pérdida sobre plata que no vemos liquidar sería mentir con cara de
precisión.

**Pendiente**: si alguna vez entra la liquidación real (archivo de PAMI, extracto
del convenio), esto se puede cerrar de verdad. Hasta entonces, el margen por
obra social es una comparación entre convenios, no un resultado.

### `importe` es BRUTO, no lo que entra a la caja

Ver `docs/observer_ventas_reconciliacion.md`. Medido de tres formas
independientes: la brecha es 2,04-2,32% comparando el detalle contra
`obs_ventas_mensuales` (que ya usaba `SUM(ImporteNeto)`), y 5,95% mirando sólo
ventas particulares con pago propio. Sobre ~$690M/mes son ~$14M.

Resuelto en el PR #410: `obs_ventas_detalle.importe_neto` trae la columna que ya
existía en `DW.ProductosVendidos`. **Queda NULL en todo lo ya sincronizado** —
el sync incremental sólo repasa su ventana de 20 días. Para el histórico hay que
forzar un sync con `desde_fecha` viejo. Por eso `ventas_por_ean` devuelve
`neto_pct`: qué parte de la facturación tiene el dato bueno.

---

## Patrón de aumento por laboratorio

Cada laboratorio aumenta un día fijo del mes. De los 191 laboratorios con 100+
productos, **26 concentran más del 80% de sus aumentos en un solo día**:

| Laboratorio | Productos | Día | Concentración | Meses distintos |
|---|---|---|---|---|
| Lafedar | 381 | 19 | 96,6% | 7 |
| Fabra | 147 | 31 | 95,9% | 5 |
| Richet | 277 | 22 | 94,2% | 8 |
| Tuteur | 180 | 1 | 92,8% | 9 |
| Fecofar | 154 | 29 | 90,3% | 13 |
| Gador | 345 | 31 | 95% | — |
| Siegfried | 595 | 20 | 92% | — |
| Casasco | 600 | 28 | 89% | — |

La columna de meses es la que da confianza: las fechas de vigencia están
repartidas en hasta **13 meses distintos**, así que la concentración en un mismo
día del mes no es un evento único sino un patrón que se repite.

Verificado contra las compras: el costo de la droguería se mueve entre **0 y 3
días DESPUÉS** del PVP (Gador mismo día, Siegfried +1, Roemmers +2, Bagó +2,
Baliarda +3), consistente con que el aumento se detecte en la compra siguiente.
Ojo que la concentración del lado del costo es baja (16-20%) porque el día que
se detecta depende de cuándo se compró, no de cuándo aumentó el laboratorio.

Uso: si Lafedar aumenta el 19, conviene comprarle el 18.

---

## Lo que falta implementar

Hecho: el índice de inflación, el costeo, el cruce con ventas, el histórico de
PVP enganchado al sync, y la pantalla `/rentabilidad` (`routes/rentabilidad.py`
+ `templates/rentabilidad.html`).

1. **Falta la ficha por producto** (la pantalla de detalle: gráfico de costo vs.
   venta, historial de compras, desglose por obra social). El diseño está hecho
   y validado con datos reales; `ranking()` en `services/rentabilidad.py` ya
   trae casi todo lo que necesita, falta el detalle de compras individuales
   (`costos_por_ean(...)['compras']` ya lo tiene) y `ventas_por_obra_social()`.

2. **Que el sync de Kellerhoff escriba en `producto_precios_hist`.** Hoy las
   compras del portal no dejan histórico, y ahí se pierde el `precio_publico`
   (el PVP sugerido de la droguería), que permitiría comparar el precio de venta
   contra el sugerido. No bloquea nada de lo anterior.

3. **Normalizar el signo de las notas de crédito en el alta**, para no depender
   de que cada consumidor se acuerde de firmar por `tipo_comprobante`.

### Ya existía una pantalla parecida: `/obras-sociales/productos-rentabilidad`

Se descubrió tarde — después de construir `/rentabilidad` — que ya había un
"Top productos por margen" haciendo el mismo cruce (compra vs. venta) desde
`routes/obras_sociales.py`. **Se decidió dejar las dos**, con una nota cruzada
en cada pantalla explicando por qué pueden dar números distintos. Sirven para
cosas distintas: la vieja para elegir qué OS conviene, la nueva para decidir
qué conviene seguir comprando.

Medido contra producción, la pantalla vieja tiene dos defectos que la nueva no:
toma el costo por el **último `id` de `factura_items`** en vez de por fecha de
factura (en 111 de 2.836 productos eso da una compra más vieja que la real), y
no filtra por `tipo_comprobante` (en **30 productos** la "última compra" que usa
es en realidad una **nota de crédito**). No se tocó esa pantalla más allá de la
nota — arreglar sus bugs es trabajo aparte, si algún día se decide unificarlas.

**Lección para la próxima sesión que toque esto**: `CLAUDE.md` dice explícito
"mirar el mapa generado antes de grepear a ciegas" — no se hizo, y por eso pasó
esto. Antes de tocar algo de rentabilidad, `grep -n rentabilidad
docs/MAPA.generado.md` primero.

---

## Preguntas abiertas

**Para Lisandro — ¿qué contamos como ingreso de la obra social?** Hoy se usa lo
que el convenio *debería* pagar (`importe_a_cargo_os`). La otra opción es lo que
efectivamente liquidó, neto de recetas devueltas — pero `devolucion_receta`
existe y **está vacía**. El cálculo quedó como parámetro: cuando se decida es
configuración, no reescritura.

**¿La pantalla de cobertura de datos va aparte o como panel dentro del ranking?**
Existe para que nadie decida creyendo que el dato es más sólido de lo que es.

**¿El desglose se agrupa por obra social o por plan?** El de Ozempic mostró
"Recetario Solidario" como principal convenio; habría que ver si el nivel de
agrupamiento correcto es la OS o el plan.

**Sesgo del primer mes.** Julio arranca con desventaja: al ser el primer mes del
historial tiene menos chances de contener el mínimo de cada producto, así que
está sobre-representado en la lista de sobreprecios. Se corrige solo a medida
que el historial crece.
