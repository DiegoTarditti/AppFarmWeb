"""Panel remoto — worker standalone para el server.

Polea el endpoint de Render (/api/panel/comandos/proximo) buscando comandos
para ejecutar. Ejecuta comandos de una whitelist en el server local
(docker compose, git pull, etc.) y reporta el resultado.

Sirve para admin remoto SIN necesidad de VPN: el operador entra a la URL
pública de Render (/admin/panel), encola un comando, este worker lo levanta
y lo ejecuta acá.

Config (env vars):
  PANEL_REMOTO_URL       (default: https://farmacia-web-rj1z.onrender.com)
  PANEL_REMOTO_TOKEN     (obligatorio; header X-Panel-Token)
  PANEL_REMOTO_SEG       (default: 8; entre 3 y 60)
  APPFARMWEB_DIR         (default: /root/appfarmweb; cwd para git/docker compose)

Reemplaza el _panel_remoto_loop del DockerPanel local, ahora que la app corre
en el server (192.168.1.220) en vez de la PC de oficina.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

DEFAULT_URL = 'https://farmacia-web-rj1z.onrender.com'
DEFAULT_SEG = 8
OUTPUT_LIMIT = 30000  # Render trimma a 32k, dejamos un margen.
STEP_TIMEOUT = 300    # s por paso (5 min)

CWD = os.environ.get('APPFARMWEB_DIR', '/root/appfarmweb')
URL = os.environ.get('PANEL_REMOTO_URL', DEFAULT_URL).rstrip('/')
TOKEN = os.environ.get('PANEL_REMOTO_TOKEN', '').strip()
try:
    SEG = max(3, min(60, int(os.environ.get('PANEL_REMOTO_SEG') or DEFAULT_SEG)))
except (TypeError, ValueError):
    SEG = DEFAULT_SEG


# ── Whitelist de comandos ─────────────────────────────────────────────
# Cada key es lo que el operador tipea en /admin/panel; el value es la
# lista de pasos que se corren en serie. Si un paso falla se aborta.
# `docker compose` (plugin nuevo) reemplaza a `docker-compose` que usaba
# el DockerPanel local.
WHITELIST: dict[str, list[tuple[str, str]]] = {
    # Actualización desde git (usa el script del repo, hace pull + restart o
    # rebuild según cambien requirements.txt/Dockerfile).
    'actualizar':       [('./actualizar.sh', 'actualizar')],
    'pull_restart':     [('git pull', 'pull'),
                         ('docker compose restart web', 'restart')],
    'restart':          [('docker compose restart web', 'restart')],
    'restart_full':     [('docker compose down', 'down'),
                         ('docker compose up -d', 'up')],
    'logs':             [('docker compose logs --tail=50 web', 'logs')],
    'status':           [('docker compose ps', 'ps')],
    'version':          [('git rev-parse --short HEAD', 'rev'),
                         ('git log -1 --format=%s%n%cI', 'last_commit')],
    'sync_now':         [('curl -sS --max-time 30 -X POST "http://localhost:5000/api/auto-sync?bg=1"',
                          'auto-sync (bg)')],
    'sync_inteligente': [('curl -sS --max-time 290 -X POST "http://localhost:5000/api/auto-sync?modo=inteligente"',
                          'sync inteligente')],
    'push_cadencias':   [('docker compose exec -T web python -m scripts.push_cadencias_to_render',
                          'cadencias')],
    'dedupe_labs_dry':  [('docker compose exec -T web python -m scripts.dedupe_labs_drogs',
                          'dry-run')],
    'dedupe_labs_apply':[('docker compose exec -T web python -m scripts.dedupe_labs_drogs --apply',
                          'apply')],
    'purgar_cron_log':  [('curl -sS -X POST "http://localhost:5000/api/cron-log/purgar?dias=7"',
                          'purgar')],
    'backup':           [('/root/backup-farmacia.sh', 'backup ad-hoc'),
                         ('ls -lh /root/backups/', 'listar')],
    'health':           [('docker compose ps', 'ps'),
                         ('git rev-parse --short HEAD', 'rev'),
                         ('docker compose logs --tail=20 web', 'web logs'),
                         ('docker compose logs --tail=20 db', 'db logs'),
                         ('df -h /', 'disco'),
                         ('free -h', 'memoria')],
    # ── AppCajasBadia (systemd service en el server, distinto stack) ─────
    'actualizar-cajas': [('git -C /root/appcajasbadia pull', 'pull'),
                         ('/root/appcajasbadia/.venv/bin/pip install -q -r /root/appcajasbadia/requirements.txt', 'pip'),
                         ('systemctl restart appcajasbadia', 'restart')],
    'restart-cajas':    [('systemctl restart appcajasbadia', 'restart')],
    'logs-cajas':       [('journalctl -u appcajasbadia -n 50 --no-pager', 'logs')],
    'status-cajas':     [('systemctl status appcajasbadia --no-pager', 'status')],
}


def log(msg: str) -> None:
    ts = dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{ts}] {msg}', flush=True)


def error_en_respuesta_json(stdout: str) -> str | None:
    """Detecta HTTP 200 con {"ok": false, ...} (endpoints como /api/auto-sync
    devuelven así cuando el proceso interno falla). curl da exit 0 igual, así
    que sin esto reportaríamos 'ok' cuando en realidad hubo error."""
    salida = (stdout or '').strip()
    if not salida.startswith('{'):
        return None
    try:
        j = json.loads(salida)
    except (ValueError, TypeError):
        return None
    if not isinstance(j, dict) or j.get('ok') is not False:
        return None
    err = j.get('error')
    if not err and isinstance(j.get('pasos'), list):
        for p in j['pasos']:
            if isinstance(p, dict) and p.get('ok') is False:
                err = f"{p.get('paso')}: {p.get('error')}"
                break
    return err or 'la respuesta trajo ok:false'


def ejecutar_pasos(steps: list[tuple[str, str]]) -> tuple[str, str]:
    """Ejecuta una secuencia. Devuelve (estado, output). 'ok' si todos los
    pasos exit 0 y sin ok:false en JSON de respuesta; 'error' al primer fallo.
    """
    out: list[str] = []
    for cmd, desc in steps:
        out.append(f'$ {cmd}')
        try:
            proc = subprocess.run(
                cmd, shell=True, cwd=CWD,
                capture_output=True, text=True, timeout=STEP_TIMEOUT,
                encoding='utf-8', errors='replace',
            )
            if proc.stdout:
                out.append(proc.stdout.rstrip())
            if proc.stderr:
                out.append('[stderr] ' + proc.stderr.rstrip())
            out.append(f'[exit={proc.returncode}]')
            if proc.returncode != 0:
                return 'error', '\n'.join(out)
            err = error_en_respuesta_json(proc.stdout)
            if err:
                out.append(f'[respuesta ok:false → {err}]')
                return 'error', '\n'.join(out)
        except subprocess.TimeoutExpired:
            out.append(f'[TIMEOUT >{STEP_TIMEOUT}s en paso "{desc}"]')
            return 'error', '\n'.join(out)
        except Exception as e:  # noqa: BLE001
            out.append(f'[EXCEPCIÓN en paso "{desc}": {e}]')
            return 'error', '\n'.join(out)
    return 'ok', '\n'.join(out)


def poll_proximo() -> dict | None:
    """GET /api/panel/comandos/proximo. Devuelve el comando o None si no hay."""
    url = f'{URL}/api/panel/comandos/proximo?origen=server'
    req = urllib.request.Request(
        url,
        headers={'X-Panel-Token': TOKEN, 'User-Agent': 'PanelRemoto-Server'},
        method='GET',
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        if e.code in (401, 503):
            log(f'auth/503 ({e.code}): {e.reason}')
        else:
            log(f'HTTPError {e.code}: {e.reason}')
        return None
    except (urllib.error.URLError, OSError) as e:
        log(f'poll error: {e}')
        return None
    if not data.get('ok') or not data.get('comando'):
        return None
    return data['comando']


def reportar(cmd_id: int, estado: str, output: str, duracion_ms: int) -> None:
    """POST /api/panel/comandos/<id>/resultado."""
    url = f'{URL}/api/panel/comandos/{cmd_id}/resultado'
    body = json.dumps({
        'estado': estado,
        'resultado': output[-OUTPUT_LIMIT:],
        'duracion_ms': duracion_ms,
    }).encode('utf-8')
    req = urllib.request.Request(
        url, data=body,
        headers={'X-Panel-Token': TOKEN,
                 'Content-Type': 'application/json',
                 'User-Agent': 'PanelRemoto-Server'},
        method='POST',
    )
    try:
        urllib.request.urlopen(req, timeout=15).close()
    except urllib.error.HTTPError as e:
        log(f'reportar {cmd_id}: HTTP {e.code} {e.reason}')
    except (urllib.error.URLError, OSError) as e:
        log(f'reportar {cmd_id}: {e}')


def tick() -> None:
    cmd_info = poll_proximo()
    if not cmd_info:
        return
    cmd_id = cmd_info['id']
    cmd_name = cmd_info['comando']
    solicitado = cmd_info.get('solicitado_por', '?')
    log(f'📡 ejecutando #{cmd_id} "{cmd_name}" (pedido por {solicitado})')
    t0 = time.time()
    steps = WHITELIST.get(cmd_name)
    if not steps:
        estado, output = 'error', f'Comando "{cmd_name}" no está en el whitelist.'
    else:
        estado, output = ejecutar_pasos(steps)
    dur_ms = int((time.time() - t0) * 1000)
    log(f'✔ #{cmd_id} {estado} en {dur_ms} ms')
    reportar(cmd_id, estado, output, dur_ms)


def main() -> int:
    if not TOKEN:
        log('ERROR: falta PANEL_REMOTO_TOKEN en env. Cerrando.')
        return 1
    if not os.path.isdir(CWD):
        log(f'ERROR: APPFARMWEB_DIR="{CWD}" no existe. Cerrando.')
        return 1
    log(f'iniciado. URL={URL}  DIR={CWD}  SEG={SEG}')
    while True:
        try:
            tick()
        except Exception as e:  # noqa: BLE001
            log(f'loop error: {e}')
        time.sleep(SEG)


if __name__ == '__main__':
    sys.exit(main())
