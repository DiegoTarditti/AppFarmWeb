# Manual de Usuario — AppFarmWeb

Este manual es la fuente única de verdad para usar el sistema. Está organizado por:

- **Flujos**: las recetas paso a paso para los procesos de oro (lo que se hace todos los días).
- **Pantallas**: referencia rápida de cada vista del sistema.
- **Admin**: tareas de mantenimiento y configuración.
- **Glosario**: definiciones de términos (EAN, alfabeta, monodroga, ObServer, etc.).
- **FAQ**: problemas conocidos y cómo resolverlos.

---

## Índice

### Empezar

- [Introducción](./00_introduccion.md) — qué hace la app y cómo navegarla
- [Primer uso](./01_primer_uso.md) — login, roles, DockerPanel local

### Flujos (las recetas)

- [Análisis de compra de un laboratorio](./flujos/01_analizar_laboratorio.md)
- [Análisis vía droguería](./flujos/02_analizar_drogueria.md)
- [Subir una factura (control de ingreso)](./flujos/03_subir_factura.md)
- [Cruce de factura vs ERP y generar reclamo](./flujos/04_cruce_y_reclamo.md)
- [Convertir un pedido en proceso de compra](./flujos/05_proceso_compra.md)

### Pantallas

- [Procesos de compra](./pantallas/procesos_compra.md)
- [Pedidos guardados](./pantallas/pedidos_guardados.md)
- [Indicadores del pedido](./pantallas/indicadores_pedido.md)
- [Estadísticas por monodroga](./pantallas/estadisticas_drogas.md)
- [Catálogo ObServer](./pantallas/catalogo_observer.md)
- [Clientes](./pantallas/clientes.md)
- [Obras Sociales](./pantallas/obras_sociales.md)

### Admin / Mantenimiento

- [Plantillas de exportación](./admin/plantillas_exportacion.md)
- [Sync con ObServer (manual y automático)](./admin/observer_sync.md)
- [Vincular productos a ObServer](./admin/vincular_productos.md)
- [Reset de datos](./admin/reset_datos.md)

### Referencia

- [Glosario](./glosario.md)
- [FAQ — preguntas frecuentes](./faq.md)

---

## Cómo se mantiene

- Los `.md` están versionados en git junto al código.
- Cada vez que se modifica un flujo o pantalla, se actualiza el doc en el mismo commit.
- Capturas en `docs/manual/img/` con sufijo de fecha (`indicadores_2026-04.png`).
- Pendientes a llenar: ver [TODO.md](./TODO.md).
