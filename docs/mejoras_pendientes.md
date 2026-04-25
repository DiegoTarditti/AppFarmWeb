# Mejoras pendientes — backlog vivo

Doc maestro de mejoras. Vivo: se actualiza con cada idea/decisión. Cuando algo se hace, se marca ✅ y se agrega fecha.

**Cómo leerlo:**
- Cada item tiene **trigger** (cuándo conviene hacerlo) y **esfuerzo** estimado.
- Si el trigger se cumple → arrancarlo.
- Antes de empezar a trabajar en algo nuevo, scrollear esta lista por si hay algo más urgente.

---

## 🚀 Rendimiento — cuando empiece a tardar

### ~~Vista materializada para `/estadisticas/drogas`~~ ✅ HECHO 2026-04-25
- Implementado preventivamente. `mv_stats_drogas` con refresh automático post-push a Render. Banner de frescura en la pantalla. Ver commit `8aa1d76`.

### Trigram index en `obs_productos.descripcion`
- **Trigger**: la búsqueda en `/obs/productos` o `/estadisticas/drogas` con `q=...` tarda > 1 seg.
- **Esfuerzo**: ~10 min.
- **Cómo**: `CREATE EXTENSION pg_trgm; CREATE INDEX ... USING gin (descripcion gin_trgm_ops)`.
- **Por qué**: el `ilike '%...%'` actual hace full table scan. Con 200k+ productos se nota.

### Bulk queries en `/api/pedido/<id>/indicadores`
- **Trigger**: pedidos de más de 500 items tardan > 3 seg en abrir Indicadores.
- **Esfuerzo**: 1-2 horas.
- **Cómo**: hoy hace varios queries pequeños. Refactorear a 2-3 queries con joins masivos.

### Limpieza periódica de `home_card_clicks`
- **Trigger**: la tabla pasa de 100k filas o se nota lentitud al cargar el home.
- **Esfuerzo**: 15 min.
- **Cómo**: cron en DockerPanel: `DELETE FROM home_card_clicks WHERE clicked_at < now() - interval '90 days'`.
- **Por qué**: solo se usa para el ranking de cards en el home, datos viejos no aportan.

### Migrar PDFs a S3 / Cloudflare R2
- **Trigger**: el bucket de PDFs (facturas + reclamos) pasa de 5-10 GB.
- **Esfuerzo**: 1 día.
- **Cómo**: subir a R2 (más barato que S3), guardar URL en `Invoice.pdf_filename`. Backfill scripted.

### Optimizar `/api/droga/<id>/comparar-labs`
- **Trigger**: comparar 5+ labs tarda > 2 seg.
- **Esfuerzo**: 1 hora.
- **Cómo**: consolidar las queries por lab en una sola con `GROUP BY lab_id`.

---

## 🛠 Calidad de código

### Linter (`ruff`)
- **Trigger**: cualquier momento, gratis.
- **Esfuerzo**: 5 min.
- **Cómo**: agregar job `ruff check .` en `.github/workflows/ci.yml`. Crear `pyproject.toml` con reglas suaves al inicio.
- **Por qué**: detecta imports no usados, variables sin asignar, líneas muy largas. Limpia el código sin esfuerzo.

### Más tests para flujos de oro
- **Trigger**: cualquier momento; cuanto antes mejor.
- **Esfuerzo**: 2-4 horas por flujo.
- **Cubrir**:
  - Generación de reclamo + PDF (`routes/claims.py`).
  - Bridge `vincular_pedido_observer.py` con casos edge (ambiguos, sin lab, etc.).
  - Endpoint `/api/pedido/<id>/indicadores` con varios pedidos de prueba.
  - `/api/sync-status` y banner.
  - Comparación de labs en `/estadisticas/drogas`.
- **Hoy**: 132 tests, mayormente sobre `data_extract`, `purchase_engine`, `plantillas` y rutas básicas.

### Type hints + `mypy`
- **Trigger**: refactor grande o cuando un bug de tipado nos muerda.
- **Esfuerzo**: progresivo (varios días).
- **Cómo**: agregar tipos a funciones nuevas y de a poco a las existentes. `mypy --strict-optional` en CI.

### Branch protection en `main`
- **Trigger**: ya, cuando puedas (5 min).
- **Esfuerzo**: 5 min.
- **Cómo**: GitHub repo → Settings → Branches → Add rule para `main` → "Require status checks" → marcar `syntax` y `tests`.
- **Por qué**: evita pushear código que rompe CI a producción.

### Migrar a Alembic
- **Trigger**: pasamos las ~30 tablas en `database.py` o aparece una migración compleja (renombre, mover datos).
- **Esfuerzo**: 1-2 días.
- **Cómo**: instalar Alembic, generar baseline desde la DB actual. Convertir cada `ALTER TABLE IF NOT EXISTS` inline en una migración versionada.

### Docstrings consistentes
- **Trigger**: cuando un nuevo dev se sume al proyecto.
- **Esfuerzo**: progresivo.
- **Cómo**: convención de Google/NumPy style. Incluir args, returns, raises.

### Pre-commit hooks
- **Trigger**: cuando el equipo crezca o querés más fricción contra commits sucios.
- **Esfuerzo**: 30 min.
- **Cómo**: `pre-commit` con ruff, trailing-whitespace, end-of-file-fixer.

---

## 🎨 UX — pulir el sistema

### Botón "?" contextual del manual
- **Trigger**: ya, cuando puedas (1-2 horas).
- **Esfuerzo**: ~1-2 horas.
- **Cómo**: ya hay esqueleto en `routes/help.py` + `docs/manual/`. Falta:
  - Botón "?" flotante en `base.html`.
  - Drawer con marked.js para renderizar el `.md`.
  - Mapeo de URL → sección del manual.

### Llenar contenido del manual
- **Trigger**: ir poblando con uso real.
- **Esfuerzo**: ~1 hora por doc.
- **Ver**: `docs/manual/TODO.md` para prioridades por sección.
- **Empezar por**: `flujos/01_analizar_laboratorio.md`, `flujos/03_subir_factura.md`, `glosario.md`.

### Capturas de pantalla en el manual
- **Trigger**: cuando llenes contenido de los flujos.
- **Esfuerzo**: 5 min por doc.
- **Cómo**: carpeta `docs/manual/img/` con sufijo de fecha (`indicadores_2026-04.png`). Versionar (o `git lfs` si pesan).

### Onboarding tour primera vez
- **Trigger**: cuando hagas onboarding a otra farmacia.
- **Esfuerzo**: 4-6 horas.
- **Cómo**: librería como IntroJS. Tour guiado al primer login con `rol=farmacia`.

### Mobile más pulido
- **Trigger**: cuando recibas reportes de uso desde el celular.
- **Esfuerzo**: progresivo, pantalla a pantalla.
- **Cómo**: ya empezó. Falta auditar pantallas tipo `compare.html`, listados largos, modales de Indicadores en mobile.

### PWA (instalable como app)
- **Trigger**: si querés que Lisandro pueda usar la app como icono en home del celular.
- **Esfuerzo**: 1 día.
- **Cómo**: `manifest.json` + service worker mínimo. Habilita "agregar a home screen".

---

## ⚙️ Operación / Mantenimiento

### Backup explícito a almacenamiento externo
- **Trigger**: ya, cuando puedas.
- **Esfuerzo**: 1 hora.
- **Cómo**: cron en DockerPanel: `pg_dump` + subir a Drive/Dropbox/R2. Mensual o semanal.
- **Por qué**: hoy el único backup es Render (que también puede fallar). Tener una copia más es seguridad.

### Sentry o similar para errores en prod
- **Trigger**: cuando lleguen 2+ farmacias a usarlo.
- **Esfuerzo**: 2 horas.
- **Cómo**: `sentry-sdk[flask]` con DSN en env var. Captura excepciones automáticamente.

### Logs centralizados
- **Trigger**: si Render se vuelve insuficiente (logs limitados a últimas N horas).
- **Esfuerzo**: 4 horas.
- **Cómo**: integrar con Logflare, Better Stack, o BetterStack Logs.

### Health check page interno
- **Trigger**: ya, opcional.
- **Esfuerzo**: 30 min.
- **Cómo**: `/admin/health` con: estado de DB, conteo de tablas, último sync, espacio disponible, versión deployada. Útil para diagnóstico rápido.

---

## 🌟 Features pendientes

### Forecast simple de ventas
- **Trigger**: el user pide "y cuánto voy a vender el mes que viene".
- **Esfuerzo**: 1-2 días.
- **Cómo**: media móvil ponderada o regresión lineal sobre 12m. Mostrar en `/estadisticas/drogas` y en Indicadores.

### Sistema de reglas / alertas configurables
- **Trigger**: el user pide alertas más allá del banner de sync.
- **Esfuerzo**: 2-3 días.
- **Cómo**: tabla `reglas_alerta(condicion_json, severidad, accion)`. Cron evalúa diariamente.

### Comparación temporal de pedidos
- **Trigger**: cualquier momento, agrega valor.
- **Esfuerzo**: 1 día.
- **Cómo**: en `/order/<id>` botón "Comparar con pedido anterior" → match por proveedor/lab + período cercano.

### Cruce ventas vs Obras Sociales
- **Trigger**: cuando ObServer exponga `IdPlan` en `DW.ProductosVendidos`.
- **Bloqueante**: pendiente que averigüe Lisandro.
- **Esfuerzo**: 2-3 días.
- **Cómo**: nueva sync `obs_ventas_plan_mensuales`. Dashboard en `/obras-sociales/<id>` con qué se vende por OS.

### Multi-tenant
- **Trigger**: si querés ofrecer la app a 2+ farmacias.
- **Esfuerzo**: 2-3 semanas.
- **Cómo**: agregar `farmacia_id` a casi todas las tablas, scopes en cada query, roles refinados.

### Sugerencia automática de pedido (AI)
- **Trigger**: estabilizar primero forecast + reglas.
- **Esfuerzo**: 3-5 días.
- **Cómo**: integrar Claude API que dado un análisis sugiera cantidades + texto explicativo.

### Cuentas corrientes con vencimientos
- **Trigger**: cuando se necesite tracking de plazos.
- **Esfuerzo**: 1 día.
- **Cómo**: ya existe `pagos_ajustes_cc`. Agregar campo de vencimiento + alerta cuando se acerque.

---

## 🐞 Bugs conocidos / limitaciones

### Pedidos sin link a ObServer
- **Síntoma**: items "sin link" en Indicadores.
- **Workaround**: botón "🔗 Vincular ahora" que matchea por descripción + lab.
- **Solución definitiva**: bridging automático al crear el pedido, no después.

### `obs_clientes` no tiene `IdObraSocial`
- **Síntoma**: no se puede cruzar clientes con OS desde el catálogo actual.
- **Bloqueante**: ObServer debe agregar el campo en `DW.Clientes` o tenemos que mapear via dispensas.

### Productos con `fecha_baja` en ObServer no aparecen por defecto
- **Síntoma**: el user busca un producto y no aparece porque está dado de baja en el catálogo de ObServer.
- **Solución actual**: badge "BAJA" con opacidad, toggle "Solo activos" si quiere filtrar.

### Migraciones inline en `init_db()`
- **Síntoma**: cada cambio de schema requiere agregar `ALTER TABLE IF NOT EXISTS`. Frágil para cambios complejos (renombre, drop, mover datos).
- **Plan**: migrar a Alembic cuando aparezca un cambio que no se pueda hacer así.

---

## ✅ Hechos recientes (histórico)

- 2026-04-25: **Alerta sync fallido** — banner + endpoint + `estado_syncs()`.
- 2026-04-25: **CI mínimo** — workflow GitHub Actions con syntax + pytest.
- 2026-04-25: **Test isolation fixes** — autouse fixture + mock de `entorno`.
- 2026-04-25: **Bug `_bulk_upsert_productos`** — falta de flush entre llamadas → UNIQUE violation.
- 2026-04-25: **Simplificación de ramas** — eliminada `desarrollo`, todo trabajo en `main`.
- 2026-04-25: **Esqueleto del manual de usuario** — 22 archivos en `docs/manual/`.
- 2026-04-25: **Vista materializada `mv_stats_drogas`** — pre-calcula agregados por monodroga + banner de frescura + auto-refresh post-push.
- 2026-04-25: **Indicadores del pedido** — modal con 5 tabs + sub-modal alternativas.
- 2026-04-25: **Estadísticas por droga** — comparación de labs con 12+ gráficos.

---

**Cómo mantener este doc:**
- Cuando agregues una idea, ponela en la sección que corresponda.
- Cuando completes algo, movelo a "Hechos recientes" con la fecha.
- Si una idea cambia de prioridad, actualizá el trigger.
