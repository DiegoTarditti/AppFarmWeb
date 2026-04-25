# FAQ — Preguntas frecuentes y problemas conocidos

> ⚠ Ir poblando con problemas reales que aparecen.

---

## Datos / Sync

### "Items sin link a ObServer" en el modal Indicadores
_(Causa: el `codigo_barra` del PedidoItem no resuelve a un `obs_producto`. Solución: botón "🔗 Vincular ahora" que matchea por descripción + laboratorio.)_

### "No hay estadísticas de ventas todavía"
_(Causa: la tabla `obs_ventas_mensuales` está vacía. Solución: correr el sync desde la PC de la farmacia o pull de Render.)_

### Trabajo desde casa y veo todo en cero
_(Solución: DockerPanel → "Traer DB de Render" para bajar un snapshot.)_

## Procesos

### El botón "Analizar" no funciona / no me lleva a la pantalla de cuántos días
_(Causa: `observer_disponible()` devolvía False. Hoy se usa `observer_analisis_disponible()` que también acepta datos locales sin SQL Server.)_

## Pantallas / UI

### En mobile algunos botones quedan fuera de pantalla
_(Solución aplicada: headers con `flex-wrap` + padding responsive. Si encontrás otro caso, reportar.)_

## Facturas / Reclamos

_(Pendiente)_

---

## Cómo agregar un FAQ

Cuando aparezca un problema y la solución sea estable, agregalo acá con:
- **Título** = el síntoma con el que el usuario lo describe (no la causa técnica).
- **Causa** entre paréntesis breve.
- **Solución** concreta.
