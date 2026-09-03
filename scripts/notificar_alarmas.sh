#!/bin/bash
# Notifica alarmas del sistema a Telegram — llamado por el timer systemd
# appfarmweb-alarmas.timer (ver appfarmweb-alarmas.service.template).
#
# Por qué corre ACÁ y no solo desde GitHub Actions:
# el cron .github/workflows/cron-alarmas.yml le pega a Render, pero la
# operación real (sync de ObServer vía DockerPanel, etc.) corre en este
# server — Render nunca ve esos datos, así que checks como "Sync ObServer
# nunca registrado" daban falso positivo SIEMPRE ahí (estructuralmente no
# podían resolverse). Este timer corre el mismo endpoint contra
# localhost:5000, con los datos reales.
#
# No reemplaza (todavía) al cron de GitHub Actions/Render — conviven hasta
# decidir si ese se apaga.
set -euo pipefail

CRON_SECRET="${CRON_SECRET:?falta CRON_SECRET (seteala en el .service)}"

curl -sS --max-time 30 -X POST \
  -H "X-Cron-Secret: $CRON_SECRET" \
  -H "Content-Type: application/json" \
  http://localhost:5000/api/cron/notificar-alarmas
echo
