# Notificar alarmas — timer local en `.220`

## Por qué existe

El endpoint `/api/cron/notificar-alarmas` (`routes/admin.py`) ya existía,
disparado cada 30 min por `.github/workflows/cron-alarmas.yml` **contra
Render**. El problema: la operación real (sync de ObServer vía DockerPanel,
etc.) corre en este server, no en Render — así que checks como "Sync
ObServer nunca registrado" daban falso positivo **siempre**, evaluados
contra una base que estructuralmente nunca iba a tener esos datos.

Este timer corre el mismo endpoint, pero contra `localhost:5000` — con los
datos reales. Convive con el cron de GitHub Actions/Render (no lo
reemplaza todavía); ver `alarmas.py` para qué checks son legítimos en cada
lado.

## Archivos

- `notificar_alarmas.sh` — el script (curl al endpoint local, sin secretos
  adentro)
- `appfarmweb-alarmas.service.template` — plantilla systemd (`Type=oneshot`)
- `appfarmweb-alarmas.timer` — corre el service cada 30 min

## Instalación

```bash
# 1. El repo ya está en /root/appfarmweb — el script viaja con el pull normal.
ssh root@SERVER "chmod +x /root/appfarmweb/scripts/notificar_alarmas.sh"

# 2. CRON_SECRET propio de este server (no tiene por qué ser el mismo que usa
#    Render — son dos apps independientes pegándole al mismo código).
ssh root@SERVER "openssl rand -hex 24"   # generar uno

# 3. Copiar plantilla systemd, poner el secret real, mover a /etc/systemd/system/
scp scripts/appfarmweb-alarmas.service.template root@SERVER:/tmp/
scp scripts/appfarmweb-alarmas.timer root@SERVER:/tmp/
ssh root@SERVER "
  sed -i 's|<PONER_CRON_SECRET_ACA>|EL_SECRET_REAL|' /tmp/appfarmweb-alarmas.service.template
  mv /tmp/appfarmweb-alarmas.service.template /etc/systemd/system/appfarmweb-alarmas.service
  mv /tmp/appfarmweb-alarmas.timer /etc/systemd/system/appfarmweb-alarmas.timer
  chmod 640 /etc/systemd/system/appfarmweb-alarmas.service
  systemctl daemon-reload
  systemctl enable --now appfarmweb-alarmas.timer
"

# 4. TELEGRAM_CHAT_ID en el .env de la app (TELEGRAM_BOT_TOKEN ya suele estar,
#    lo comparten otras features — ver notificaciones.py/_telegram_config()).
#    Agregar al .env y reiniciar el container web para que lo tome.

# 5. Probar ya, sin esperar al timer:
ssh root@SERVER "systemctl start appfarmweb-alarmas.service && journalctl -u appfarmweb-alarmas -n 20 --no-pager"
```

## Logs

```bash
ssh root@SERVER "journalctl -u appfarmweb-alarmas -n 50 --no-pager"
ssh root@SERVER "systemctl list-timers appfarmweb-alarmas"
```

## Timer compañero: `appfarmweb-kellerhoff-ingresos`

Mismo mecanismo, mismo `CRON_SECRET`, pero **una vez por día** (`OnCalendar`,
no `OnUnitInactiveSec`) — re-corre `verificar_ingresos_resumen` para los
resúmenes de Kellerhoff de las últimas 4 semanas (por si una recepción se
cargó tarde en ObServer). Las diferencias de cantidad que encuentra las
levanta `alarmas.check_kellerhoff_diferencias_ingreso`, que ya viaja con
`appfarmweb-alarmas.timer` — este timer NO manda Telegram por su cuenta,
solo deja los datos actualizados.

```bash
scp scripts/appfarmweb-kellerhoff-ingresos.service.template root@SERVER:/tmp/
scp scripts/appfarmweb-kellerhoff-ingresos.timer root@SERVER:/tmp/
ssh root@SERVER "
  sed -i 's|<PONER_CRON_SECRET_ACA>|EL_MISMO_SECRET_DE_APPFARMWEB_ALARMAS|' /tmp/appfarmweb-kellerhoff-ingresos.service.template
  mv /tmp/appfarmweb-kellerhoff-ingresos.service.template /etc/systemd/system/appfarmweb-kellerhoff-ingresos.service
  mv /tmp/appfarmweb-kellerhoff-ingresos.timer /etc/systemd/system/appfarmweb-kellerhoff-ingresos.timer
  chmod 640 /etc/systemd/system/appfarmweb-kellerhoff-ingresos.service
  systemctl daemon-reload
  systemctl enable --now appfarmweb-kellerhoff-ingresos.timer
"
```

## Pendiente

Decidir si el cron de GitHub Actions (`cron-alarmas.yml`, contra Render)
se apaga o se deja — algunos checks (`check_recalculo_os_atrasado`,
`check_cron_log_grande`) pueden seguir siendo válidos ahí si Render corre
sus propios crons. Los que dependen de datos que solo genera el DockerPanel
local (`check_sync_observer_parado`, `check_obs_codigos_barras_desfasada`,
`check_matview_sin_refresh`) son falsos positivos estructurales en Render y
deberían silenciarse ahí o retirarse del `CHECKS` que corre contra esa base.
