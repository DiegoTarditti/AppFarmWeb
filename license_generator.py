"""
Generador de licencias — AppFarmacia
⚠ ESTE ARCHIVO NO SE DISTRIBUYE AL CLIENTE. Solo para uso interno.
Requiere: Python 3.8+ con tkinter (incluido en instalación estándar).
"""
import csv
import hashlib
import hmac
import json
import os
import tkinter as tk
from datetime import date, timedelta
from tkinter import filedialog, messagebox, ttk

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
