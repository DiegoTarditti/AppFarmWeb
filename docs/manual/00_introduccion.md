# Introducción

AppFarmWeb es un sistema de **control de stock y compra inteligente** para una farmacia. Cubre el ciclo completo desde el análisis de ventas históricas hasta la generación de reclamos por diferencias en facturas.

## ¿Qué resuelve?

- **¿Cuánto comprar?** — análisis de compra basado en ventas reales y stock actual.
- **¿Lo que llegó es lo que facturé?** — cruce automático factura PDF vs ERP / ObServer.
- **¿Hay diferencias?** — generación de reclamos con PDF a la droguería.
- **¿Qué se vende y qué no?** — estadísticas por monodroga, comparación de labs, cobertura de pedido.

## Stack

- **Backend**: Flask + SQLAlchemy + Postgres.
- **Frontend**: Tailwind CSS (CDN), Chart.js, vanilla JS.
- **Integración**: ObServer (sistema de gestión existente, SQL Server 2014).
- **Deploy**: Docker en farmacia para sync, Render para la app web.

## Arquitectura híbrida

```
ObServer (SQL Server, en farmacia)
   ↑ sync
DockerPanel (PC farmacia) ─→ Postgres farmacia ─→ push ─→ Postgres Render ─→ App web
                                                                           ↑
                                                              Browser (cualquier user)
```

- La **PC de la farmacia** corre el `DockerPanel` que sincroniza ObServer cada cierto tiempo y replica los datos a Render.
- La **app web** vive en Render. La usa Lisandro (farmacia), vos (dev) y eventualmente otros (remoto).
- El sync es asíncrono: nadie consulta SQL Server desde la app web, todo va a través del espejo en Postgres.

## Mapa de la app

Sidebar izquierdo con secciones principales:
- **Inicio** — home con cards de acciones frecuentes.
- **Procesos de compra** — el ciclo análisis → pedido → factura.
- **Pedidos guardados** — análisis convertidos en pedidos.
- **Control de Ingreso** — subir facturas PDF.
- **Reclamos** — diferencias y reclamos generados.
- **Productos** — catálogo master.
- **Catálogo ObServer** — los 122k productos de ObServer.
- **Estadísticas por droga** — análisis macro por monodroga.
- **Clientes** — base de 84k clientes con extensión local editable.
- **Obras Sociales** — catálogo OS / convenios / planes.
- **Configuración** — ajustes del sistema.
- **Admin** — utilidades de mantenimiento (solo admin/dev).

## Conceptos básicos antes de empezar

Tres términos que aparecen en todos lados:
- **EAN**: el código de barras (13 dígitos) impreso en cada producto.
- **Alfabeta**: el código numérico de Argentina que ObServer usa para indexar.
- **Monodroga**: el principio activo (Paracetamol, Ibuprofeno...).

Glosario completo: ver [glosario](./glosario.md).

## Si es tu primer día

Recomendación de lectura:

1. [Primer uso](./01_primer_uso.md) — login, roles, configuración inicial.
2. [Análisis de laboratorio](./flujos/01_analizar_laboratorio.md) — el flujo de oro.
3. [Subir factura](./flujos/03_subir_factura.md) — control de ingreso.
4. [Indicadores del pedido](./pantallas/indicadores_pedido.md) — la pantalla más útil para decisiones.

Después, pickear cualquier doc según necesidad. El **glosario** y la **FAQ** sirven para consultas rápidas.
