# AppNúcleo — dashboard de grupo (Fase 0)

UX **separada y read-only** que consolida datos de las farmacias del grupo
(Badia, Pieri, y próximamente Grassi y Cappone). Evita el multi-tenant dentro de
una DB: cada farmacia sigue siendo su propia instancia; el Núcleo solo **lee** y
agrega.

## Cómo lee los datos

Fan-out a la tabla `product_analytics` de cada farmacia (snapshot chico ya
pre-agregado por el sync de cada una: stock + precio + ventas 12m + lab + rubro).
**Nunca toca `obs_ventas_detalle` crudo** → liviano, sin riesgo de OOM. Los
agregados del grupo se calculan en memoria, con caché (TTL 5 min) y degradación
por instancia (una farmacia caída no rompe el dashboard).

## Configuración

Registro de farmacias por env var `NUCLEO_FARMACIAS` (JSON):

```json
[
  {"slug":"badia","nombre":"Badia","url":"postgresql://USER:PASS@HOST/DB"},
  {"slug":"pieri","nombre":"Pieri","url":"postgresql://USER:PASS@HOST/DB"}
]
```

> Usar un **rol read-only** de Postgres por farmacia (no el owner). Las URLs no
> se commitean: viven en la env var del servicio.

Sin `NUCLEO_FARMACIAS` → **modo DEMO** con datos sintéticos (para ver la UI).

## Correr

```bash
# local (dentro del container web, reusa deps):
docker-compose exec -e NUCLEO_FARMACIAS='[...]' web python -m appnucleo.app
# → http://localhost:5001

# deploy (servicio Render aparte):
gunicorn 'appnucleo.app:create_app()'
```

## Pantallas

- `/` — Resumen: KPIs (ventas/unidades/stock valorizado/sin movimiento),
  tendencia 12m apilada por farmacia, participación, top labs, rotación, y
  detalle por farmacia con salud del feed.
- `/ventas-multi` — Pivot por laboratorio/producto/rubro con **columna por
  farmacia + consolidado** (responsive: tabla en PC, cards en mobile).

## Roadmap

- **Fase 1**: dims médico (matrícula) y obra social — requieren `obs_ventas_detalle`.
  Mejor resolverlas con el patrón "edge-ETL": cada localhost prepara un feed
  normalizado (claves naturales: alfabeta, matrícula, droga/OS normalizadas) y lo
  pushea a un warehouse propio del Núcleo → el Núcleo consulta local, sin fan-out.
- **Fase 2**: pedidos grupales (creados en las apps locales, consolidados acá).
- **Fase 3**: scoping por dueño (cada uno ve su farmacia) + auth de grupo.
