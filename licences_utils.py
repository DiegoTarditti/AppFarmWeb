# license_utils.py
import hashlib, uuid, subprocess, platform

def get_fingerprint():
    mac = hex(uuid.getnode())
    hostname = platform.node()
    # Nro de serie del disco C: en Windows
    try:
        vol = subprocess.check_output('vol C:', shell=True).decode()
        serial = vol.split()[-1]
    except Exception:
        serial = 'unknown'
    raw = f"{mac}|{hostname}|{serial}"
    return hashlib.sha256(raw.encode()).hexdigest()[:40]
