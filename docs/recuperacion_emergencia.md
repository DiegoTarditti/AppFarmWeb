# 🚨 Plan de recuperación ante fallos

Procedimiento para volver la app online cuando algo se rompe — pensado para
que cualquiera de la farmacia pueda ejecutarlo sin saber programación.

---

## 🎯 Diagnóstico rápido

**Síntoma**: el sitio no carga / muestra error / pantalla blanca / "502 Bad Gateway".

Antes de hacer nada, contestá:

1. **¿Cuándo dejó de andar?**
   - Justo después de un deploy → el commit nuevo rompió algo. Solución: rollback.
   - De golpe sin razón visible → puede ser DB, infraestructura o cuelgue. Solución: reinicio.
   - Hace días que no anda → desconexión de internet / Render dormido / DB caída.

2. **¿Dónde corre la app?**
   - **Render** (cloud, default): mirá [render.com/dashboard](https://dashboard.render.com).
   - **Local con DockerPanel**: abrí el panel.
   - **Mini-PC en farmacia**: SSH o panel local.

---

## 🔄 Caso A — Sitio caído después de un deploy nuevo (Render)

**Probabilidad:** baja, porque Render tiene health check y NO cambia tráfico
si el build nuevo no responde. Pero si pasó:

### Solución 1: Rollback con Render Dashboard (1 click, ~2 min)

1. Ir a https://dashboard.render.com
2. Seleccionar el servicio `appfarmweb` (o como se llame).
3. Pestaña **"Events"**.
4. Encontrar el deploy ANTERIOR al que rompió (uno con ✅ verde).
5. Click en el menú `⋯` → **"Rollback to this deploy"**.
6. Esperar 2-3 min. El sitio vuelve.

### Solución 2: Rollback usando el tag `production-stable` (avanzado)

Si Solución 1 no funciona o querés hacerlo desde la línea de comandos:

```bash
git fetch origin
git reset --hard production-stable
git push origin main --force
```

⚠ **Esto reescribe el historial del repo**. Solo úsalo si entendés qué hace.

---

## ♻️ Caso B — App local caída (DockerPanel / Mini-PC)

### Solución 1: Reinicio simple (90% de los casos)

1. Abrir DockerPanel.
2. Click en **"Reiniciar Web"**.
3. Esperar el post-check (~30s).
4. Si dice ✅ → listo.

### Solución 2: Si el reinicio no levanta

```bash
docker-compose down
docker-compose up -d
docker-compose logs --tail=50 web
```

Mirar los logs. Mensajes típicos:

| Mensaje | Significado | Solución |
|---|---|---|
| `Worker failed to boot` | El código tiene un bug que rompe el arranque | Rollback al tag stable (ver Caso A Solución 2) |
| `connection refused` postgres | DB no levantó | `docker-compose up -d db` y volver a intentar |
| `disk quota exceeded` | Disco lleno | Borrar logs/backups viejos, `docker system prune` |

### Solución 3: Reset total (último recurso)

```bash
docker-compose down -v   # ⚠ BORRA volúmenes — solo si la DB está corrupta
git reset --hard production-stable
docker-compose up -d --build
# Restaurar último backup de DB
psql -h localhost -U postgres < backups/ultimo.sql
```

---

## 🆘 Caso C — DB corrupta / data perdida

### Restore desde backup

DockerPanel tiene "Backup / Restore" en el menú. Si no:

```bash
# Listar backups disponibles
ls -lt /backups/

# Restaurar uno
docker-compose exec -T db psql -U postgres -d farmacia < /backups/2026-04-29.sql
```

⚠ El restore **sobreescribe** la DB actual. Asegurate antes con un backup
del estado actual:

```bash
docker-compose exec -T db pg_dump -U postgres farmacia > /backups/antes_de_restore.sql
```

---

## 📞 Cuándo escalar

Si después de las soluciones de arriba el sitio sigue caído:

1. **Tomar screenshot** del error o de los logs.
2. **Anotar la hora** en que dejó de andar.
3. **No tocar más nada** (los intentos de "arreglar" pueden empeorar).
4. Avisar al desarrollador con el screenshot + hora.

---

## 📋 Lo que el sistema hace solo (no necesitás tocarlo)

- **Health check de Render**: cada deploy nuevo se valida automáticamente. Si
  no responde, Render mantiene la versión anterior.
- **Tag `production-stable`**: GitHub Actions taggea automáticamente cada
  commit que pasa health check exitoso. Es el "punto de retorno seguro"
  para cualquier rollback.
- **Backups de DB**: se ejecutan diariamente vía cron del DockerPanel
  (revisar que estén corriendo cada tanto).

---

## 🔧 Para el desarrollador

- Tag `production-stable` lo crea `.github/workflows/ci.yml` job `tag-stable`.
- Necesita el secret `RENDER_HEALTH_URL` (URL completa al endpoint `/health`).
- Si el secret no está, el tag NO se mueve (el cliente queda anclado al tag manual).
- Para configurar: GitHub repo → Settings → Secrets → Actions → New → `RENDER_HEALTH_URL=https://farmacia.onrender.com/health`.
