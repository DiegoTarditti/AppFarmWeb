# Migración UX móvil → app

Análisis de opciones para evolucionar el acceso móvil actual hacia una app dedicada.
Fecha: 2026-05-25.

## Qué tenemos hoy

La "sección móvil" (`Movil.Lab`, `/consulta-stock`, `purchase_results` mobile-first) **no es una app**:
son páginas Flask + Jinja2 responsive que el navegador del celular abre desde Render. Misma app
web, con templates pensados para pantalla chica (cards apiladas, slider 7-120 días, 4 indicadores).

Base ya construida sin saberlo: **muchos endpoints `/api/` que devuelven JSON** repartidos en 30+
archivos de `routes/`. Eso es lo que una app nativa necesita para hablar con el backend.

## Las 4 opciones (de menos a más esfuerzo)

| # | Opción | Esfuerzo | Qué da | Reusa la web actual |
|---|--------|----------|--------|---------------------|
| 1 | **PWA** (manifest + service worker) | Bajo (días) | Ícono instalable, pantalla completa, algo offline, push | 100% |
| 2 | **Capacitor** (wrapper nativo) | Bajo/medio | Lo de PWA + Play Store + **cámara/scanner** + push nativo | 100% (envuelve la web) |
| 3 | **Flutter / React Native** | Medio/alto | App nativa de verdad, offline real, Android **e iOS** | 0% (UI desde cero, consume API) |
| 4 | **Nativo puro** (Android Studio/Kotlin) | Alto | Solo Android, todo desde cero | 0% |

## El detalle clave para una app de farmacia

El gran valor de pasar a app **no es la estética**, es el **lector de código de barras con la
cámara**. Se trabaja todo el día con EANs (cruce de facturas, `barcode_mappings`, `productos`).
Apuntar la cámara a un producto y que traiga stock/diferencias es un salto grande de productividad.
Eso lo dan solo **Capacitor (2)** o **Flutter (3)** — la PWA pura no.

## Cambio técnico a tener en cuenta

Hoy la auth usa **sesiones por cookie** (Flask-Login). Una app nativa conviene que use **token
(JWT o API key)** en lugar de cookie. Cambio acotado pero hay que hacerlo antes de empaquetar.

Pendiente previo a cualquier app real: **mapear qué tan completa está la API JSON** (qué endpoints
faltan para alimentar todas las pantallas que se quieran mostrar en la app).

## Recomendación

Camino progresivo, sin tirar nada:

1. **PWA primero** — casi gratis, se prueba en una semana.
2. Si sirve el scanner → **Capacitor** (reusa la misma web + cámara + Play Store).
3. Saltar a **Flutter** solo si se choca con un límite real (offline pesado, UI muy custom, iOS).

**Descartar Android Studio nativo (4):** mucho trabajo, solo Android, no aporta nada que Capacitor
no dé para este caso.

## Próximos pasos posibles (cuando se decida arrancar)

- [ ] Armar la PWA sobre la app actual (manifest + service worker + ícono instalable).
- [ ] PoC Capacitor envolviendo la web + scanner de barras.
- [ ] Auditar cobertura de la API JSON existente.
- [ ] Migrar auth a token para clientes nativos.
