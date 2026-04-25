# TODO — Manual de Usuario

Estado de cada doc. Marcar `[x]` cuando esté completo.

## Prioridad ALTA — flujos diarios

- [ ] [`flujos/01_analizar_laboratorio.md`](./flujos/01_analizar_laboratorio.md) — el ciclo más usado, prioridad #1.
- [ ] [`flujos/03_subir_factura.md`](./flujos/03_subir_factura.md) — control de ingreso paso a paso.
- [ ] [`flujos/04_cruce_y_reclamo.md`](./flujos/04_cruce_y_reclamo.md) — cómo armar un reclamo por diferencias.
- [ ] [`pantallas/indicadores_pedido.md`](./pantallas/indicadores_pedido.md) — explicar cobertura, riesgos, alternativas.
- [ ] [`pantallas/estadisticas_drogas.md`](./pantallas/estadisticas_drogas.md) — uso de comparación de labs.
- [ ] [`00_introduccion.md`](./00_introduccion.md) — primer contacto con la app.
- [ ] [`glosario.md`](./glosario.md) — EAN, alfabeta, monodroga, IdProducto, observer_id.

## Prioridad MEDIA — pantallas frecuentes

- [ ] [`flujos/02_analizar_drogueria.md`](./flujos/02_analizar_drogueria.md) — canal droguería (lab que entra vía drog).
- [ ] [`flujos/05_proceso_compra.md`](./flujos/05_proceso_compra.md) — ciclo completo análisis → factura → cruce.
- [ ] [`pantallas/pedidos_guardados.md`](./pantallas/pedidos_guardados.md) — listado de pedidos.
- [ ] [`pantallas/procesos_compra.md`](./pantallas/procesos_compra.md) — listado de procesos.
- [ ] [`pantallas/catalogo_observer.md`](./pantallas/catalogo_observer.md) — los 122k productos.
- [ ] [`01_primer_uso.md`](./01_primer_uso.md) — login y configuración inicial.

## Prioridad BAJA — admin / referencia

- [ ] [`pantallas/clientes.md`](./pantallas/clientes.md) — base de 84k.
- [ ] [`pantallas/obras_sociales.md`](./pantallas/obras_sociales.md) — catálogo OS.
- [ ] [`admin/plantillas_exportacion.md`](./admin/plantillas_exportacion.md)
- [ ] [`admin/observer_sync.md`](./admin/observer_sync.md)
- [ ] [`admin/vincular_productos.md`](./admin/vincular_productos.md)
- [ ] [`admin/reset_datos.md`](./admin/reset_datos.md)
- [ ] [`faq.md`](./faq.md) — ir poblando con problemas reales.

## Pendientes técnicos

- [ ] Botón "?" contextual en `templates/base.html` que renderice el `.md` del nav_active.
- [ ] Ruta `/help/<seccion>` con render server-side (marked.js o python-markdown).
- [ ] Carpeta `docs/manual/img/` con capturas con sufijo de fecha.
- [ ] Decidir si versionar capturas con `git lfs` (si crecen mucho).
- [ ] Videos cortos para flujos de oro (después de tener los .md completos).

## Reglas para mantener

- Cuando se cambia una UI / flujo, actualizar el doc en el mismo commit.
- Sin nombres propios de farmacia/personas en ejemplos (usar "tu farmacia", "el laboratorio X").
- Definiciones de términos solo en `glosario.md`, los demás docs linkean.
- Cada doc no debería pasar de ~200 líneas; si crece, partirlo.
