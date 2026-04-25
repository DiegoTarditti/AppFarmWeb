# TODO — Manual de Usuario

Estado de cada doc.

## ✅ Contenido completado (2026-04-25)

- [x] [`00_introduccion.md`](./00_introduccion.md)
- [x] [`01_primer_uso.md`](./01_primer_uso.md)
- [x] [`glosario.md`](./glosario.md) — completo con definiciones para todos los términos clave
- [x] [`faq.md`](./faq.md) — primera pasada con problemas conocidos

### Flujos
- [x] [`flujos/01_analizar_laboratorio.md`](./flujos/01_analizar_laboratorio.md)
- [x] [`flujos/02_analizar_drogueria.md`](./flujos/02_analizar_drogueria.md)
- [x] [`flujos/03_subir_factura.md`](./flujos/03_subir_factura.md)
- [x] [`flujos/04_cruce_y_reclamo.md`](./flujos/04_cruce_y_reclamo.md)
- [x] [`flujos/05_proceso_compra.md`](./flujos/05_proceso_compra.md)

### Pantallas
- [x] [`pantallas/procesos_compra.md`](./pantallas/procesos_compra.md)
- [x] [`pantallas/pedidos_guardados.md`](./pantallas/pedidos_guardados.md)
- [x] [`pantallas/indicadores_pedido.md`](./pantallas/indicadores_pedido.md)
- [x] [`pantallas/estadisticas_drogas.md`](./pantallas/estadisticas_drogas.md)
- [x] [`pantallas/catalogo_observer.md`](./pantallas/catalogo_observer.md)
- [x] [`pantallas/clientes.md`](./pantallas/clientes.md)
- [x] [`pantallas/obras_sociales.md`](./pantallas/obras_sociales.md)

### Admin
- [x] [`admin/observer_sync.md`](./admin/observer_sync.md)
- [x] [`admin/plantillas_exportacion.md`](./admin/plantillas_exportacion.md)
- [x] [`admin/vincular_productos.md`](./admin/vincular_productos.md)
- [x] [`admin/reset_datos.md`](./admin/reset_datos.md)

## Pendientes técnicos

- [ ] **Botón "?" contextual** en `templates/base.html` que renderice el `.md` del nav_active actual. Backend ya está listo: ruta `/api/help/<seccion>` en `routes/help.py` que devuelve el markdown raw como JSON. Falta:
  - Botón flotante "?" en `base.html` (probablemente bottom-right).
  - Drawer lateral con `marked.js` (CDN) que renderice el `.md`.
  - Mapeo automático URL → seccion del manual (puede usar el `nav_active` o el path).
  - Manejo de links internos (que un click en un `[link](otra.md)` reemplace el contenido del drawer).
- [ ] **Carpeta `docs/manual/img/`** con capturas de pantalla. Sufijo de fecha (`indicadores_2026-04.png`).
  - Decidir si se versionan en git o se usa `git lfs` (depende del peso).
- [ ] **Videos cortos** (5-7 min) para los flujos de oro. Después de tener los `.md` completos. Empezar por análisis de laboratorio + control de ingreso.

## Reglas para mantener

- Cuando se cambia una UI / flujo, **actualizar el doc en el mismo commit**.
- Sin nombres propios de farmacia/personas en ejemplos (usar "tu farmacia", "el laboratorio X").
- Definiciones de términos solo en `glosario.md`, los demás docs linkean.
- Cada doc no debería pasar de ~250 líneas; si crece, partirlo.
