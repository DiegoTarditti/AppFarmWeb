# Panel Remoto — worker standalone

Deploy en un server Linux para admin remoto sin VPN. Reemplaza el loop
`_panel_remoto_loop` del `DockerPanel/docker_panel.py` que corría en la PC
de oficina.

## Archivos

- `panel_remoto_worker.py` — el worker Python (polea + ejecuta + reporta)
- `appfarmweb-panel-remoto.service.template` — plantilla systemd

## Instalación

```bash
# 1. Copiar el worker al server
scp scripts/panel_remoto_worker.py root@SERVER:/root/panel_remoto_worker.py

# 2. Copiar plantilla systemd, poner el token real, mover a /etc/systemd/system/
scp scripts/appfarmweb-panel-remoto.service.template root@SERVER:/tmp/
ssh root@SERVER "
  sed -i 's|<PONER_TOKEN_ACA>|EL_TOKEN_REAL|' /tmp/appfarmweb-panel-remoto.service.template
  mv /tmp/appfarmweb-panel-remoto.service.template /etc/systemd/system/appfarmweb-panel-remoto.service
  chmod 640 /etc/systemd/system/appfarmweb-panel-remoto.service
  systemctl daemon-reload
  systemctl enable --now appfarmweb-panel-remoto
"

# 3. Verificar
ssh root@SERVER "systemctl status appfarmweb-panel-remoto"
```

## 2do worker apuntando a LAN (opcional)

Para procesar comandos encolados desde el panel local (además del Render
público):

```bash
ssh root@SERVER "
  cp /etc/systemd/system/appfarmweb-panel-remoto.service \
     /etc/systemd/system/appfarmweb-panel-remoto-lan.service
  sed -i 's|https://farmacia-web-rj1z.onrender.com|http://192.168.1.220:5000|' \
    /etc/systemd/system/appfarmweb-panel-remoto-lan.service
  sed -i 's|Description=Panel Remoto|Description=Panel Remoto LAN|' \
    /etc/systemd/system/appfarmweb-panel-remoto-lan.service
  systemctl daemon-reload
  systemctl enable --now appfarmweb-panel-remoto-lan
"
```

Los 2 workers coexisten sin conflicto: cada uno polea una DB distinta y
actúa sobre su propia base.

## Whitelist de comandos

Ver `WHITELIST` en `panel_remoto_worker.py`. Incluye:

**AppFarmWeb** (docker compose):
- `actualizar`, `pull_restart`, `restart`, `restart_full`
- `logs`, `status`, `version`, `health`
- `sync_now`, `sync_inteligente`, `push_cadencias`
- `dedupe_labs_dry` / `dedupe_labs_apply`, `purgar_cron_log`
- `backup`

**AppCajasBadia** (systemd nativo):
- `actualizar-cajas` — git pull + pip install + restart
- `restart-cajas`, `logs-cajas`, `status-cajas`

Para agregar un comando nuevo: editar `panel_remoto_worker.py` en el server
(`/root/panel_remoto_worker.py`), agregar entrada a `WHITELIST`, y
`systemctl restart appfarmweb-panel-remoto`.

## Actualizar el worker

Cuando cambies el archivo:

```bash
scp scripts/panel_remoto_worker.py root@SERVER:/root/panel_remoto_worker.py
ssh root@SERVER "systemctl restart appfarmweb-panel-remoto appfarmweb-panel-remoto-lan"
```

## Logs

```bash
ssh root@SERVER "journalctl -u appfarmweb-panel-remoto -f"
```

O desde Portainer: containers → panel_remoto (no aplica — es systemd nativo,
no container). Usar `journalctl` en el server.
