Son dos temas separados. Te explico las opciones reales para cada uno.

1. Instalador
El sistema requiere Docker, lo que complica un poco el deploy para usuarios no técnicos. La estructura más práctica:

Contenido del instalador:


FarmaciaApp_v1.0_Setup.exe  ← Inno Setup o NSIS
  ├── docker-compose.yml     ← configurado para producción
  ├── imagen Docker          ← pre-construida y empaquetada
  ├── license.dat            ← archivo de licencia (1 por cliente)
  ├── instalar.bat           ← levanta Docker, crea shortcuts
  └── DockerDesktopInstaller.exe  ← si no está instalado

#Flujo de instalación:

Ejecutar el .exe → verifica si Docker está instalado, si no lo instala
Copia los archivos a C:\FarmaciaApp\
Carga la imagen Docker pre-construida (docker load)
Crea acceso directo en el escritorio que ejecuta docker-compose up -d y abre el browser
Al primer arranque: detecta que no hay licencia activada → muestra pantalla de activación

Para armar el instalador usás Inno Setup (gratuito, muy usado en Windows).

2. Sistema de licencias
La estrategia más robusta sin servidor online es: huella de máquina + archivo de licencia firmado digitalmente.

Cómo funciona:

ACTIVACIÓN (una sola vez):
  PC cliente genera huella → te la manda → vos generás license.dat → cliente lo instala

ARRANQUE:
  App lee license.dat → verifica firma → verifica huella de esta PC → verifica vencimiento
  Si algo falla → modo bloqueado (solo muestra pantalla de error)
Huella de máquina
Combina datos del hardware que no cambian fácilmente:

Dirección MAC de la placa de red
Número de serie del disco
Nombre del equipo (hostname)

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

Modelo de negocio sugerido
Opción	Precio	Incluye
Licencia anual	$X/año	Actualizaciones + soporte
Licencia perpetua	$XX	App fija, sin updates
Multi-sucursal	$XX/pc	Misma farmacia, más equipos
Renovación: el expiry en la licencia vence en 1 año. Vos generás una licencia nueva con la misma huella y nuevo vencimiento → el cliente reemplaza el archivo.

Generar licencia (lo hacés vos, en tu PC)

import hmac, hashlib, json
from datetime import date

_SECRET = "clave-que-solo-vos-conoces-nunca-en-el-instalador"

def generate_license(pharmacy_name, fingerprint, expiry: str) -> str:
    data = {
        "farmacia": pharmacy_name,
        "fingerprint": fingerprint,
        "vence": expiry,  # "2027-04-09"
    }
    payload = json.dumps(data, sort_keys=True)
    sig = hmac.new(_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return json.dumps({"data": data, "sig": sig}, ensure_ascii=False)

    Verificar licencia (en el arranque de la app)
    def verify_license(license_path) -> tuple[bool, str]:
    try:
        obj = json.loads(open(license_path).read())
        payload = json.dumps(obj["data"], sort_keys=True)
        expected = hmac.new(_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if obj["sig"] != expected:
            return False, "Licencia inválida o manipulada."
        if get_fingerprint() != obj["data"]["fingerprint"]:
            return False, "Esta licencia no es válida para este equipo."
        if date.today().isoformat() > obj["data"]["vence"]:
            return False, f"Licencia vencida el {obj['data']['vence']}."
        return True, obj["data"]["farmacia"]
    except Exception:
        return False, "Archivo de licencia no encontrado o corrupto."

En app.py, antes del primer request:


@app.before_request
def check_license():
    valid, msg = verify_license('/app/license.dat')
    if not valid and request.endpoint not in ('license_error', 'static'):
        return render_template('license_error.html', mensaje=msg), 403


Lo que tenés que guardar vos (nunca en el instalador)

_SECRET = "tu-clave-privada"  # esto NUNCA va en el paquete que entregás
Esto va en un script separado que solo corre en tu PC para generar licencias. El instalador que entregás solo tiene el verificador, no el generador.

 Un script Python standalone que solo corre en tu PC, nunca se distribuye. Dado que ya tenés tkinter instalado (el docker_panel lo usa), lo más cómodo es una pequeña GUI:

license_generator.py — lo guardás en una carpeta privada tuya, no dentro del proyecto que entregás.


C:\MisFarmacias\
  ├── license_generator.py   ← solo vos
  ├── licencias_emitidas.csv ← registro de clientes
  └── licencias\
        ├── farmacia_san_martin.dat
        └── farmacia_lopez.dat    

La ventana haría:

Campo: nombre de la farmacia
Campo: huella (el cliente la obtiene desde la pantalla de activación del sistema)
Campo: fecha de vencimiento (date picker)
Botón "Generar licencia" → guarda el .dat y lo copia al portapapeles o lo guarda en la carpeta
¿Lo implemento?

**********************************

Write license_generator.py
173 lines
"""
Generador de licencias — AppFarmacia
⚠ ESTE ARCHIVO NO SE DISTRIBUYE AL CLIENTE. Solo para uso interno.
Requiere: Python 3.8+ con tkinter (incluido en instalación estándar).
"""
import hashlib
import hmac
import json
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import date, timedelta
import csv
import os

# ─── CLAVE PRIVADA ────────────────────────────────────────────────────────────
# Debe ser idéntica a la de license_utils.py en el proyecto.
_SECRET = "CAMBIAR-POR-UNA-CLAVE-SECRETA-LARGA-Y-UNICA"
# ──────────────────────────────────────────────────────────────────────────────

LOG_FILE = os.path.join(os.path.dirname(__file__), "licencias_emitidas.csv")


def generate_license(farmacia: str, fingerprint: str, expiry: str) -> str:
    data = {
        "farmacia": farmacia.strip(),
        "fingerprint": fingerprint.strip(),
        "vence": expiry,
        "emitida": date.today().isoformat(),
    }
    payload = json.dumps(data, sort_keys=True, ensure_ascii=False)
    sig = hmac.new(_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return json.dumps({"data": data, "sig": sig}, ensure_ascii=False, indent=2)


def log_license(farmacia, fingerprint, expiry, filepath):
    new_file = not os.path.exists(LOG_FILE)
    with open(LOG_FILE, 'a', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["Fecha emisión", "Farmacia", "Huella", "Vencimiento", "Archivo"])
        w.writerow([date.today().isoformat(), farmacia, fingerprint, expiry, filepath])


class LicenseGenerator(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Generador de Licencias — AppFarmacia")
        self.resizable(False, False)
        self.configure(bg="#1c1c1e")
        self._build_ui()

    def _build_ui(self):
        PAD = {"padx": 16, "pady": 6}
        BG = "#1c1c1e"
        SURF = "#2c2c2e"
        FG = "#f5f5f5"
        FG2 = "#888888"
        ACC = "#EAB308"
        FONT = ("Segoe UI", 10)
        FONT_SM = ("Segoe UI", 9)
        FONT_BOLD = ("Segoe UI", 10, "bold")

        # Título
        tk.Label(self, text="Generador de Licencias", bg=BG, fg=ACC,
                 font=("Segoe UI", 14, "bold")).pack(pady=(18, 2))
        tk.Label(self, text="AppFarmacia — uso interno", bg=BG, fg=FG2,
                 font=FONT_SM).pack(pady=(0, 14))

        frame = tk.Frame(self, bg=SURF, bd=0, relief="flat")
        frame.pack(padx=20, pady=0, fill="x")

        def lbl(text):
            tk.Label(frame, text=text, bg=SURF, fg=FG2,
                     font=FONT_SM, anchor="w").pack(fill="x", padx=14, pady=(10, 1))

        def entry(var, mono=False):
            f = ("Consolas", 10) if mono else FONT
            e = tk.Entry(frame, textvariable=var, bg="#3a3a3c", fg=FG,
                         insertbackground=FG, relief="flat", font=f,
                         highlightthickness=1, highlightbackground="#4a4a4c",
                         highlightcolor=ACC)
            e.pack(fill="x", padx=14, pady=(0, 2), ipady=5)
            return e

        # Nombre farmacia
        lbl("Nombre de la farmacia")
        self.var_farmacia = tk.StringVar()
        entry(self.var_farmacia)

        # Huella
        lbl("Huella de la máquina (obtenida del sistema del cliente)")
        self.var_fp = tk.StringVar()
        entry(self.var_fp, mono=True)

        # Vencimiento
        lbl("Vencimiento")
        frm_date = tk.Frame(frame, bg=SURF)
        frm_date.pack(fill="x", padx=14, pady=(0, 2))

        default_expiry = (date.today() + timedelta(days=365)).isoformat()
        self.var_expiry = tk.StringVar(value=default_expiry)
        e_exp = tk.Entry(frm_date, textvariable=self.var_expiry, bg="#3a3a3c", fg=FG,
                         insertbackground=FG, relief="flat", font=("Consolas", 10),
                         width=14, highlightthickness=1, highlightbackground="#4a4a4c",
                         highlightcolor=ACC)
        e_exp.pack(side="left", ipady=5)

        for label, days in [("1 año", 365), ("2 años", 730), ("6 meses", 180)]:
            d = days
            tk.Button(frm_date, text=label, bg="#3a3a3c", fg=FG2,
                      font=FONT_SM, relief="flat", cursor="hand2",
                      command=lambda d=d: self.var_expiry.set(
                          (date.today() + timedelta(days=d)).isoformat()
                      )).pack(side="left", padx=(6, 0), ipady=3, ipadx=6)

        tk.Frame(frame, bg="#3a3a3c", height=1).pack(fill="x", padx=14, pady=12)

        # Botón generar
        tk.Button(frame, text="Generar y guardar licencia",
                  bg=ACC, fg="#0d0d0d", font=FONT_BOLD,
                  relief="flat", cursor="hand2", pady=8,
                  command=self._generate).pack(fill="x", padx=14, pady=(0, 14))

        # Output
        lbl_out = tk.Label(self, text="", bg=BG, fg=FG2, font=FONT_SM,
                           wraplength=420, justify="left")
        lbl_out.pack(padx=20, pady=(10, 18))
        self.lbl_out = lbl_out

    def _generate(self):
        farmacia = self.var_farmacia.get().strip()
        fp = self.var_fp.get().strip()
        expiry = self.var_expiry.get().strip()

        if not farmacia:
            messagebox.showwarning("Falta dato", "Ingresá el nombre de la farmacia.")
            return
        if not fp:
            messagebox.showwarning("Falta dato", "Ingresá la huella de la máquina.")
            return
        try:
            date.fromisoformat(expiry)
        except ValueError:
            messagebox.showwarning("Fecha inválida", "Formato de fecha: AAAA-MM-DD")
            return

        content = generate_license(farmacia, fp, expiry)

        filepath = filedialog.asksaveasfilename(
            title="Guardar licencia",
            defaultextension=".dat",
            initialfile=f"license_{farmacia.lower().replace(' ', '_')}.dat",
            filetypes=[("Archivo de licencia", "*.dat"), ("Todos", "*.*")]
        )
        if not filepath:
            return

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)

        log_license(farmacia, fp, expiry, filepath)

        self.lbl_out.config(
            fg="#4ade80",
            text=f"✓ Licencia generada: {os.path.basename(filepath)}\n"
                 f"Farmacia: {farmacia}\nVence: {expiry}\nRegistrada en licencias_emitidas.csv"
        )


if __name__ == "__main__":
    app = LicenseGenerator()
    app.mainloop()
Los dos archivos están listos. Resumen:

license_utils.py — va dentro del proyecto que entregás al cliente:

get_fingerprint() → genera la huella de la PC
verify_license() → verifica firma, huella y vencimiento
Por ahora no está conectado a la app (no hay @app.before_request)
license_generator.py — solo para vos, no se distribuye:

GUI con tkinter
Campos: nombre farmacia, huella, vencimiento (default 1 año, botones rápidos)
Guarda el .dat donde vos elijas
Registra cada licencia emitida en licencias_emitidas.csv

Antes de usar, cambiá _SECRET por una clave larga en los dos archivos (tienen que ser idénticas). Podés generar una con:


python -c "import secrets; print(secrets.token_hex(32))"
Cuando quieras activar la validación en el sistema, lo enganchamos al arranque de Flask.