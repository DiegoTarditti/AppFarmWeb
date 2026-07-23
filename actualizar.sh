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
if git diff --name-only "$ANTES" "$DESPUES" | grep -qE '^(requirements\.txt|Dockerfile)$'; then
  echo "▶ Cambió requirements.txt/Dockerfile → rebuild de la imagen (puede tardar)…"
  docker compose up -d --build web bot
else
  echo "▶ Solo código/templates → restart rápido…"
  docker compose restart web bot
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
