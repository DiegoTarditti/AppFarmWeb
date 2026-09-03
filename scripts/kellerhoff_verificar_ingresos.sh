#!/bin/bash
# Re-corre el cruce contra ObServer para los resúmenes de Kellerhoff de las
# últimas semanas — llamado por el timer systemd
# appfarmweb-kellerhoff-ingresos.timer (ver
# appfarmweb-kellerhoff-ingresos.service.template).
#
# Por qué diario y no una sola vez: una recepción puede cargarse en ObServer
# días después del comprobante — correr esto una sola vez deja "✗ no
# encontrado" plantado para siempre en facturas que en realidad sí llegaron,
# solo que tarde. Reintenta las últimas 4 semanas cada vez (idempotente, no
# duplica nada).
#
# Las diferencias que encuentra quedan disponibles para
# alarmas.check_kellerhoff_diferencias_ingreso, que viaja con
# appfarmweb-alarmas.timer — este script NO manda Telegram por su cuenta.
set -euo pipefail

CRON_SECRET="${CRON_SECRET:?falta CRON_SECRET (seteala en el .service)}"

curl -sS --max-time 120 -X POST \
  -H "X-Cron-Secret: $CRON_SECRET" \
  -H "Content-Type: application/json" \
  http://localhost:5000/api/cron/kellerhoff-verificar-ingresos
echo
