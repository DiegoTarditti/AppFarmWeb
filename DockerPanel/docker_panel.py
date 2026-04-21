# ──────────────────────────────────────────────────────────────────────────────
# Panel de control Docker + scanner de facturas.
#
# Incluye la opción de buscar PDFs en \facturas\pendientes para detectar
# documentos nuevos y procesarlos desde esta interfaz.
# ──────────────────────────────────────────────────────────────────────────────

import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
import subprocess
import threading
import queue
import os
import datetime

# === BEGIN HELPER HTTP (copy to unified panel) ===
# Mini servidor HTTP local para que el frontend hosteado (Render) pueda
# listar / leer PDFs desde la máquina de la farmacia.
import http.server
import json
import urllib.parse
import urllib.request
import urllib.error
import time

HELPER_PORT = 5055
HELPER_ALLOWED_ORIGINS = {
    "https://farmacia-web-rj1z.onrender.com",
    "http://localhost:5001",
    "http://127.0.0.1:5001",
}
# === END HELPER HTTP ===

# === BEGIN KEEPALIVE RENDER (copy to unified panel) ===
# Thread que pingea Render periódicamente para evitar que se duerma el
# servicio free. Lee su config del mismo agente_config.txt.
KEEPALIVE_DEFAULT_URL = "https://farmacia-web-rj1z.onrender.com/health_web"
KEEPALIVE_DEFAULT_MIN = 10
# === END KEEPALIVE RENDER ===

# ── Configuración de comandos ──────────────────────────────────────────────────
COMMANDS = [
    ("🔄  Reiniciar Web",         "docker-compose restart web",              "#2563EB"),
    ("🏗️  Rebuild Web",           "docker-compose build web",                "#7C3AED"),
    ("🏗️  Rebuild Todo",          "docker-compose build",                    "#7C3AED"),
    ("▶️  Iniciar (up -d)",       "docker-compose up -d",                    "#16A34A"),
    ("⏹️  Detener (down)",        "docker-compose down",                     "#DC2626"),
    ("📋  Logs Web (50 líneas)",  "docker-compose logs --tail=50 web",       "#D97706"),
    ("📋  Logs DB (50 líneas)",   "docker-compose logs --tail=50 db",        "#D97706"),
    ("📊  Estado contenedores",   "docker-compose ps",                       "#0891B2"),
    ("🧹  Limpiar imágenes",      "docker image prune -f",                   "#6B7280"),
    ("🧹  Limpiar todo (prune)",  "docker system prune -f",                  "#6B7280"),
]

BG       = "#1c1c1e"
SURFACE  = "#2c2c2e"
BORDER   = "#3a3a3c"
FG       = "#f5f5f5"
FG_DIM   = "#888888"
BRAND    = "#EAB308"
GREEN    = "#4ADE80"
RED      = "#F87171"
YELLOW   = "#FBBF24"


# ── Diálogo de selección de proyecto ──────────────────────────────────────────

class _StartupDialog(tk.Toplevel):
    """Pide al usuario que elija el directorio del proyecto Docker antes de abrir el panel."""

    def __init__(self, master):
        super().__init__(master)
        self.result = None
        self.title("Abrir proyecto")
        self.configure(bg=BG)
        self.resizable(False, False)
        self._build()
        self._center(500, 220)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.wait_window()

    def _center(self, w, h):
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

    def _build(self):
        tk.Label(self, text="Seleccioná el directorio del proyecto Docker",
                 font=("Segoe UI", 10, "bold"), bg=BG, fg=FG
                 ).pack(padx=20, pady=(18, 8), anchor="w")

        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", padx=20)

        _default = r"C:\AppFarmWeb" if os.path.isdir(r"C:\AppFarmWeb") else ""
        self._path_var = tk.StringVar(value=_default)
        self._entry = tk.Entry(row, textvariable=self._path_var,
                               font=("Consolas", 9), bg=SURFACE, fg=FG,
                               insertbackground=FG, relief="flat", bd=4)
        self._entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self._entry.bind("<Return>", lambda _: self._confirm())

        tk.Button(row, text="Buscar…", font=("Segoe UI", 9),
                  bg=SURFACE, fg=BRAND, activebackground=BORDER,
                  activeforeground=BRAND, relief="flat", cursor="hand2",
                  command=self._browse).pack(side="left")

        self._warn_lbl = tk.Label(self, text="", font=("Segoe UI", 8),
                                   bg=BG, fg=RED)
        self._warn_lbl.pack(padx=20, anchor="w", pady=(4, 0))

        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x", padx=20, pady=(6, 16))

        tk.Button(btns, text="Cancelar", font=("Segoe UI", 9),
                  bg=SURFACE, fg=FG_DIM, activebackground=BORDER,
                  activeforeground=FG, relief="flat", cursor="hand2",
                  command=self._cancel).pack(side="right", padx=(6, 0))

        self._open_btn = tk.Button(btns, text="Abrir proyecto",
                                    font=("Segoe UI", 9, "bold"),
                                    bg=BRAND, fg=BG, activebackground="#ca9c07",
                                    activeforeground=BG, relief="flat",
                                    cursor="hand2", command=self._confirm)
        self._open_btn.pack(side="right")

    def _browse(self):
        initial = self._path_var.get() or os.path.expanduser("~")
        d = filedialog.askdirectory(parent=self, initialdir=initial)
        if d:
            self._path_var.set(d)
            self._warn_lbl.config(text="")
            self._open_btn.config(text="Abrir proyecto")

    def _confirm(self):
        d = self._path_var.get().strip()
        if not d or not os.path.isdir(d):
            self._warn_lbl.config(text="⚠  Directorio inválido.")
            return
        if not os.path.exists(os.path.join(d, "docker-compose.yml")):
            if self._open_btn.cget("text") != "Abrir de todas formas":
                self._warn_lbl.config(text="⚠  No se encontró docker-compose.yml en ese directorio.")
                self._open_btn.config(text="Abrir de todas formas")
                return
        self.result = d
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


# ── Panel principal ────────────────────────────────────────────────────────────

class DockerPanel(tk.Tk):
    def __init__(self):
        super().__init__()
        self.withdraw()

        self.title("Docker Panel")
        self.configure(bg=BG)
        self.resizable(True, True)
        self.minsize(820, 560)

        self._running = False
        self._proc = None
        self._status_indicators = {}  # name → (dot_label, text_label)
        self._queue = queue.Queue()   # cola de comandos secuencial

        initial_dir = self._ask_project_dir()
        if initial_dir is None:
            self.destroy()
            return

        self._build_ui(initial_dir)
        self._center_window(960, 660)
        self.deiconify()
        self._refresh_status()

        # Hilo worker permanente que consume la cola
        threading.Thread(target=self._queue_worker, daemon=True).start()

        # === BEGIN HELPER HTTP (copy to unified panel) ===
        self._helper_server = None
        threading.Thread(target=_start_helper_server, args=(self,), daemon=True).start()
        # === END HELPER HTTP ===

        # === BEGIN KEEPALIVE RENDER (copy to unified panel) ===
        self._keepalive_stop = threading.Event()
        threading.Thread(target=self._keepalive_loop, daemon=True).start()
        self.after(500, self._update_keepalive_label)
        # === END KEEPALIVE RENDER ===

    def _ask_project_dir(self):
        dlg = _StartupDialog(self)
        return dlg.result

    def _center_window(self, w, h):
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self, initial_dir):
        # ── Header ──
        header = tk.Frame(self, bg=SURFACE, pady=8)
        header.pack(fill="x")
        tk.Label(header, text="⚙  Docker Panel", font=("Segoe UI", 13, "bold"),
                 bg=SURFACE, fg=FG).pack(side="left", padx=16)

        # ── Status bar (contenedores + imágenes) ──
        self._status_bar = tk.Frame(self, bg="#111113", pady=8)
        self._status_bar.pack(fill="x")
        self._build_status_bar()

        # ── Working directory bar ──
        dir_bar = tk.Frame(self, bg=BORDER, pady=6)
        dir_bar.pack(fill="x")
        tk.Label(dir_bar, text="Directorio:", font=("Segoe UI", 9),
                 bg=BORDER, fg=FG_DIM).pack(side="left", padx=(12, 4))
        self.dir_var = tk.StringVar(value=initial_dir)
        tk.Entry(dir_bar, textvariable=self.dir_var, font=("Consolas", 9),
                 bg=SURFACE, fg=FG, insertbackground=FG, relief="flat",
                 bd=0).pack(side="left", fill="x", expand=True, padx=(0, 6))
        tk.Button(dir_bar, text="Buscar…", font=("Segoe UI", 9),
                  bg=SURFACE, fg=BRAND, activebackground=BORDER,
                  activeforeground=BRAND, relief="flat", cursor="hand2",
                  command=self._pick_dir).pack(side="left", padx=(0, 10))

        # ── Body ──
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=12, pady=10)

        # Left: botones (con scroll vertical)
        left_wrap = tk.Frame(body, bg=BG, width=246)
        left_wrap.pack(side="left", fill="y", padx=(0, 10))
        left_wrap.pack_propagate(False)

        left_canvas = tk.Canvas(left_wrap, bg=BG, highlightthickness=0, bd=0)
        left_scroll = tk.Scrollbar(left_wrap, orient="vertical",
                                   command=left_canvas.yview)
        left_canvas.configure(yscrollcommand=left_scroll.set)
        left_scroll.pack(side="right", fill="y")
        left_canvas.pack(side="left", fill="both", expand=True)

        left = tk.Frame(left_canvas, bg=BG)
        left_window = left_canvas.create_window((0, 0), window=left, anchor="nw")

        def _on_left_configure(_e=None):
            left_canvas.configure(scrollregion=left_canvas.bbox("all"))
            left_canvas.itemconfig(left_window, width=left_canvas.winfo_width())
        left.bind("<Configure>", _on_left_configure)
        left_canvas.bind("<Configure>", _on_left_configure)

        # Scroll con la rueda del mouse cuando el cursor está sobre el panel
        def _on_mousewheel(event):
            left_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        left_canvas.bind("<Enter>", lambda e: left_canvas.bind_all("<MouseWheel>", _on_mousewheel))
        left_canvas.bind("<Leave>", lambda e: left_canvas.unbind_all("<MouseWheel>"))

        tk.Label(left, text="COMANDOS", font=("Segoe UI", 8, "bold"),
                 bg=BG, fg=FG_DIM).pack(anchor="w", pady=(0, 6))

        for label, cmd, color in COMMANDS:
            self._make_btn(left, label, cmd, color)

        tk.Frame(left, bg=BORDER, height=1).pack(fill="x", pady=8)

        # ── Backup buttons ──
        tk.Label(left, text="BACKUP BASE DE DATOS", font=("Segoe UI", 8, "bold"),
                 bg=BG, fg=FG_DIM).pack(anchor="w", pady=(0, 4))

        btn_bk = tk.Button(
            left, text="💾  Backup ahora",
            font=("Segoe UI", 9, "bold"),
            bg="#1a3a1a", fg=GREEN,
            activebackground="#2a4a2a", activeforeground=GREEN,
            relief="flat", cursor="hand2", pady=7, anchor="w", padx=10,
            command=self._backup_now
        )
        btn_bk.pack(fill="x", pady=2)
        btn_bk.bind("<Enter>", lambda e: btn_bk.config(bg="#2a5a2a"))
        btn_bk.bind("<Leave>", lambda e: btn_bk.config(bg="#1a3a1a"))

        btn_bkf = tk.Button(
            left, text="📂  Backup a carpeta…",
            font=("Segoe UI", 9),
            bg=SURFACE, fg=FG,
            activebackground=BORDER, activeforeground=FG,
            relief="flat", cursor="hand2", pady=7, anchor="w", padx=10,
            command=self._backup_to_folder
        )
        btn_bkf.pack(fill="x", pady=2)
        btn_bkf.bind("<Enter>", lambda e: btn_bkf.config(bg=BORDER))
        btn_bkf.bind("<Leave>", lambda e: btn_bkf.config(bg=SURFACE))

        btn_rs = tk.Button(
            left, text="♻  Restore desde archivo…",
            font=("Segoe UI", 9, "bold"),
            bg="#3a2a1a", fg=YELLOW,
            activebackground="#4a3a2a", activeforeground=YELLOW,
            relief="flat", cursor="hand2", pady=7, anchor="w", padx=10,
            command=self._restore_from_file
        )
        btn_rs.pack(fill="x", pady=2)
        btn_rs.bind("<Enter>", lambda e: btn_rs.config(bg="#5a3a2a"))
        btn_rs.bind("<Leave>", lambda e: btn_rs.config(bg="#3a2a1a"))

        tk.Frame(left, bg=BORDER, height=1).pack(fill="x", pady=8)

        # ── Agente pendientes ──
        tk.Label(left, text="AGENTE PENDIENTES", font=("Segoe UI", 8, "bold"),
                 bg=BG, fg=FG_DIM).pack(anchor="w", pady=(0, 4))

        btn_ag = tk.Button(
            left, text="📤  Subir PDFs a Render",
            font=("Segoe UI", 9, "bold"),
            bg="#1a2a3a", fg="#67E8F9",
            activebackground="#2a3a4a", activeforeground="#67E8F9",
            relief="flat", cursor="hand2", pady=7, anchor="w", padx=10,
            command=self._run_agente_pendientes
        )
        btn_ag.pack(fill="x", pady=2)
        btn_ag.bind("<Enter>", lambda e: btn_ag.config(bg="#2a4a5a"))
        btn_ag.bind("<Leave>", lambda e: btn_ag.config(bg="#1a2a3a"))

        btn_cfg = tk.Button(
            left, text="⚙  Configurar agente…",
            font=("Segoe UI", 9),
            bg=SURFACE, fg=FG,
            activebackground=BORDER, activeforeground=FG,
            relief="flat", cursor="hand2", pady=7, anchor="w", padx=10,
            command=self._config_agente
        )
        btn_cfg.pack(fill="x", pady=2)
        btn_cfg.bind("<Enter>", lambda e: btn_cfg.config(bg=BORDER))
        btn_cfg.bind("<Leave>", lambda e: btn_cfg.config(bg=SURFACE))

        tk.Frame(left, bg=BORDER, height=1).pack(fill="x", pady=8)

        tk.Button(left, text="⛔  Detener proceso",
                  font=("Segoe UI", 9, "bold"),
                  bg="#3a1a1a", fg=RED,
                  activebackground="#4a2a2a", activeforeground=RED,
                  relief="flat", cursor="hand2", pady=7,
                  command=self._stop_proc).pack(fill="x")

        tk.Frame(left, bg=BORDER, height=1).pack(fill="x", pady=8)
        tk.Button(left, text="🗑  Limpiar consola",
                  font=("Segoe UI", 9),
                  bg=SURFACE, fg=FG_DIM,
                  activebackground=BORDER, activeforeground=FG,
                  relief="flat", cursor="hand2", pady=6,
                  command=self._clear_log).pack(fill="x")

        # Right: consola
        right = tk.Frame(body, bg=BG)
        right.pack(side="left", fill="both", expand=True)

        top_bar = tk.Frame(right, bg=BG)
        top_bar.pack(fill="x", pady=(0, 6))
        tk.Label(top_bar, text="CONSOLA", font=("Segoe UI", 8, "bold"),
                 bg=BG, fg=FG_DIM).pack(side="left")
        self.status_lbl = tk.Label(top_bar, text="● Listo",
                                   font=("Segoe UI", 8), bg=BG, fg=GREEN)
        self.status_lbl.pack(side="right")

        # Botón refrescar estado
        tk.Button(top_bar, text="↺ Refrescar estado",
                  font=("Segoe UI", 8),
                  bg=SURFACE, fg=FG_DIM,
                  activebackground=BORDER, activeforeground=FG,
                  relief="flat", cursor="hand2", padx=6, pady=2,
                  command=self._refresh_status).pack(side="right", padx=(0, 8))

        # Badge cola + botón vaciar
        tk.Button(top_bar, text="✕ Vaciar cola",
                  font=("Segoe UI", 8),
                  bg=SURFACE, fg=FG_DIM,
                  activebackground=BORDER, activeforeground=RED,
                  relief="flat", cursor="hand2", padx=6, pady=2,
                  command=self._clear_queue).pack(side="right", padx=(0, 4))
        self.queue_badge = tk.Label(top_bar, text="Cola: vacía",
                                    font=("Segoe UI", 8), bg=BG, fg=FG_DIM)
        self.queue_badge.pack(side="right", padx=(0, 6))

        self.log = scrolledtext.ScrolledText(
            right, font=("Consolas", 9), bg=SURFACE, fg=FG,
            insertbackground=FG, relief="flat", bd=0,
            wrap="word", state="disabled"
        )
        self.log.pack(fill="both", expand=True)

        self.log.tag_config("ok",    foreground=GREEN)
        self.log.tag_config("err",   foreground=RED)
        self.log.tag_config("cmd",   foreground=BRAND)
        self.log.tag_config("dim",   foreground=FG_DIM)
        self.log.tag_config("bk",    foreground="#67E8F9")

    def _build_status_bar(self):
        """Construye los indicadores de estado en la barra superior."""
        for w in self._status_bar.winfo_children():
            w.destroy()
        self._status_indicators = {}

        tk.Label(self._status_bar, text="ESTADO:", font=("Segoe UI", 8, "bold"),
                 bg="#111113", fg=FG_DIM).pack(side="left", padx=(14, 8))

        for name in ("web", "db"):
            frame = tk.Frame(self._status_bar, bg="#111113")
            frame.pack(side="left", padx=6)
            dot = tk.Label(frame, text="●", font=("Segoe UI", 11),
                           bg="#111113", fg=FG_DIM)
            dot.pack(side="left")
            lbl = tk.Label(frame, text=name, font=("Segoe UI", 8),
                           bg="#111113", fg=FG_DIM)
            lbl.pack(side="left", padx=(2, 0))
            self._status_indicators[name] = (dot, lbl)

        # Separador
        tk.Label(self._status_bar, text="│", bg="#111113", fg=BORDER).pack(side="left", padx=8)

        # Imágenes
        tk.Label(self._status_bar, text="IMÁGENES:", font=("Segoe UI", 8, "bold"),
                 bg="#111113", fg=FG_DIM).pack(side="left", padx=(0, 8))

        for name, display in (("web", "web (local)"), ("db", "postgres:15")):
            frame = tk.Frame(self._status_bar, bg="#111113")
            frame.pack(side="left", padx=6)
            dot = tk.Label(frame, text="●", font=("Segoe UI", 11),
                           bg="#111113", fg=FG_DIM)
            dot.pack(side="left")
            lbl = tk.Label(frame, text=display, font=("Segoe UI", 8),
                           bg="#111113", fg=FG_DIM)
            lbl.pack(side="left", padx=(2, 0))
            self._status_indicators[f"img:{name}"] = (dot, lbl)

        # Timestamp
        self._status_time_lbl = tk.Label(self._status_bar, text="",
                                          font=("Segoe UI", 7), bg="#111113", fg=FG_DIM)
        self._status_time_lbl.pack(side="right", padx=14)

        # === BEGIN HELPER HTTP (copy to unified panel) ===
        tk.Label(self._status_bar, text="│", bg="#111113", fg=BORDER).pack(side="right", padx=8)
        self._helper_lbl = tk.Label(
            self._status_bar,
            text=f"● helper :{HELPER_PORT} (iniciando…)",
            font=("Segoe UI", 8),
            bg="#111113", fg=YELLOW,
        )
        self._helper_lbl.pack(side="right", padx=4)
        # === END HELPER HTTP ===

        # === BEGIN KEEPALIVE RENDER (copy to unified panel) ===
        tk.Label(self._status_bar, text="│", bg="#111113", fg=BORDER).pack(side="right", padx=8)
        self._keepalive_lbl = tk.Label(
            self._status_bar,
            text="○ keep-alive off",
            font=("Segoe UI", 8),
            bg="#111113", fg=FG_DIM,
        )
        self._keepalive_lbl.pack(side="right", padx=4)
        # === END KEEPALIVE RENDER ===

    def _refresh_status(self):
        """Consulta Docker en background y actualiza los indicadores."""
        threading.Thread(target=self._check_docker_status, daemon=True).start()

    def _check_docker_status(self):
        cwd = self.dir_var.get()

        # Contenedores corriendo
        try:
            result = subprocess.run(
                "docker-compose ps --services --filter status=running",
                shell=True, cwd=cwd,
                capture_output=True, text=True, timeout=10
            )
            running_services = set(result.stdout.strip().splitlines())
        except Exception:
            running_services = set()

        # Imágenes del proyecto (docker-compose images lista solo las del proyecto)
        try:
            result = subprocess.run(
                "docker-compose images -q",
                shell=True, cwd=cwd,
                capture_output=True, text=True, timeout=10
            )
            # Si devuelve IDs, hay imágenes; si no, no hay
            img_ids = [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]
        except Exception:
            img_ids = []

        # Verificar imagen web (buildeada) y postgres por separado
        try:
            r_web = subprocess.run(
                "docker-compose images web",
                shell=True, cwd=cwd,
                capture_output=True, text=True, timeout=10
            )
            web_has_image = len(r_web.stdout.strip().splitlines()) > 1  # header + al menos 1 fila
        except Exception:
            web_has_image = False

        try:
            r_db = subprocess.run(
                "docker-compose images db",
                shell=True, cwd=cwd,
                capture_output=True, text=True, timeout=10
            )
            db_has_image = len(r_db.stdout.strip().splitlines()) > 1
        except Exception:
            db_has_image = False

        def _update():
            for name in ("web", "db"):
                dot, lbl = self._status_indicators[name]
                if name in running_services:
                    dot.config(fg=GREEN)
                    lbl.config(fg=GREEN)
                else:
                    dot.config(fg=RED)
                    lbl.config(fg=RED)

            for img_key, has_img in (("web", web_has_image), ("db", db_has_image)):
                dot, lbl = self._status_indicators[f"img:{img_key}"]
                if has_img:
                    dot.config(fg=GREEN)
                    lbl.config(fg=GREEN)
                else:
                    dot.config(fg=YELLOW)
                    lbl.config(fg=YELLOW)

            now = datetime.datetime.now().strftime("%H:%M:%S")
            self._status_time_lbl.config(text=f"actualizado {now}")

        self.after(0, _update)

    # ── Backup ────────────────────────────────────────────────────────────────

    def _get_backup_folder(self):
        """Devuelve la carpeta backups/ dentro del proyecto, creándola si no existe."""
        folder = os.path.join(self.dir_var.get(), "backups")
        os.makedirs(folder, exist_ok=True)
        return folder

    def _run_backup(self, dest_folder):
        """Ejecuta pg_dump dentro del contenedor db y guarda el .sql."""
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"farmacia_{ts}.sql"
        dest = os.path.join(dest_folder, filename)

        cmd = (
            f'docker-compose exec -T db '
            f'pg_dump -U postgres farmacia > "{dest}"'
        )
        self._running = True
        cwd = self.dir_var.get()
        self.after(0, self._append, f"\n💾  Backup → {dest}\n", "bk")
        self.after(0, self._set_status, "● Ejecutando…", YELLOW)

        try:
            proc = subprocess.run(
                cmd, shell=True, cwd=cwd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", timeout=120
            )
            if proc.returncode == 0 and os.path.getsize(dest) > 0:
                self.after(0, self._append, f"✔  Backup completado: {filename}\n", "ok")
                self.after(0, self._set_status, "● Listo", GREEN)
            else:
                output = proc.stdout.strip() if proc.stdout else "sin salida"
                self.after(0, self._append, f"✖  Error en backup (rc={proc.returncode}): {output}\n", "err")
                self.after(0, self._set_status, "● Error", RED)
                # eliminar archivo vacío si se creó
                if os.path.exists(dest) and os.path.getsize(dest) == 0:
                    os.remove(dest)
        except Exception as e:
            self.after(0, self._append, f"✖  {e}\n", "err")
            self.after(0, self._set_status, "● Error", RED)
        finally:
            self._running = False

    def _backup_now(self):
        folder = self._get_backup_folder()
        self._queue.put(("backup", folder))
        self._update_queue_badge()
        self._append(f"  ↳ backup encolado → {folder}\n", "dim")

    def _backup_to_folder(self):
        folder = filedialog.askdirectory(
            parent=self,
            initialdir=self._get_backup_folder(),
            title="Elegir carpeta destino del backup"
        )
        if folder:
            self._queue.put(("backup", folder))
            self._update_queue_badge()
            self._append(f"  ↳ backup encolado → {folder}\n", "dim")

    # ── Restore ───────────────────────────────────────────────────────────────

    def _restore_from_file(self):
        src = filedialog.askopenfilename(
            parent=self,
            initialdir=self._get_backup_folder(),
            title="Elegir archivo .sql para restaurar",
            filetypes=[("SQL dumps", "*.sql"), ("Todos", "*.*")],
        )
        if not src:
            return
        if not messagebox.askyesno(
            "Confirmar restore",
            "⚠  Esto REEMPLAZARÁ toda la base 'farmacia' con:\n\n"
            f"{os.path.basename(src)}\n\n"
            "Las conexiones activas serán cerradas. ¿Continuar?",
            parent=self, icon="warning",
        ):
            return
        self._queue.put(("restore", src))
        self._update_queue_badge()
        self._append(f"  ↳ restore encolado ← {src}\n", "dim")

    def _run_restore(self, src):
        """Drop+create DB y carga el dump SQL."""
        self._running = True
        cwd = self.dir_var.get()
        fname = os.path.basename(src)
        self.after(0, self._append, f"\n♻  Restore ← {src}\n", "bk")
        self.after(0, self._set_status, "● Ejecutando…", YELLOW)

        steps = [
            (
                "Cerrando conexiones + drop database",
                'docker-compose exec -T db psql -U postgres -d postgres -c '
                '"DROP DATABASE IF EXISTS farmacia WITH (FORCE);"',
            ),
            (
                "Creando database vacía",
                'docker-compose exec -T db psql -U postgres -d postgres -c '
                '"CREATE DATABASE farmacia;"',
            ),
            (
                f"Cargando dump {fname}",
                f'docker-compose exec -T db psql -U postgres -d farmacia < "{src}"',
            ),
        ]

        try:
            for label, cmd in steps:
                self.after(0, self._append, f"  → {label}\n", "dim")
                proc = subprocess.run(
                    cmd, shell=True, cwd=cwd,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", timeout=600
                )
                if proc.stdout:
                    for line in proc.stdout.splitlines():
                        if not line.strip():
                            continue
                        is_err = any(w in line.lower() for w in ("error", "fatal"))
                        self.after(0, self._append, f"    {line}\n", "err" if is_err else "dim")
                if proc.returncode != 0:
                    self.after(0, self._append,
                               f"✖  Falló (rc={proc.returncode}) en: {label}\n", "err")
                    self.after(0, self._set_status, "● Error", RED)
                    return

            self.after(0, self._append, f"✔  Restore completado desde {fname}\n", "ok")
            self.after(0, self._append,
                       "  ⚠  Reiniciá Web para que tome la nueva base.\n", "dim")
            self.after(0, self._set_status, "● Listo", GREEN)
        except Exception as e:
            self.after(0, self._append, f"✖  {e}\n", "err")
            self.after(0, self._set_status, "● Error", RED)
        finally:
            self._running = False

    # ── Agente pendientes ────────────────────────────────────────────────────

    def _get_agente_config_path(self):
        return os.path.join(self.dir_var.get(), "docker_panel", "agente_config.txt")

    def _load_agente_config(self):
        """Carga carpeta y URL del agente desde archivo de config."""
        cfg_path = self._get_agente_config_path()
        carpeta = ""
        url = "https://farmacia-web-rj1z.onrender.com"
        mover = True
        # === BEGIN KEEPALIVE RENDER (copy to unified panel) ===
        keepalive = False
        keepalive_url = KEEPALIVE_DEFAULT_URL
        keepalive_min = KEEPALIVE_DEFAULT_MIN
        # === END KEEPALIVE RENDER ===
        if os.path.isfile(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("carpeta="):
                        carpeta = line.split("=", 1)[1]
                    elif line.startswith("url="):
                        url = line.split("=", 1)[1]
                    elif line.startswith("mover="):
                        mover = line.split("=", 1)[1].lower() in ("true", "1", "si")
                    # === BEGIN KEEPALIVE RENDER (copy to unified panel) ===
                    elif line.startswith("keepalive_url="):
                        keepalive_url = line.split("=", 1)[1]
                    elif line.startswith("keepalive_min="):
                        try: keepalive_min = max(1, min(60, int(line.split("=", 1)[1])))
                        except ValueError: pass
                    elif line.startswith("keepalive="):
                        keepalive = line.split("=", 1)[1].lower() in ("true", "1", "si")
                    # === END KEEPALIVE RENDER ===
        return carpeta, url, mover, keepalive, keepalive_url, keepalive_min

    def _save_agente_config(self, carpeta, url, mover, keepalive=False,
                             keepalive_url=KEEPALIVE_DEFAULT_URL,
                             keepalive_min=KEEPALIVE_DEFAULT_MIN):
        cfg_path = self._get_agente_config_path()
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write(f"carpeta={carpeta}\n")
            f.write(f"url={url}\n")
            f.write(f"mover={'true' if mover else 'false'}\n")
            # === BEGIN KEEPALIVE RENDER (copy to unified panel) ===
            f.write(f"keepalive={'true' if keepalive else 'false'}\n")
            f.write(f"keepalive_url={keepalive_url}\n")
            f.write(f"keepalive_min={keepalive_min}\n")
            # === END KEEPALIVE RENDER ===

    def _config_agente(self):
        """Abre diálogo para configurar carpeta y URL del agente."""
        carpeta, url, mover, keepalive, ka_url, ka_min = self._load_agente_config()

        dlg = tk.Toplevel(self)
        dlg.title("Configurar Agente Pendientes")
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        dlg.grab_set()

        # Carpeta
        tk.Label(dlg, text="Carpeta de PDFs:", font=("Segoe UI", 9, "bold"),
                 bg=BG, fg=FG).pack(anchor="w", padx=16, pady=(14, 2))
        row1 = tk.Frame(dlg, bg=BG)
        row1.pack(fill="x", padx=16)
        carpeta_var = tk.StringVar(value=carpeta)
        tk.Entry(row1, textvariable=carpeta_var, font=("Consolas", 9),
                 bg=SURFACE, fg=FG, insertbackground=FG, relief="flat", bd=4
                 ).pack(side="left", fill="x", expand=True, padx=(0, 6))
        tk.Button(row1, text="Buscar…", font=("Segoe UI", 9),
                  bg=SURFACE, fg=BRAND, activebackground=BORDER,
                  activeforeground=BRAND, relief="flat", cursor="hand2",
                  command=lambda: carpeta_var.set(
                      filedialog.askdirectory(parent=dlg, initialdir=carpeta_var.get() or os.path.expanduser("~")) or carpeta_var.get()
                  )).pack(side="left")

        # URL
        tk.Label(dlg, text="URL de la app:", font=("Segoe UI", 9, "bold"),
                 bg=BG, fg=FG).pack(anchor="w", padx=16, pady=(10, 2))
        url_var = tk.StringVar(value=url)
        tk.Entry(dlg, textvariable=url_var, font=("Consolas", 9),
                 bg=SURFACE, fg=FG, insertbackground=FG, relief="flat", bd=4
                 ).pack(fill="x", padx=16)

        # Mover
        mover_var = tk.BooleanVar(value=mover)
        tk.Checkbutton(dlg, text="Mover PDFs a subcarpeta 'enviados/' después de subir",
                       variable=mover_var, font=("Segoe UI", 9),
                       bg=BG, fg=FG, selectcolor=SURFACE, activebackground=BG,
                       activeforeground=FG).pack(anchor="w", padx=16, pady=(10, 0))

        # === BEGIN KEEPALIVE RENDER (copy to unified panel) ===
        tk.Frame(dlg, bg=BORDER, height=1).pack(fill="x", padx=16, pady=(14, 8))
        tk.Label(dlg, text="KEEP-ALIVE RENDER", font=("Segoe UI", 8, "bold"),
                 bg=BG, fg=FG_DIM).pack(anchor="w", padx=16)
        tk.Label(dlg, text="Pinguea Render periódicamente para evitar que se duerma el servicio free.",
                 font=("Segoe UI", 8), bg=BG, fg=FG_DIM, wraplength=460, justify="left"
                 ).pack(anchor="w", padx=16, pady=(0, 4))

        ka_enabled_var = tk.BooleanVar(value=keepalive)
        tk.Checkbutton(dlg, text="Activar keep-alive (ping a Render)",
                       variable=ka_enabled_var, font=("Segoe UI", 9),
                       bg=BG, fg=FG, selectcolor=SURFACE, activebackground=BG,
                       activeforeground=FG).pack(anchor="w", padx=16)

        tk.Label(dlg, text="URL a pinguear:", font=("Segoe UI", 9),
                 bg=BG, fg=FG).pack(anchor="w", padx=16, pady=(8, 2))
        ka_url_var = tk.StringVar(value=ka_url)
        tk.Entry(dlg, textvariable=ka_url_var, font=("Consolas", 9),
                 bg=SURFACE, fg=FG, insertbackground=FG, relief="flat", bd=4
                 ).pack(fill="x", padx=16)

        row_int = tk.Frame(dlg, bg=BG)
        row_int.pack(fill="x", padx=16, pady=(8, 0))
        tk.Label(row_int, text="Intervalo (min):", font=("Segoe UI", 9),
                 bg=BG, fg=FG).pack(side="left")
        ka_min_var = tk.StringVar(value=str(ka_min))
        tk.Entry(row_int, textvariable=ka_min_var, font=("Consolas", 9), width=6,
                 bg=SURFACE, fg=FG, insertbackground=FG, relief="flat", bd=4
                 ).pack(side="left", padx=(8, 0))
        tk.Label(row_int, text="(1–60)", font=("Segoe UI", 8),
                 bg=BG, fg=FG_DIM).pack(side="left", padx=(6, 0))
        # === END KEEPALIVE RENDER ===

        # Botones
        btns = tk.Frame(dlg, bg=BG)
        btns.pack(fill="x", padx=16, pady=(14, 14))

        def _save():
            try: ka_min_int = max(1, min(60, int(ka_min_var.get())))
            except (ValueError, TypeError): ka_min_int = KEEPALIVE_DEFAULT_MIN
            self._save_agente_config(
                carpeta_var.get().strip(), url_var.get().strip(), mover_var.get(),
                keepalive=ka_enabled_var.get(),
                keepalive_url=ka_url_var.get().strip() or KEEPALIVE_DEFAULT_URL,
                keepalive_min=ka_min_int,
            )
            self._append("  ✔  Config del agente guardada.\n", "ok")
            # Actualiza label del status bar inmediatamente
            self._update_keepalive_label()
            dlg.destroy()

        tk.Button(btns, text="Cancelar", font=("Segoe UI", 9),
                  bg=SURFACE, fg=FG_DIM, relief="flat", cursor="hand2",
                  command=dlg.destroy).pack(side="right", padx=(6, 0))
        tk.Button(btns, text="Guardar", font=("Segoe UI", 9, "bold"),
                  bg=BRAND, fg=BG, relief="flat", cursor="hand2",
                  command=_save).pack(side="right")

        # Centrar
        dlg.update_idletasks()
        w, h = 520, 500
        sw = dlg.winfo_screenwidth()
        sh = dlg.winfo_screenheight()
        dlg.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

    def _run_agente_pendientes(self):
        """Ejecuta el agente de pendientes en background."""
        carpeta, url, mover = self._load_agente_config()
        if not carpeta:
            messagebox.showwarning(
                "Configurar agente",
                "Primero configurá la carpeta de PDFs con el botón '⚙ Configurar agente…'",
                parent=self
            )
            return

        script = os.path.join(self.dir_var.get(), "docker_panel", "agente_pendientes.py")
        if not os.path.isfile(script):
            self._append(f"  ✖  No se encontró {script}\n", "err")
            return

        cmd = f'python "{script}" --carpeta "{carpeta}" --url "{url}"'
        if mover:
            cmd += ' --mover'

        self._queue.put(("cmd", cmd))
        self._update_queue_badge()
        self._append(f"  ↳ agente encolado: {carpeta} → {url}\n", "dim")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _make_btn(self, parent, label, cmd, color):
        btn = tk.Button(
            parent, text=label,
            font=("Segoe UI", 9),
            bg=SURFACE, fg=FG,
            activebackground=BORDER, activeforeground=FG,
            relief="flat", cursor="hand2",
            anchor="w", padx=10, pady=7,
            command=lambda c=cmd: self._run(c)
        )
        btn.pack(fill="x", pady=2)
        btn.bind("<Enter>", lambda e, b=btn, col=color: b.config(bg=col, fg=BG))
        btn.bind("<Leave>", lambda e, b=btn: b.config(bg=SURFACE, fg=FG))

    def _pick_dir(self):
        d = filedialog.askdirectory(initialdir=self.dir_var.get())
        if not d:
            return
        if not os.path.exists(os.path.join(d, "docker-compose.yml")):
            messagebox.showwarning(
                "Advertencia",
                "No se encontró docker-compose.yml en ese directorio.\n"
                "Podés seguir, pero los comandos pueden fallar.",
                parent=self,
            )
        self.dir_var.set(d)
        self._refresh_status()

    def _clear_log(self):
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")

    def _append(self, text, tag=""):
        self.log.config(state="normal")
        self.log.insert("end", text, tag)
        self.log.see("end")
        self.log.config(state="disabled")

    def _set_status(self, text, color):
        self.status_lbl.config(text=text, fg=color)

    # === BEGIN KEEPALIVE RENDER (copy to unified panel) ===
    def _keepalive_loop(self):
        """Hilo que lee la config y, si keepalive=true, pingea ka_url cada N min."""
        while not self._keepalive_stop.is_set():
            try:
                _, _, _, enabled, ka_url, ka_min = self._load_agente_config()
            except Exception:
                enabled, ka_url, ka_min = False, KEEPALIVE_DEFAULT_URL, KEEPALIVE_DEFAULT_MIN
            if enabled and ka_url:
                try:
                    req = urllib.request.Request(ka_url, headers={"User-Agent": "DockerPanel-KeepAlive"})
                    with urllib.request.urlopen(req, timeout=15) as r:
                        code = r.getcode()
                    ts = datetime.datetime.now().strftime("%H:%M")
                    self.after(0, self._append, f"  ⏱  {ts} keep-alive → {code} ({ka_url})\n", "dim")
                except (urllib.error.URLError, OSError) as e:
                    ts = datetime.datetime.now().strftime("%H:%M")
                    self.after(0, self._append, f"  ⚠  {ts} keep-alive falló: {e}\n", "err")
                self.after(0, self._update_keepalive_label)
            # Sleep en chunks para poder salir rápido al cerrar
            total = max(1, min(60, int(ka_min))) * 60
            for _ in range(total):
                if self._keepalive_stop.is_set():
                    return
                time.sleep(1)

    def _update_keepalive_label(self):
        """Refresca el indicador visual del keep-alive en la status bar."""
        if not hasattr(self, "_keepalive_lbl"):
            return
        try:
            _, _, _, enabled, _, ka_min = self._load_agente_config()
        except Exception:
            enabled, ka_min = False, KEEPALIVE_DEFAULT_MIN
        if enabled:
            self._keepalive_lbl.config(text=f"● keep-alive {ka_min}m", fg=GREEN)
        else:
            self._keepalive_lbl.config(text="○ keep-alive off", fg=FG_DIM)
    # === END KEEPALIVE RENDER ===

    # === BEGIN HELPER HTTP (copy to unified panel) ===
    def _set_helper_status(self, ok, err=None):
        """Actualiza el indicador del server HTTP local."""
        if not hasattr(self, "_helper_lbl"):
            return
        if ok:
            self._helper_lbl.config(text=f"● helper :{HELPER_PORT}", fg=GREEN)
        else:
            self._helper_lbl.config(text=f"● helper :{HELPER_PORT} (error)", fg=RED)
            if err:
                self._append(f"\n⚠  Helper HTTP no arrancó: {err}\n", "err")
    # === END HELPER HTTP ===

    def _run(self, cmd):
        """Encola el comando; el worker lo ejecuta cuando le toca."""
        n = self._queue.qsize()
        if n > 0:
            self.after(0, self._append, f"  ↳ encolado ({n} en cola): {cmd}\n", "dim")
        self._queue.put(("cmd", cmd))
        self._update_queue_badge()

    def _queue_worker(self):
        """Hilo permanente: consume la cola y ejecuta un comando a la vez."""
        while True:
            item = self._queue.get()          # bloquea hasta que haya algo
            kind, payload = item
            if kind == "cmd":
                self._exec(payload)
            elif kind == "backup":
                self._run_backup(payload)
            elif kind == "restore":
                self._run_restore(payload)
            self._queue.task_done()
            self.after(0, self._update_queue_badge)

    def _update_queue_badge(self):
        n = self._queue.qsize()
        if n == 0:
            self.queue_badge.config(text="Cola: vacía", fg=FG_DIM)
        else:
            self.queue_badge.config(text=f"Cola: {n} pendiente{'s' if n > 1 else ''}", fg=YELLOW)

    def _exec(self, cmd):
        self._running = True
        cwd = self.dir_var.get()
        self.after(0, self._append, f"\n$ {cmd}\n", "cmd")
        self.after(0, self._append, f"  dir: {cwd}\n", "dim")
        self.after(0, self._set_status, "● Ejecutando…", YELLOW)

        try:
            self._proc = subprocess.Popen(
                cmd, shell=True, cwd=cwd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace"
            )
            for line in self._proc.stdout:
                tag = "err" if any(w in line.lower() for w in ("error", "failed", "fatal")) else ""
                self.after(0, self._append, line, tag)
            self._proc.wait()
            rc = self._proc.returncode
            if rc == 0:
                self.after(0, self._append, "\n✔  Completado (exit 0)\n", "ok")
                self.after(0, self._set_status, "● Listo", GREEN)
                self.after(500, self._refresh_status)
            else:
                self.after(0, self._append, f"\n✖  Terminó con código {rc}\n", "err")
                self.after(0, self._set_status, f"● Error ({rc})", RED)
        except Exception as e:
            self.after(0, self._append, f"\n✖  {e}\n", "err")
            self.after(0, self._set_status, "● Error", RED)
        finally:
            self._running = False
            self._proc = None

    def _stop_proc(self):
        """Interrumpe el proceso activo. Los comandos encolados siguen pendientes."""
        if self._proc:
            self._proc.terminate()
            self._append("\n⛔  Proceso interrumpido. Los comandos encolados seguirán ejecutándose.\n", "err")
        else:
            self._append("  No hay proceso activo.\n", "dim")

    def _clear_queue(self):
        """Vacía la cola de comandos pendientes."""
        cleared = 0
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
                cleared += 1
            except queue.Empty:
                break
        if cleared:
            self._append(f"  Cola vaciada ({cleared} comando{'s' if cleared > 1 else ''} cancelado{'s' if cleared > 1 else ''}).\n", "dim")
        self._update_queue_badge()


# === BEGIN HELPER HTTP (copy to unified panel) ===
class _HelperHandler(http.server.BaseHTTPRequestHandler):
    """Endpoints locales: /ping, /folder-files?path=…, /read-pdf?path=…"""

    def _cors(self):
        origin = self.headers.get("Origin", "")
        if origin in HELPER_ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/ping":
            return self._json(200, {"ok": True, "version": "unified", "port": HELPER_PORT})

        if parsed.path == "/folder-files":
            ruta = qs.get("path", [""])[0]
            if not ruta or not os.path.isdir(ruta):
                return self._json(400, {"error": "path inválido o inaccesible"})
            try:
                files = []
                for name in os.listdir(ruta):
                    if not name.lower().endswith(".pdf"):
                        continue
                    full = os.path.join(ruta, name)
                    try:
                        st = os.stat(full)
                        files.append({
                            "name": name,
                            "size": st.st_size,
                            "mtime": int(st.st_mtime),
                        })
                    except OSError:
                        pass
                files.sort(key=lambda f: f["mtime"], reverse=True)
                return self._json(200, {"files": files, "ruta": ruta})
            except OSError as e:
                return self._json(500, {"error": str(e)})

        if parsed.path == "/read-pdf":
            full = qs.get("path", [""])[0]
            if not full or not os.path.isfile(full) or not full.lower().endswith(".pdf"):
                return self._json(400, {"error": "path inválido"})
            try:
                with open(full, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Length", str(len(body)))
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{os.path.basename(full)}"',
                )
                self.end_headers()
                self.wfile.write(body)
                return
            except OSError as e:
                return self._json(500, {"error": str(e)})

        return self._json(404, {"error": "not found"})

    # Silencia el log ruidoso por default a stderr
    def log_message(self, fmt, *args):
        pass


def _start_helper_server(panel):
    """Arranca el HTTP server en un thread daemon y notifica al GUI."""
    try:
        srv = http.server.HTTPServer(("127.0.0.1", HELPER_PORT), _HelperHandler)
    except OSError as e:
        panel.after(0, panel._set_helper_status, False, str(e))
        return
    panel._helper_server = srv
    panel.after(0, panel._set_helper_status, True, None)
    srv.serve_forever()
# === END HELPER HTTP ===


if __name__ == "__main__":
    app = DockerPanel()
    app.mainloop()
