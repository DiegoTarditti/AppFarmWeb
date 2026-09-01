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

En el server, dos servicios systemd (appfarmweb-panel-remoto y
appfarmweb-panel-remoto-lan) corren este archivo vía el symlink
/root/panel_remoto_worker.py -> este mismo path. Como es un proceso Python de
larga vida, un `git pull` solo no alcanza para que tome cambios acá — hace
falta reiniciar esos dos servicios (actualizar.sh ya lo hace solo si este
archivo cambió; a mano: `systemctl restart appfarmweb-panel-remoto
appfarmweb-panel-remoto-lan`).
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

# URL local del web para el health-check (localhost, no la URL de polling que
# puede ser Render). Los comandos que reinician el web se confirman por acá.
HEALTH_URL = os.environ.get('PANEL_REMOTO_HEALTH_URL', 'http://localhost:5000/')
HEALTH_TIMEOUT = 120  # s a esperar que el web vuelva tras un restart
# Comandos que reinician/recrean el container web: no alcanza con el exit code
# de `docker compose restart` (vuelve antes de que gunicorn escuche), y además
# el web está caído justo cuando iríamos a reportar. Para estos confirmamos por
# health-check y reintentamos el reporte.
REINICIA_WEB = {'pull_restart', 'restart', 'restart_full', 'actualizar'}


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
    # Ojo: `git pull` sin especificar rama requiere upstream tracking configurado.
    # El repo del server se creó con `git init + reset --hard origin/master` (no clone),
    # así que la primera vez no tiene upstream. Explicito rama para evitar el error.
    'actualizar-cajas': [('git -C /root/appcajasbadia pull origin master', 'pull'),
                         ('/root/appcajasbadia/.venv/bin/pip install -q -r /root/appcajasbadia/requirements.txt', 'pip'),
                         ('systemctl restart appcajasbadia', 'restart')],
    'restart-cajas':    [('systemctl restart appcajasbadia', 'restart')],
    'logs-cajas':       [('journalctl -u appcajasbadia -n 50 --no-pager', 'logs')],
    'status-cajas':     [('systemctl status appcajasbadia --no-pager', 'status')],
    # ── Cartelera digital (repo aparte, docker compose en /opt/cartelera-badia) ──
    # El codigo va montado como volumen (./app:/app), asi que alcanza con
    # restart y no hace falta rebuild. El -f con la ruta del compose evita
    # depender del cwd, que aca es APPFARMWEB_DIR (/root/appfarmweb).
    'actualizar-cartelera': [
        ('git -C /opt/cartelera-badia pull', 'pull'),
        ('docker compose -f /opt/cartelera-badia/docker-compose.yml restart', 'restart'),
    ],
    # ── AppLabo (repo aparte, /root/applabo, schema compartido con appfarmweb) ──
    # El código va montado como volumen (.:/app en docker-compose.yml), así que
    # alcanza con restart — no hace falta rebuild salvo que cambie
    # requirements.txt/Dockerfile (ahí sí: `docker compose up -d --build web`,
    # no está en la whitelist porque no hizo falta hasta ahora). El restart
    # corre de nuevo `flask db upgrade` (está en el CMD del
    # docker-compose.override.yml del server), así que las migraciones nuevas
    # se aplican solas. A diferencia de cartelera, acá SÍ hace falta `cd` (no
    # alcanza con -f al compose): el override vive al lado del compose base y
    # docker compose solo lo auto-carga si el cwd es esa carpeta.
    'actualizar-applabo': [
        ('git -C /root/applabo pull', 'pull'),
        ('cd /root/applabo && docker compose restart web', 'restart'),
    ],
    'restart-applabo':    [('cd /root/applabo && docker compose restart web', 'restart')],
    'logs-applabo':       [('cd /root/applabo && docker compose logs --tail=50 web', 'logs')],
    'status-applabo':     [('cd /root/applabo && docker compose ps', 'ps')],
    # ── Asistencia Badia (repo aparte, docker compose en /opt/asistencia-badia/app) ──
    # El código va montado como volumen (.:/app en docker-compose.yml), así que
    # alcanza con restart — no hace falta rebuild salvo que cambie
    # requirements.txt/Dockerfile. init_db() corre en el arranque de app.py
    # (dentro del container), así que las migraciones nuevas se aplican solas.
    'actualizar-asistencia': [
        ('git -C /opt/asistencia-badia/app pull', 'pull'),
        ('docker compose -f /opt/asistencia-badia/app/docker-compose.yml restart', 'restart'),
    ],
    'restart-asistencia': [('docker compose -f /opt/asistencia-badia/app/docker-compose.yml restart', 'restart')],
    'logs-asistencia':    [('docker compose -f /opt/asistencia-badia/app/docker-compose.yml logs --tail=50', 'logs')],
    'status-asistencia':  [('docker compose -f /opt/asistencia-badia/app/docker-compose.yml ps', 'ps')],
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
    # Reintentos: si el comando reinició el web, el endpoint de reporte puede
    # tardar unos segundos en volver a atender. Sin esto el resultado se perdía
    # y el comando quedaba "en proceso" hasta el timeout de 10 min.
    for intento in range(1, 7):
        try:
            urllib.request.urlopen(req, timeout=15).close()
            return
        except urllib.error.HTTPError as e:
            log(f'reportar {cmd_id}: HTTP {e.code} {e.reason}')
            return  # el server respondió (aunque sea error) → no reintentar
        except (urllib.error.URLError, OSError) as e:
            log(f'reportar {cmd_id} intento {intento}/6: {e}')
            time.sleep(5)
    log(f'reportar {cmd_id}: no se pudo entregar el resultado tras 6 intentos')


def web_vivo() -> bool:
    """True si el web local responde algo por HTTP (302/401/200 = está arriba).
    Solo es False si la conexión se rechaza o hace timeout (web caído)."""
    req = urllib.request.Request(
        HEALTH_URL, method='GET', headers={'User-Agent': 'PanelRemoto-Health'})
    try:
        urllib.request.urlopen(req, timeout=5).close()
        return True
    except urllib.error.HTTPError:
        return True  # responde HTTP aunque sea 4xx/3xx → gunicorn está vivo
    except (urllib.error.URLError, OSError):
        return False


def esperar_web_sano(timeout: int = HEALTH_TIMEOUT, intervalo: int = 3) -> bool:
    """Espera a que el web vuelva tras un restart. True si respondió a tiempo."""
    fin = time.time() + timeout
    while time.time() < fin:
        if web_vivo():
            return True
        time.sleep(intervalo)
    return web_vivo()


def _commit_actual() -> str:
    try:
        r = subprocess.run('git rev-parse --short HEAD', shell=True, cwd=CWD,
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or '?'
    except Exception:  # noqa: BLE001
        return '?'


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
        # Comandos que reinician el web: el éxito real es que gunicorn vuelva a
        # atender. Confirmamos por health-check (no por el exit del restart) y
        # dejamos el commit para trazabilidad. Esto también da tiempo a que el
        # web esté arriba para poder recibir el reporte.
        if estado == 'ok' and cmd_name in REINICIA_WEB:
            log(f'esperando que el web vuelva (health {HEALTH_URL})…')
            if esperar_web_sano():
                output += f'\n[web sano tras restart · commit {_commit_actual()}]'
            else:
                estado = 'error'
                output += (f'\n[web NO respondió en {HEALTH_TIMEOUT}s tras el '
                           f'restart — revisar logs]')
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


def cli_run(nombre: str) -> int:
    """Corre UN comando de la WHITELIST directo (sin Render, sin loop) y sale.
    Pensado para invocarse por SSH (ver panel-server.ps1 y el comando de
    Claude Code) — misma lista y misma lógica que usa el loop de polling,
    para no mantener dos copias de qué hace cada comando."""
    steps = WHITELIST.get(nombre)
    if not steps:
        print(f'"{nombre}" no está en la whitelist. Usá --list para ver los disponibles.',
              file=sys.stderr)
        return 2
    print(f'▶ {nombre}')
    estado, output = ejecutar_pasos(steps)
    print(output)
    if estado == 'ok' and nombre in REINICIA_WEB:
        print(f'esperando que el web vuelva (health {HEALTH_URL})…')
        if esperar_web_sano():
            print(f'✔ web sano tras restart · commit {_commit_actual()}')
        else:
            estado = 'error'
            print(f'✗ web NO respondió en {HEALTH_TIMEOUT}s tras el restart')
    print('--- OK ---' if estado == 'ok' else '--- ERROR ---')
    return 0 if estado == 'ok' else 1


if __name__ == '__main__':
    if len(sys.argv) > 1:
        if sys.argv[1] in ('--list', '-l'):
            for nombre in WHITELIST:
                print(nombre)
            sys.exit(0)
        sys.exit(cli_run(sys.argv[1]))
    sys.exit(main())
