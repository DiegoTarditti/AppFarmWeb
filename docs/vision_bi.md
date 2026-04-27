# Visión — Módulo de Inteligencia de Negocios

**Para llevarle a Lisandro.**
Versión: 2026-04-26.

---

## La pregunta clave

> Si fueras dueño de una farmacia, ¿qué necesitás mirar todos los días, semanas y meses para que el negocio crezca?

Hoy cualquier sistema te muestra estadísticas. Lo que va a hacer distinto a este es **darte acciones concretas** apoyadas en los datos que ya tenemos en ObServer.

No es Power BI ni un dashboard genérico. Es un **asesor embebido** que te dice qué hacer.

---

## Lo que nos diferencia: 3 niveles

### 🌅 Diario — "¿qué pasa hoy?"
Lo que el dueño tiene que mirar antes de abrir la persiana, **5 minutos**:

- 🔴 **Para reponer ya**: cuántos productos están bajo mínimo, cuánta plata se está perdiendo por mes por no tenerlos.
- ⏳ **Próximos quiebres**: productos que se van a quedar sin stock en los próximos 14 días según la rotación.
- 📅 **Vencimientos próximos**: lo que vence en los próximos 60 días, para liquidar antes que se pierda.
- 📉 **Caídas bruscas**: productos que vendían bien y ahora bajaron a la mitad — algo está pasando.

**Acción esperada**: armar pedido o llamar al lab.

### 📅 Semanal — "¿cómo vamos?"
Lunes por la mañana, **15 minutos**:

- 📈 Crecimiento vs misma semana del año pasado.
- 🏆 Top 10 productos / labs / drogas que más vendiste.
- 💀 Stock muerto: lo que compraste y no se mueve.
- ⚠ Anomalías de precio detectadas en imports recientes.

**Acción esperada**: negociar con proveedores, ajustar precios, decidir qué dejar de comprar.

### 📆 Mensual — "¿hacia dónde voy?"
Primer día del mes, **30 minutos**:

- 💰 Margen real por categoría / lab.
- 🔄 Rotación promedio (cuántos días tardás en vender el stock).
- 📦 Cumplimiento de pedidos: lo pedido vs lo recibido.
- 📊 Forecast del mes que viene + cómo te fue contra el del mes pasado.

**Acción esperada**: planificar mes, decidir mix de productos, evaluar proveedores.

---

## Datos que ya tenemos vs los que faltan

### ✅ Tenemos (gracias a ObServer + lo que importamos)
- Stock actual + mínimos + máximos por producto y farmacia.
- Ventas mensuales por producto (12+ meses).
- Catálogo: productos, labs, drogas, OS, planes.
- Facturas + reclamos.
- Pedidos guardados + módulos del lab.

### ❌ Faltan
| Falta | Cómo conseguirlo |
|-------|------------------|
| **Costo por producto** (para calcular margen real) | (a) Lo extraemos del PDF de factura del proveedor; (b) carga manual top 100; (c) lista oficial de Alfabeta. |
| **Cobranzas** (el cliente paga lo facturado) | Form simple en la app o sync con ObServer si lo expone. |
| **Gastos fijos** (alquiler, sueldos, luz) | Form simple para que Lisandro cargue 1 vez por mes. |
| **Vencimientos por lote** | Confirmar si ObServer lo expone — debería. |
| **Comportamiento del cliente individual** | Ya tenemos `obs_clientes`. Falta cruzarlo con dispensas. |

---

## Plan en 3 fases

### Fase 1 — Tablero diario (semana 1)
Una pantalla `/bi` con 4 cards: para reponer / próximos quiebres / vencimientos / caídas.

**Reusa lo que ya tenemos**: Productos bajo mínimo y Pedido auto ya están construidos. Falta integrarlos visualmente + agregar Próximos quiebres y Caídas.

**Entregable visible**: pantalla operativa lista para que Lisandro la mire al abrir la mañana.

### Fase 2 — Tablero semanal + insights (semanas 2-3)
- Top productos / labs / drogas con comparación temporal.
- Stock muerto.
- Texto narrativo automático: *"Esta semana vendiste 12% más que la pasada. Roemmers te creció 30%. AMOXIDAL DUO no se vende hace 60 días."*

**Diferenciador**: que no sean solo gráficos, que **te diga qué pasa con palabras**.

### Fase 3 — Asesor (semanas 4-6)
Chat embebido que conoce tus datos:
- Lisandro escribe "¿cómo viene mayo?" y recibe número + comparación + tendencia.
- "¿Qué dejé de vender?" → lista de productos con caída detectada.
- "¿Qué le compraría a Bernabó este mes?" → sugerencia con números.

**Stack**: Claude API + capa de memoria (decisión a tomar entonces: pg_vector / Mem0 / Engram).

**Entregable visible**: el sistema te asesora, no solo te muestra datos.

---

## Antes de programar Fase 1

### Reunión con Lisandro (30 min)

1. Mostrarle la lista de indicadores propuestos.
2. Preguntarle:
   - ¿Cuáles te sirven todos los días?
   - ¿Cuáles no entendés / no te importan?
   - ¿Qué falta que no está en la lista?
3. **Tachar lo que dice que no le sirve.** No construirlo. Confiamos en él.
4. Anotar lo que pide nuevo en `c:/AppSeguimiento/07-bi-inteligencia-negocios.md`.

### Cosas a confirmar con él
- ¿Cuándo abre la persiana? (define a qué hora se actualizan los datos).
- ¿Mira métricas en celular o solo en PC? (define mobile-first o no).
- ¿Quiere recibir alertas (WhatsApp, mail) o solo verlas cuando entra al sistema?
- ¿Le interesan datos por farmacia (si tiene varias) o consolidado?

---

## Métrica de éxito

Al cierre de cada fase, una sola pregunta para Lisandro:
> *"¿Esto te cambia cómo manejás la farmacia?"*

Si la respuesta es "sí, ahora veo X que antes no veía" → vamos bien.
Si la respuesta es "está lindo pero no lo uso" → reset y empezar de nuevo.

---

## Próximos pasos

1. Construir Fase 1 mínima (1 pantalla `/bi` con 4 cards) — **hoy/mañana** para mostrarla a Lisandro como prueba.
2. Reunión con Lisandro (30 min) — **mañana**.
3. Iterar Fase 1 con su feedback.
4. Arrancar Fase 2.
