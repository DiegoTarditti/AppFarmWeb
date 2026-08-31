# Panel Remoto — worker standalone

Deploy en un server Linux para admin remoto sin VPN. Reemplaza el loop
`_panel_remoto_loop` del `DockerPanel/docker_panel.py` que corría en la PC
de oficina.

## Archivos

- `panel_remoto_worker.py` — el worker Python (polea + ejecuta + reporta;
  también corre un comando suelto por CLI, ver más abajo)
- `appfarmweb-panel-remoto.service.template` — plantilla systemd
- `panel-server.ps1` — atajo local por SSH directo (sin Render), ver más abajo

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

Ver `WHITELIST` en `panel_remoto_worker.py` (fuente de la verdad — esta
lista es un resumen, puede quedar corta). Incluye:

**AppFarmWeb** (docker compose, `/root/appfarmweb`):
- `actualizar`, `pull_restart`, `restart`, `restart_full`
- `logs`, `status`, `version`, `health`
- `sync_now`, `sync_inteligente`, `push_cadencias`
- `dedupe_labs_dry` / `dedupe_labs_apply`, `purgar_cron_log`
- `backup`

**AppCajasBadia** (systemd nativo, `/root/appcajasbadia`):
- `actualizar-cajas` — git pull + pip install + restart
- `restart-cajas`, `logs-cajas`, `status-cajas`

**Cartelera Badia** (docker compose, `/opt/cartelera-badia`):
- `actualizar-cartelera` — git pull + restart

**AppLabo** (docker compose, `/root/applabo`, schema compartido con
appfarmweb):
- `actualizar-applabo`, `restart-applabo`, `logs-applabo`, `status-applabo`

Para agregar un comando nuevo: editar `WHITELIST` acá en el repo (no en el
server — ver "Actualizar el worker" abajo), PR, merge, y `./actualizar.sh`
en `/root/appfarmweb` lo despliega y reinicia los workers solo.

## Actualizar el worker

**Ya no hace falta `scp` a mano.** Desde 2026-08-31, `/root/panel_remoto_worker.py`
en el server es un **symlink** a `scripts/panel_remoto_worker.py` de este
repo — un `git pull` normal ya actualiza el archivo. Y `actualizar.sh`
detecta si ese archivo cambió y reinicia los dos servicios systemd solo (ver
el paso "2b" ahí). O sea: mergear el PR y correr `./actualizar.sh` en
`/root/appfarmweb` alcanza — nada manual.

Excepción rara: si el mismo commit toca `panel_remoto_worker.py` **y**
`actualizar.sh` a la vez (como en el commit que introdujo el symlink), el
primer `./actualizar.sh` que trae ambos cambios corre con la versión VIEJA de
sí mismo ya cargada en memoria (bash no relee un script que se está
ejecutando) y no dispara el restart — hace falta un
`systemctl restart appfarmweb-panel-remoto appfarmweb-panel-remoto-lan` manual
esa primera vez. De ahí en más, un cambio solo en `panel_remoto_worker.py`
(sin tocar `actualizar.sh`) se propaga solo, sin este caso especial.

Si de verdad hace falta reemplazar el symlink a mano (nunca debería):
```bash
ssh root@SERVER "systemctl restart appfarmweb-panel-remoto appfarmweb-panel-remoto-lan"
```

## Modo CLI directo (sin Render)

`panel_remoto_worker.py` también corre UN comando de la whitelist directo,
sin el loop de polling ni depender de que Render esté arriba:

```bash
ssh root@SERVER "python3 /root/panel_remoto_worker.py --list"            # ver comandos disponibles
ssh root@SERVER "python3 /root/panel_remoto_worker.py actualizar-applabo" # correr uno
```

Dos atajos que usan esto:

- **`panel-server.ps1`** (PowerShell, en este mismo `scripts/`) — menú
  interactivo o `.\panel-server.ps1 <comando>` directo. Lee la lista de
  comandos en vivo del server, no la duplica.
- **`/panel-server`** en Claude Code (`~/.claude/commands/panel-server.md`,
  a nivel usuario — no viaja con este repo) — mismo mecanismo, pedido en
  lenguaje natural en cualquier sesión.

## Logs

```bash
ssh root@SERVER "journalctl -u appfarmweb-panel-remoto -f"
```

O desde Portainer: containers → panel_remoto (no aplica — es systemd nativo,
no container). Usar `journalctl` en el server.
