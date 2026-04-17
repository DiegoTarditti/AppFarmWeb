# Estrategia: Acceso a DB de ObServer desde Render

**Contexto**: La semana próxima tendremos acceso a una DB con info online de ventas, estadísticos, productos, etc. Son tablas gigantes del ObServer. La app está deployada en Render y queremos evitar copiar todo.

## Opciones

### 1. Agente local + API delgada (vía Tailscale)

Un proceso chico corriendo en la PC con ObServer expone solo los endpoints que la web necesita.

- Ejemplo: `/ventas-producto/<ean>?desde=...`, `/stock-actual/<ean>`, `/rotacion/<lab>`
- Render consulta on-demand a través del tailnet (Tailscale ya está configurado)
- Nada se replica, data siempre fresca

**Tradeoff**: si la PC local se apaga o pierde conexión, la web pierde esa funcionalidad → necesita cache o fallback.

### 2. Sync selectivo de agregados

Un cron en la PC local calcula solo lo que la app necesita y lo pushea al Postgres de Render.

- Ventas mensuales por EAN, rotación, stock actual, analytics precalculados
- Las tablas crudas del ObServer nunca salen de la PC
- Se envía solo el resultado agregado

**Tradeoff**: data con lag (horas o un día). A cambio, la web es totalmente independiente de la conectividad local en runtime.

### 3. Híbrido (recomendado a mediano plazo)

Combina las dos anteriores:

- **Agregados sincronizados** para lo que se consulta siempre (dashboard, rotación, resúmenes)
- **API on-demand** para queries puntuales raras (detalle de una venta vieja, drill-down)

## Recomendación inicial

Arrancar con la opción 1 (agente local vía Tailscale) porque:

- Reutiliza la infra ya configurada (tailscale_setup.md ya existe)
- Menor fricción para implementar y probar
- Data 100% fresca desde el primer día

Si aparece el problema de "la PC se apagó" o latencias, se agrega cache o se evoluciona a la opción 3.

## Pendientes a decidir

- Qué datos puntuales necesita la web (listar queries que hoy hace el ERP y qué debería hacer la web)
- Qué se consulta a menudo vs. qué es ocasional (define qué agregar vs. qué dejar on-demand)
- Política de cache: si el agente responde lento, ¿cuánto es tolerable? ¿Cacheamos en Render?
- Autenticación del endpoint entre Render y el agente local
