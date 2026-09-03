#!/usr/bin/env bash
# actualizar.sh — actualiza AppFarmWeb en el server.
#
# Hace: git pull → restart de los containers de la app. Si cambió
# requirements.txt o el Dockerfile, rebuildea la imagen; si no, hace un
# restart rápido (segundos). Las migraciones corren solas al arrancar
# (RUN_INIT_DB_ON_STARTUP=1, idempotentes), así que no hay paso manual de DB.
#
# Uso:   ./actualizar.sh      (o: bash actualizar.sh)
# Nota:  no toca la data — vive en los volúmenes Postgres, intactos.

set -euo pipefail

# Pararse en la carpeta del repo (donde está este script), sin importar desde
# dónde se ejecute.
cd "$(dirname "$0")"

echo "▶ AppFarmWeb — actualizando…"
echo "  carpeta:  $(pwd)"
echo "  branch:   $(git rev-parse --abbrev-ref HEAD)"

ANTES="$(git rev-parse HEAD)"
echo "  commit:   $(git rev-parse --short HEAD)"

# 1) Traer cambios (fast-forward: falla claro si el server tiene commits locales).
echo "▶ git pull…"
git pull --ff-only

DESPUES="$(git rev-parse HEAD)"

if [ "$ANTES" = "$DESPUES" ]; then
  echo "✓ Ya estabas al día. Nada para actualizar."
  exit 0
fi
echo "  nuevo:    $(git rev-parse --short HEAD)"

# 2) ¿Cambió algo que obligue a rebuildar la imagen?
CAMBIADOS="$(git diff --name-only "$ANTES" "$DESPUES")"
if echo "$CAMBIADOS" | grep -qE '^(requirements\.txt|Dockerfile)$'; then
  echo "▶ Cambió requirements.txt/Dockerfile → rebuild de la imagen (puede tardar)…"
  docker compose up -d --build web bot
elif echo "$CAMBIADOS" | grep -qE '^docker-compose(\.override)?\.yml$'; then
  # `restart` reusa el container ya creado — un `environment:` nuevo en el
  # compose no se aplica así. Hace falta recrear (sin rebuild, no cambió
  # código de la imagen).
  echo "▶ Cambió docker-compose.yml → recrear el container (sin rebuild)…"
  docker compose up -d web bot
else
  echo "▶ Solo código/templates → restart rápido…"
  docker compose restart web bot
fi

# 2b) El panel remoto corre FUERA de docker (dos servicios systemd, ver
# scripts/panel_remoto_worker.py) vía un symlink a este mismo repo
# (/root/panel_remoto_worker.py -> scripts/panel_remoto_worker.py). El pull ya
# actualizó el archivo en disco, pero el proceso Python no relee su propio
# código solo — sin este restart, un cambio a ese script (ej. agregar un
# comando a la whitelist) quedaría en el repo sin efecto hasta que alguien
# lo note y reinicie a mano.
if git diff --name-only "$ANTES" "$DESPUES" | grep -q '^scripts/panel_remoto_worker\.py$'; then
  echo "▶ Cambió panel_remoto_worker.py → reiniciando los workers del panel remoto…"
  systemctl restart appfarmweb-panel-remoto appfarmweb-panel-remoto-lan
fi

# 3) Esperar el arranque y mostrar estado.
echo "▶ Esperando arranque…"
sleep 8
docker compose ps

# 4) Health check contra el endpoint público (200 = OK, 503 = algo mal).
PORT="${WEB_PORT:-5000}"
if command -v curl >/dev/null 2>&1; then
  echo "▶ Health check…"
  if curl -fsS -o /dev/null -w "  http://localhost:${PORT}/health → HTTP %{http_code}\n" "http://localhost:${PORT}/health"; then
    echo "✓ Actualizado a $(git rev-parse --short HEAD) y respondiendo OK."
  else
    echo "⚠ /health no respondió OK. Revisá los logs:"
    echo "    docker compose logs web --tail=50"
    exit 1
  fi
else
  echo "✓ Actualizado a $(git rev-parse --short HEAD). (curl no está instalado; salteo el health check)."
fi
