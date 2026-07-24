#!/usr/bin/env python3
"""
actualizar_server.py — Actualiza AppFarmWeb en el servidor de la farmacia.
Hace git pull + reinicia el container web via Portainer API.

Requiere VPN activa y portainer_config.txt configurado.
Uso: python actualizar_server.py
  o: actualizar_server.bat
"""

import os
import sys
import time

import requests

# ── Colores ANSI ──────────────────────────────────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"
RED    = "\033[31m"
DIM    = "\033[2m"

def _paso(n, total, texto):
    print(f"\n{BOLD}{CYAN}[{n}/{total}]{RESET} {texto}...")

def _ok(texto=""):
    print(f"    {GREEN}✓{RESET}  {texto}" if texto else f"    {GREEN}✓{RESET}")

def _warn(texto):
    print(f"    {YELLOW}⚠{RESET}  {texto}")

def _err(texto):
    print(f"\n{RED}{BOLD}✗  ERROR:{RESET} {texto}\n")
    sys.exit(1)

# ── Config ────────────────────────────────────────────────────────────────────
CONFIG_FILE  = os.path.join(os.path.dirname(__file__), "portainer_config.txt")
EXAMPLE_FILE = os.path.join(os.path.dirname(__file__), "portainer_config.example.txt")

def _leer_config():
    if not os.path.exists(CONFIG_FILE):
        _err(
            f"No existe portainer_config.txt\n"
            f"  Copiá {EXAMPLE_FILE}\n"
            f"  → {CONFIG_FILE}\n"
            f"  y completá al menos PORTAINER_PASS."
        )
    cfg = {}
    for line in open(CONFIG_FILE, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip()
    return cfg

# ── Output helper ─────────────────────────────────────────────────────────────
def _print_output(raw: bytes):
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return
    for line in text.splitlines():
        print(f"    {DIM}{line}{RESET}")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    cfg        = _leer_config()
    url        = cfg.get("PORTAINER_URL",   "http://192.168.1.220:9000")
    app_url    = cfg.get("APP_URL",         "http://192.168.1.220:5000")
    user       = cfg.get("PORTAINER_USER",  "admin")
    pwd        = cfg.get("PORTAINER_PASS",  "")
    panel_tok  = cfg.get("PANEL_REMOTO_TOKEN", "")
    cname      = cfg.get("CONTAINER_NAME",  "web")
    ep_id      = int(cfg.get("ENDPOINT_ID", "1"))
    branch     = cfg.get("BRANCH",          "main")

    if not pwd:
        _err("PORTAINER_PASS está vacío en portainer_config.txt")
    if not panel_tok:
        _err("PANEL_REMOTO_TOKEN está vacío en portainer_config.txt")

    TOTAL = 4
    print(f"\n{BOLD}╔══ AppFarmWeb — Actualización en servidor ══╗{RESET}")
    print(f"  {DIM}App       {app_url}{RESET}")
    print(f"  {DIM}Portainer {url}{RESET}")
    print(f"  {DIM}Container {cname}  /  Branch {branch}{RESET}")

    s = requests.Session()
    s.timeout = 30

    # ── Paso 1: Auth Portainer ────────────────────────────────────────────────
    _paso(1, TOTAL, "Autenticando en Portainer")
    try:
        r = s.post(f"{url}/api/auth", json={"username": user, "password": pwd})
    except requests.ConnectionError:
        _err(f"No se puede conectar a {url}\n  ¿VPN activa? ¿Portainer corriendo?")
    if r.status_code == 422:
        _err("Credenciales inválidas. Verificá PORTAINER_USER / PORTAINER_PASS.")
    r.raise_for_status()
    s.headers["Authorization"] = f"Bearer {r.json()['jwt']}"
    _ok("Token obtenido")

    # ── Auto-detectar endpoint si el configurado da 404 ──────────────────────
    def _get_endpoint_id():
        r2 = s.get(f"{url}/api/endpoints")
        r2.raise_for_status()
        endpoints = r2.json()
        if not endpoints:
            _err("Portainer no tiene endpoints configurados.")
        for e in endpoints:
            if e.get("Name", "").lower() == "local":
                return e["Id"]
        return endpoints[0]["Id"]

    # ── Paso 2: Encontrar container ───────────────────────────────────────────
    _paso(2, TOTAL, f"Buscando container '{cname}'")
    r = s.get(
        f"{url}/api/endpoints/{ep_id}/docker/containers/json",
        params={"all": "true"},
    )
    if r.status_code == 404:
        ep_id = _get_endpoint_id()
        r = s.get(
            f"{url}/api/endpoints/{ep_id}/docker/containers/json",
            params={"all": "true"},
        )
    r.raise_for_status()
    container_id = None
    for c in r.json():
        names = [n.lstrip("/") for n in c.get("Names", [])]
        if any(cname in n for n in names):
            container_id = c["Id"]
            status       = c.get("Status", "?")
            _ok(f"{names[0]}  [{status}]  id={container_id[:12]}")
            break
    if not container_id:
        _err(
            f"No se encontró container con '{cname}' en el nombre.\n"
            f"  Verificá CONTAINER_NAME en portainer_config.txt"
        )

    # ── Paso 3: Git pull via endpoint Flask ───────────────────────────────────
    _paso(3, TOTAL, f"git pull origin {branch}")
    try:
        r = requests.post(
            f"{app_url}/api/admin/actualizar",
            json={"branch": branch},
            headers={"X-Panel-Token": panel_tok},
            timeout=60,
        )
    except requests.ConnectionError:
        _err(f"No se puede conectar a la app en {app_url}\n  ¿El container web está corriendo?")
    if r.status_code == 401:
        _err("Token inválido. Verificá PANEL_REMOTO_TOKEN en portainer_config.txt")
    r.raise_for_status()
    data = r.json()
    for line in (data.get("output") or "").splitlines():
        print(f"    {DIM}{line}{RESET}")
    if not data.get("ok"):
        _warn("git pull reportó error — revisá la salida de arriba")
    elif "already up to date" in (data.get("output") or "").lower():
        _warn("Ya estaba actualizado — no hubo cambios")

    # ── Paso 4: Reload ────────────────────────────────────────────────────────
    _paso(4, TOTAL, "Recargando workers")
    if data.get("reload"):
        _ok("SIGHUP enviado al master de gunicorn — workers recargando")
    else:
        _warn("No se envió SIGHUP (el pull falló). Reiniciá el container manualmente si es necesario.")

    print(f"\n{BOLD}{GREEN}╚══ Listo. App disponible en ~5 segundos. ══╝{RESET}\n")


if __name__ == "__main__":
    main()
