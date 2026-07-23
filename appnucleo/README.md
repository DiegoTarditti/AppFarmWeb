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

Cómodo: copiá `appnucleo/.env.example` → `appnucleo/.env` (gitignored) y poné ahí
`NUCLEO_FARMACIAS`. Si `python-dotenv` está instalado, se carga solo (y no tenés
que pasar el `-e` por comando).

> En producción el registro se lee de la tabla `sucursales` (las activas con
> `url_externa`) vía `NUCLEO_REGISTRO_URL` (o `DATABASE_URL`). Precedencia:
> `NUCLEO_FARMACIAS` (override) → `sucursales` → DEMO.

## Login (usuarios + scoping por farmacia)

Usuarios por env var `NUCLEO_USERS` (JSON). Cada uno con su `password` y las
`farmacias` que puede ver (`"*"` = todas, o lista de slugs):

```json
[
  {"usuario":"diego","password":"...","nombre":"Diego","farmacias":"*"},
  {"usuario":"badia","password":"...","nombre":"Dueño Badia","farmacias":["badia"]}
]
```

- **Sin `NUCLEO_USERS`** → acceso abierto (sin login). Seteala para exigir login.
- El usuario solo ve sus farmacias permitidas (el grupo se filtra por sesión).
- `/ping` queda siempre abierto (healthcheck). `/login` y `/logout` gestionan la sesión.
- Passwords en texto plano en la env var (scope chico: dueños). La sesión usa
  `NUCLEO_SECRET_KEY`. Para endurecer a futuro: hashear o pasar a OAuth.

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
