"""
Utilidades de licencia — AppFarmacia
Incluir en el proyecto distribuido. NO incluir license_generator.py.
"""
import hashlib
import hmac
import json
import os
import platform
import subprocess
import uuid
from datetime import date

# ─── CLAVE PRIVADA ────────────────────────────────────────────────────────────
# Se lee de la env var LICENSE_SECRET. Debe ser idéntica en el equipo que
# genera licencias (license_generator.py) y en cada equipo que las verifica.
# Nunca commitear el valor real. Si falta, verify_license() falla — eso es
# intencional para que un deploy mal configurado no quede con HMAC débil.
# ──────────────────────────────────────────────────────────────────────────────

LICENSE_PATH = '/app/license.dat'


def _get_secret() -> bytes:
    secret = os.environ.get('LICENSE_SECRET', '').strip()
    if len(secret) < 32:
        raise RuntimeError(
            "LICENSE_SECRET no configurada o demasiado corta (>=32 chars). "
            "Definila como env var en el equipo. La firma HMAC de licencias depende de esta clave."
        )
    return secret.encode()


def get_fingerprint() -> str:
    """Genera una huella única de la máquina actual."""
    mac = hex(uuid.getnode())
    hostname = platform.node()
    try:
        out = subprocess.check_output('vol C:', shell=True, stderr=subprocess.DEVNULL).decode()
        serial = out.strip().split()[-1]
    except Exception:
        serial = 'unknown'
    raw = f"{mac}|{hostname}|{serial}"
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


def verify_license(license_path: str = LICENSE_PATH) -> tuple:
    """
    Verifica la licencia.
    Retorna (True, farmacia_nombre) o (False, mensaje_error).
    """
    try:
        with open(license_path, encoding='utf-8') as f:
            obj = json.load(f)
    except FileNotFoundError:
        return False, "Licencia no encontrada. Contactá al proveedor del sistema."
    except Exception:
        return False, "Archivo de licencia corrupto."

    # Verificar firma
    payload = json.dumps(obj["data"], sort_keys=True, ensure_ascii=False)
    expected = hmac.new(_get_secret(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(obj.get("sig", ""), expected):
        return False, "Licencia inválida o manipulada."

    # Verificar huella de máquina
    if get_fingerprint() != obj["data"].get("fingerprint"):
        return False, "Esta licencia no es válida para este equipo."

    # Verificar vencimiento
    try:
        expiry = date.fromisoformat(obj["data"]["vence"])
    except Exception:
        return False, "Fecha de vencimiento inválida."
    if date.today() > expiry:
        dias = (date.today() - expiry).days
        return False, f"Licencia vencida hace {dias} día(s). Contactá al proveedor para renovar."

    return True, obj["data"]["farmacia"]
