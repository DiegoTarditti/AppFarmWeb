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
    user       = cfg.get("PORTAINER_USER",  "admin")
    pwd        = cfg.get("PORTAINER_PASS",  "")
    cname      = cfg.get("CONTAINER_NAME",  "web")
    ep_id      = int(cfg.get("ENDPOINT_ID", "1"))
    branch     = cfg.get("BRANCH",          "main")

    if not pwd:
        _err("PORTAINER_PASS está vacío en portainer_config.txt")

    TOTAL = 4
    print(f"\n{BOLD}╔══ AppFarmWeb — Actualización en servidor ══╗{RESET}")
    print(f"  {DIM}URL       {url}{RESET}")
    print(f"  {DIM}Container {cname}{RESET}")
    print(f"  {DIM}Branch    {branch}{RESET}")

    s = requests.Session()
    s.timeout = 30

    # ── Paso 1: Auth ──────────────────────────────────────────────────────────
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

    # ── Paso 2: Encontrar container ───────────────────────────────────────────
    _paso(2, TOTAL, f"Buscando container '{cname}'")
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

    # ── Paso 3: Git pull ──────────────────────────────────────────────────────
    _paso(3, TOTAL, f"git pull origin {branch}")
    cmd = f"git -C /app pull origin {branch} 2>&1"
    r = s.post(
        f"{url}/api/endpoints/{ep_id}/docker/containers/{container_id}/exec",
        json={
            "AttachStdout": True,
            "AttachStderr": True,
            "Tty": True,
            "Cmd": ["/bin/sh", "-c", cmd],
        },
    )
    r.raise_for_status()
    exec_id = r.json()["Id"]

    r = s.post(
        f"{url}/api/endpoints/{ep_id}/docker/exec/{exec_id}/start",
        json={"Detach": False, "Tty": True},
    )
    _print_output(r.content)

    output_text = r.content.decode("utf-8", errors="replace").lower()
    if "already up to date" in output_text:
        _warn("Ya estaba actualizado — no hubo cambios")
    elif "error" in output_text and "fast-forward" not in output_text and "updating" not in output_text:
        _warn("Revisá la salida de arriba, puede haber un error en el pull")

    # ── Paso 4: Restart ───────────────────────────────────────────────────────
    _paso(4, TOTAL, "Reiniciando container web")
    r = s.post(
        f"{url}/api/endpoints/{ep_id}/docker/containers/{container_id}/restart",
        params={"t": 5},
    )
    r.raise_for_status()
    _ok("Container reiniciado (gunicorn recargando...)")

    print(f"\n{BOLD}{GREEN}╚══ Listo. App disponible en ~10 segundos. ══╝{RESET}\n")


if __name__ == "__main__":
    main()
