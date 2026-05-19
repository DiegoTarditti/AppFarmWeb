# Lecciones de despliegue en Render — 2026-05-19

Incidente: la app estuvo caída ~2 horas en Render mientras Lisandro
necesitaba laburar. Se mezclaron 4 problemas en cascada que dispararon
deploys fallidos uno arriba del otro. Documento para no repetir.

---

## 1. `--preload` + `init_db()` lento = port-scan timeout

**Síntoma**: deploy se cancela con `==> Port scan timeout reached, no open ports detected. Bind your service to at least one port.` antes de que gunicorn logge nada. NO hay traceback Python.

**Causa**: con `--preload`, gunicorn corre `app.py` (que llama `init_db()`) en el master **antes** de bindear el puerto. Si `init_db()` tarda más de ~90s (port-scan-timeout de Render), Render cancela.

`init_db()` actual hace:
- Cleanup de pg_type huérfanos (whitelist)
- `Base.metadata.create_all()`
- N migraciones inline `ALTER TABLE IF NOT EXISTS`

En la DB de Render (no local), eso puede pasarse de 90s.

**Fix**: quitar `--preload` del CMD del Dockerfile. El master bindea inmediato y cada worker hace su propio `init_db()`. Migraciones son idempotentes, no rompe nada.

**Como confirmar futuro**: si deploy falla con "No open ports" sin output de gunicorn → preload colgado. Si hay traceback → es otro problema.

---

## 2. Workers ocupados → health check timeout → "Failed service"

**Síntoma**: app funciona normal, después de un rato `[CRITICAL] WORKER TIMEOUT` + `SIGKILL`. Render marca **Failed service** después de varios kills.

**Causa**: 2 workers sync. Cuando ambos quedan colgados en queries lentas (`/order/28` con 30s+, antes del fix de filtrado), CUALQUIER request — incluido el `/health` que Render usa — queda en cola del web server. Al pasar 5s sin respuesta, Render asume que la app murió.

**Fixes aplicados**:
- Nuevo endpoint `/ping` que NO toca DB ni nada caro
- `render.yaml: healthCheckPath: /ping`
- (Pendiente) Optimizar queries lentas. Ya hicimos `/order/<id>` filtrando catálogo al lab del pedido. Falta `/api/notifications` y otras.

**Importante**: el endpoint `/ping` solo ayuda si los workers tienen un thread libre. Si todos los workers están **bloqueados al 100%**, ni `/ping` responde. La cura real es queries rápidas.

---

## 3. `gthread + threads + --preload` ROMPE startup (causa exacta no diagnosticada)

**Síntoma**: cambiar `gunicorn --workers 2 --preload` a `gunicorn --workers 2 --threads 4 --worker-class gthread --preload` hizo que Render dejara de bindear el puerto. Mismo "No open ports".

**Causa probable**: interacción entre `--preload` + threading + algún módulo que asume single-thread durante import. No diagnosticado en profundidad porque tuvimos que revertir rápido.

**Conclusión**: **NO migrar a gthread sin un test completo en staging.** Si querés paralelismo intra-worker, necesitás:
- Quitar `--preload` (ya lo quitamos por otra razón)
- Verificar que ningún módulo importado tenga state global mutable
- Verificar que las extensions de SQLAlchemy/Flask sean thread-safe

---

## 4. `render.yaml` puede ser pisado por config manual en dashboard

**Síntoma**: cambié `healthCheckPath: /ping` en render.yaml, pero Render seguía pegándole a `/health_web` (un alias viejo). El banner del dashboard mostraba: `Waiting for internal health check at .../health_web`.

**Causa**: Render permite override de la config declarada en `render.yaml` desde el dashboard (Settings → Health Check Path). Una vez que se cambia ahí, **manda el dashboard, no el yaml**.

**Importante**:
- Si querés cambiar healthCheckPath, hacelo en AMBOS lados (yaml + dashboard) o solo desde el dashboard.
- Documentar qué settings están overrideados manualmente. No los vemos en git.

---

## 5. Diagnosticar Render: tipos de "log"

El dashboard de Render mezcla tres tipos de output que parecen iguales pero son distintos:

| Tipo | Ejemplo | Útil para |
|---|---|---|
| **Build logs** | `Step 5/12 : COPY requirements.txt .` | Error de Docker build |
| **Deploy logs** | `[INFO] Starting gunicorn`, `Traceback...` | Error de startup de la app |
| **Access logs** | `[GET] farmacia-web/...` | Tráfico HTTP entrante |
| **Render events** | `==> Deploying...`, `==> No open ports detected` | Decisiones del orchestrator |

**Si no hay deploy logs visibles + hay "No open ports detected"** → la app no llega a arrancar. Suele ser `--preload` colgado o un crash silencioso antes del logger init.

**Si hay deploy logs con `Starting gunicorn` + después `WORKER TIMEOUT`** → la app levantó pero alguna request cuelga workers.

---

## 6. Antes de mergear a main: probar local + entender qué tocás

Tres veces esta sesión hice cambios y mergeé sin probarlos a fondo, generando deploys rotos. Específicamente:

- **gthread**: cambio agresivo sin staging → rompió port binding
- **--preload removal**: probablemente correcto, pero hubiera ido más rápido si lo hubiera detectado antes
- **healthCheckPath /ping**: el dashboard tenía override, mi cambio en yaml no aplicó

**Para futuro**:
- Cambios al Dockerfile y a `render.yaml` requieren especial cuidado. Si es posible, hacer en commits separados pequeños para poder hacer rollback granular.
- Antes de cambiar gunicorn config (workers, threads, worker-class, preload), entender qué hace cada flag y por qué lo querés cambiar.
- Si hay un problema en producción, hacer **rollback primero** y debuggear después, no al revés.

---

## 7. Plan free de Render: límites duros

- **512 MB RAM**: cualquier query que cargue >50k objetos SQLAlchemy puede OOMear el worker.
- **1 CPU**: WEB_CONCURRENCY=1 por default, gunicorn fuerza --workers 2 sin problemas pero hay throughput limitado.
- **Build sin cache eficiente**: cada deploy reinstala apt + pip desde 0 → 8-15 min normal.
- **Port-scan-timeout ~90s**: el puerto debe abrirse en menos de eso o se cancela el deploy.
- **Sentry uptime bot**: pega a `/` con regularidad. No es atacante, ignorar en métricas.

---

## Resumen pragmático

Si volvés a ver "Port scan timeout reached, no open ports detected":
1. Confirmar que `--preload` NO está en el CMD del Dockerfile
2. Confirmar que init_db() no hace nada que cuelgue (queries lentas, migraciones grandes)
3. Confirmar que el commit local levanta con `docker-compose up --build`
4. Si todo eso está OK y aún falla: probable problema infra de Render, hacer rollback

Si volvés a ver "Failed service" con `WORKER TIMEOUT`:
1. Verificar que `/ping` esté implementado y `healthCheckPath` apunte ahí
2. Identificar la query lenta culpable (los access logs muestran responseTimeMS)
3. Optimizar esa query (filtrar antes de `.all()`, paginar, etc.)
