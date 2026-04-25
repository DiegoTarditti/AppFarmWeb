# Glosario

Términos que aparecen en todo el sistema. Linkear desde otros docs hacia este, no repetir definiciones.

---

## A

### Alfabeta
Código numérico de Alfabeta — la base de datos farmacéutica de referencia en Argentina. ObServer indexa cada producto por este código en el campo `obs_productos.codigo_alfabeta`. NO es el EAN. Distintos paquetes del mismo producto (envase x10, x30) tienen distinto alfabeta.

## B

### Bridge productos.observer_id
La columna `productos.observer_id` (FK opcional a `obs_productos.observer_id`) que une el catálogo local (indexado por EAN) con el espejo de ObServer (indexado por IdProducto). Sin este bridge, no se puede cruzar EAN con datos históricos de venta.

## C

### Canal de compra
Cómo entra la mercadería al sistema:
- **Laboratorio (directo)**: comprás directo al fabricante. El proveedor en factura es el lab mismo.
- **Droguería**: comprás vía un intermediario (Kellerhoff, Pharmos, 20 de Junio). El proveedor en factura es la droguería, aunque el lab fabricante puede ser distinto.

Se elige en el paso 4 del wizard de análisis (`/order/<id>`). Persiste en `pedidos.canal` + `pedidos.partner_id` y se hereda al proceso al apretar "Enviar a Procesos".

### Cliente / ObsCliente
- `ObsCliente` (`obs_clientes`): espejo de `DW.Clientes` de ObServer. ~84k filas. Read-only.
- `Cliente` (`clientes`): extensión local editable, 1:1 con ObsCliente vía `observer_id`. Tiene notas, WhatsApp, email, tags, fecha de nacimiento.

### Codigo_alfabeta
Ver [Alfabeta](#alfabeta).

### Cron log
Tabla `cron_log` que registra cada proceso automático del sistema (sync, refresh, push, vincular, agente, etc.) con su inicio, fin, duración, estado y mensaje. Visible en `/admin/cron-log`. Auto-purga > 7 días.

## D

### DockerPanel
App de escritorio (Python + tkinter) que corre **en la PC de la farmacia**. Maneja: sync de ObServer, push a Render, pull desde Render para remotos, agente de PDFs, HTTP helper en puerto 5055, backup/restore de Postgres. Está en `DockerPanel/docker_panel.py`.

### Droguería
Intermediario que vende productos de varios laboratorios. Tiene CUIT propio y emite factura. En el sistema viven en la tabla `proveedores` con `tipo='drogueria'`. Las droguerías típicas en este sistema: Kellerhoff, Pharmos, 20 de Junio.

## E

### EAN
European Article Number — el código de barras de 13 dígitos impreso en cada producto. En Argentina los productos farmacéuticos usan EANs que empiezan con `779`. ObServer NO indexa por EAN, solo por alfabeta y por IdProducto.

### ERP
Sistema de Resource Planning de la farmacia. En este contexto, "subir Excel del ERP" significa el archivo de stock o recepciones generado por ObServer u otro sistema interno, que se cruza con la factura PDF.

### Estacionalidad
Patrón de venta de un producto/droga a lo largo del año. Se ve en heatmaps mensuales (12 meses) en `/estadisticas/drogas` (modal de comparación) y en Indicadores. Identificar drogas estacionales (gripe, alergia, fotoprotectores) ayuda a planificar compras.

## I

### IdProducto
El ID interno que ObServer asigna a cada producto (campo `IdProducto` en `DW.Productos`). En el sistema lo guardamos como `obs_productos.observer_id` y como bridge en `productos.observer_id`. Es la clave para cruzar local ↔ ObServer.

### Indicadores del pedido
Modal con 5 pestañas (Cobertura, Riesgos, Top productos, Mix, Estacionalidad) que se abre desde `/orders` con el botón violeta. Permite revisar un pedido antes de confirmarlo. Ver [Indicadores del pedido](pantallas/indicadores_pedido.md).

## L

### Laboratorio
Fabricante. En el sistema viven en la tabla `laboratorios` (local) y tienen contraparte en `obs_laboratorios` (espejo de ObServer). Ejemplos: Roemmers, Bagó, Bayer, Genomma.

## M

### Matview / Vista materializada
Una "tabla cache" en Postgres que guarda el resultado pre-calculado de un SELECT. Hoy hay una: `mv_stats_drogas` que pre-calcula los agregados por monodroga para `/estadisticas/drogas`. Se refresca automáticamente después del push a Render.

### Modulo (de descuento)
Lista de productos con cantidad y descuento que un laboratorio ofrece como paquete. Se importa por Excel en el paso 1 del wizard de análisis. Hay dos formatos:
- `modulo_packs`: módulos con productos definidos.
- Módulos libres: lista plana de descuentos sin packs.

### Momentum
Indicador de tendencia: `(uni_3m × 4 - uni_12m) / uni_12m * 100`. Si positivo, el lab/producto está creciendo (las ventas de los últimos 3 meses anualizadas superan el real de 12m). Si negativo, está cayendo. Se ve en Indicadores y en comparación de labs.

### Monodroga
El principio activo de un medicamento (ej. Paracetamol, Ibuprofeno, Sildenafil). Distintos laboratorios pueden vender la misma monodroga con marcas distintas (Tafirol vs Geniol = ambos Paracetamol). En ObServer está en `obs_nombres_drogas`.

## O

### ObServer
Sistema de gestión existente en la farmacia. Corre sobre **SQL Server 2014**. AppFarmWeb se conecta a su DB para traer ventas, stock y catálogo. Las tablas locales `obs_*` son cache/espejo, NO se modifican desde la app web — son lectura.

### Oferta con mínimo
Compra de N unidades del mismo producto que dispara un descuento extra del laboratorio. Se importa de Excel en el paso 3 del wizard de análisis. Formato típico: Bernabó. Las ofertas guardadas viven en la tabla `ofertas_minimo`.

## P

### Pedido
Resultado del análisis de compra: lista de productos con cantidad sugerida, opcionalmente con módulo + ofertas aplicadas. Vive en `pedidos` y `pedido_items`. Se ve en `/orders`.

### Plantilla de exportación
Configuración por lab (XLSX) o por proveedor (TXT ancho fijo) que define cómo se exporta un pedido para que matchee con el formato que el destinatario pide. Ver [Plantillas de exportación](admin/plantillas_exportacion.md).

### Proceso de compra
Wrapper sobre un pedido + factura + reclamo que rastrea el ciclo completo de una compra. Estados: `BORRADOR → ANALIZADO → PEDIDO → ENVIADO → FACTURADO → INGRESADO → CERRADO`. Vive en `procesos_compra`.

## R

### Render
Plataforma de hosting (PaaS) donde corre la app web en producción. La DB de la app está en Render Postgres. El deploy es automático al push a `main`.

### Rotación
Clasificación de un producto según velocidad de venta:
- **A** (alta): ≥ 20 unidades/mes promedio.
- **M** (media): 5-20 unidades/mes.
- **B** (baja): < 5 unidades/mes.

Umbrales configurables en `Config`.

## S

### Share de mercado
Porcentaje de unidades vendidas de cada laboratorio dentro de una droga, en los últimos 12 meses. Se ve en doughnut chart en `/estadisticas/drogas` (drill-down y modal de comparación).

### Stock dormido
Producto que tiene stock pero no tuvo ventas en los últimos 3 meses. Capital congelado. Aparece como flag en la pestaña Riesgos de Indicadores.

### Sync
Proceso automático que copia datos desde ObServer (SQL Server) a las tablas locales `obs_*` en Postgres. Disparado por cron del DockerPanel cada 3-6 horas. Ver [Observer sync](admin/observer_sync.md).
