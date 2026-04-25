# FAQ — Preguntas frecuentes y problemas conocidos

Doc vivo. Cuando aparezca un problema y la solución sea estable, sumalo acá.

---

## Datos / Sync

### "Items sin link a ObServer" en el modal Indicadores
**Causa**: el `codigo_barra` del PedidoItem no resuelve a un `obs_producto`.
**Solución**: botón **"🔗 Vincular ahora"** en la pestaña Riesgos del modal Indicadores. Matchea por descripción + laboratorio. Idempotente.
**Ver**: [Vincular productos](admin/vincular_productos.md).

### "No hay estadísticas de ventas todavía"
**Causa**: la tabla `obs_ventas_mensuales` está vacía o desactualizada.
**Solución**:
- Si estás en la farmacia: correr el sync desde DockerPanel (`/admin/observer-sync` → "Sync TODO").
- Si estás remoto (sin SQL Server): hacer pull de Render con DockerPanel ("🔄 Traer DB de Render").

### Trabajo desde casa y veo todo en cero
**Causa**: tu Postgres local está vacío. La app necesita los datos de ObServer.
**Solución**: DockerPanel → "🔄 Traer DB de Render". Trae snapshot fresco de producción.

### Banner ámbar/rojo de sync arriba de la pantalla
**Causa**: el sync de `ventas_mensuales` o `stock` tiene > 24h.
**Solución**: click en "Ver detalle" → `/admin/observer-sync` → corré el sync.
**Para configurar el cron automático**: usar el cron embebido del DockerPanel.

---

## Procesos / Pedidos

### El botón "Analizar" no funciona / no me lleva a "cuántos días"
**Causa histórica**: `observer_disponible()` chequeaba conexión TCP a SQL Server. Desde casa sin VPN devolvía False y caía en `proceso_detail` en vez de `observer_analizar`.
**Solución actual**: ya se usa `observer_analisis_disponible()` que también acepta datos locales sin SQL Server.
**Si sigue fallando**: chequear que `obs_ventas_mensuales` tenga filas locales. Si está vacía → pull de Render.

### Pedido en `/orders` con badge "Sin link a ObServer"
**Causa**: el pedido se generó desde Excel (no ObServer) y los EANs no están bridgeados.
**Solución**: abrir Indicadores → Riesgos → "🔗 Vincular ahora".

### Quería elegir droguería pero el radio sigue en laboratorio
**Causa**: el auto-save dispara cuando cambiás el radio.
**Solución**: refrescar la página y volver a intentar. El último canal elegido queda persistido.

---

## Pantallas / UI

### En mobile algunos botones quedan fuera de pantalla
**Causa**: header con `flex` sin `flex-wrap`.
**Solución aplicada**: headers con `flex-wrap` + padding responsive (`px-3 sm:px-6`) + `min-w-0` + `truncate` en titles. Si encontrás otro caso, reportar.

### Filas de pedidos guardados se ven aplastadas en pantallas medianas
**Causa**: con tantas columnas + 5 botones, el flex no entraba en una línea.
**Solución aplicada**: `flex-wrap xl:flex-nowrap` (wrap default, no-wrap solo en pantallas ≥1280px) + Guardado/Procesado ocultos en pantallas medianas (`hidden lg:block`).

### Catálogo ObServer no me trae un producto que sé que existe
**Causa más común**: el producto está dado de baja en ObServer (`fecha_baja IS NOT NULL`).
**Solución actual**: por default mostramos todos. Si tildaste "Solo activos", desmarcalo. Los productos baja aparecen con badge **"BAJA"** en gris.

---

## Facturas / Reclamos

### "Parser devolvió 0 ítems" al subir factura
**Causa**: el PDF cambió de formato o el regex del parser no matchea.
**Solución**:
- Usar el conversor (`/converter/upload`) para reaprender el formato sin código.
- O ajustar el parser `parsers/<slug>.py` a mano.

### Cruce no encuentra nada
**Causa**: la factura usa formato distinto al ERP (ej. EAN vs código interno).
**Solución**: hacer el match manual la primera vez, las equivalencias quedan guardadas en `barcode_mappings` para reuso futuro.

### "No se generó el PDF del reclamo"
**Causa**: reportlab falla con caracteres especiales o datos faltantes del proveedor.
**Solución**: editar el proveedor en `/providers` con razón social, CUIT, domicilio completos.

---

## Performance

### `/estadisticas/drogas` tarda mucho en cargar
**Solución actual**: la pantalla lee de la vista materializada `mv_stats_drogas` que pre-calcula los agregados → < 50ms.
**Si la vista no se refresca**: aparece banner "🔄 Calculado en vivo" indicando que está usando JOIN al vuelo. Refrescar manualmente con el botón "🔄 Refrescar ahora" (admin/dev) o esperar al próximo push a Render.

### `/obs/productos` lento al buscar
**Causa**: `ilike '%query%'` sobre 122k filas.
**Pendiente**: agregar trigram index (pg_trgm) cuando se note. Ver `docs/mejoras_pendientes.md`.

---

## Cómo agregar un FAQ

Cuando aparezca un problema:

1. **Título** = el síntoma con el que el user lo describe (no la causa técnica).
2. **Causa** entre paréntesis breve.
3. **Solución** concreta — los pasos para arreglarlo.

Ejemplo:
```markdown
### "Apreto X y aparece Y"
**Causa**: explicación técnica corta.
**Solución**: pasos concretos.
**Ver**: links a docs relacionados.
```
