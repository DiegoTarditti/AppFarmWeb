# ──────────────────────────────────────────────────────────────────────────────
# Panel de control Docker + scanner de facturas.
#
# Incluye la opción de buscar PDFs en \facturas\pendientes para detectar
# documentos nuevos y procesarlos desde esta interfaz.
# ──────────────────────────────────────────────────────────────────────────────

import datetime

# === BEGIN HELPER HTTP (copy to unified panel) ===
# Mini servidor HTTP local para que el frontend hosteado (Render) pueda
# listar / leer PDFs desde la máquina de la farmacia.
import http.server
import json
import os
import queue
import socket
import subprocess
import threading
import time
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
from tkinter import filedialog, messagebox, scrolledtext

HELPER_PORT = 5055
HELPER_ALLOWED_ORIGINS = {
    "https://farmacia-web-rj1z.onrender.com",
    "http://localhost:5000",
    "http://127.0.0.1:5000",
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


# ── Persistencia del último proyecto abierto ──────────────────────────────────

def _last_project_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_project.txt")

def _load_last_project():
    p = _last_project_path()
    if not os.path.isfile(p):
        return ""
    try:
        with open(p, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""

def _save_last_project(path):
    try:
        with open(_last_project_path(), "w", encoding="utf-8") as f:
            f.write(path.strip())
    except Exception:
        pass


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

        _last = _load_last_project()
        _default = _last if _last and os.path.isdir(_last) else (
            r"C:\AppFarmWeb" if os.path.isdir(r"C:\AppFarmWeb") else ""
        )
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
        _save_last_project(d)
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
        self._revisar_carpeta()

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

        # === BEGIN AUTO-SYNC (cron ObServer → Render) ===
        self._auto_sync_stop = threading.Event()
        self._auto_sync_lock = threading.Lock()
        self._auto_sync_last_run = None
        self._auto_sync_last_ok = None
        self._auto_sync_last_error = None
        self._auto_sync_fallos = 0
        self._sync_overlay = None
        threading.Thread(target=self._auto_sync_loop, daemon=True).start()
        self.after(500, self._update_autosync_label)
        # === END AUTO-SYNC ===

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

        # ── Sync desde Render ──
        tk.Label(left, text="DATA DE RENDER", font=("Segoe UI", 8, "bold"),
                 bg=BG, fg=FG_DIM).pack(anchor="w", pady=(12, 4))

        self.btn_pull = tk.Button(
            left, text="🔄  Traer DB de Render",
            font=("Segoe UI", 9, "bold"),
            bg="#1a3a2a", fg="#7fff9f",
            activebackground="#2a4a3a", activeforeground="#7fff9f",
            relief="flat", cursor="hand2", pady=7, anchor="w", padx=10,
            command=self._run_pull_render
        )
        self.btn_pull.pack(fill="x", pady=2)
        self.btn_pull.bind("<Enter>", lambda e: (self.btn_pull.config(bg="#2a5a3a") if str(self.btn_pull['state']) == 'normal' else None))
        self.btn_pull.bind("<Leave>", lambda e: (self.btn_pull.config(bg="#1a3a2a") if str(self.btn_pull['state']) == 'normal' else None))

        # === BEGIN AUTO-SYNC (cron ObServer → Render) ===
        tk.Label(left, text="SYNC AUTOMÁTICO", font=("Segoe UI", 8, "bold"),
                 bg=BG, fg=FG_DIM).pack(anchor="w", pady=(12, 4))

        btn_sync_now = tk.Button(
            left, text="🔄  Sincronizar ahora",
            font=("Segoe UI", 9, "bold"),
            bg="#2a1a3a", fg="#c9a3ff",
            activebackground="#3a2a4a", activeforeground="#c9a3ff",
            relief="flat", cursor="hand2", pady=7, anchor="w", padx=10,
            command=lambda: threading.Thread(
                target=self._ejecutar_auto_sync, daemon=True
            ).start()
        )
        btn_sync_now.pack(fill="x", pady=2)
        btn_sync_now.bind("<Enter>", lambda e: btn_sync_now.config(bg="#4a3a5a"))
        btn_sync_now.bind("<Leave>", lambda e: btn_sync_now.config(bg="#2a1a3a"))

        btn_sync_cfg = tk.Button(
            left, text="⚙  Configurar auto-sync…",
            font=("Segoe UI", 9),
            bg=SURFACE, fg=FG,
            activebackground=BORDER, activeforeground=FG,
            relief="flat", cursor="hand2", pady=7, anchor="w", padx=10,
            command=self._config_autosync
        )
        btn_sync_cfg.pack(fill="x", pady=2)
        btn_sync_cfg.bind("<Enter>", lambda e: btn_sync_cfg.config(bg=BORDER))
        btn_sync_cfg.bind("<Leave>", lambda e: btn_sync_cfg.config(bg=SURFACE))
        # === END AUTO-SYNC ===

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

        # Browser: puerto HTTP del contenedor web (click abre en navegador)
        tk.Label(self._status_bar, text="│", bg="#111113", fg=BORDER).pack(side="right", padx=8)
        self._browser_lbl = tk.Label(
            self._status_bar,
            text="○ browser —",
            font=("Segoe UI", 8, "bold"),
            bg="#111113", fg=FG_DIM, cursor="hand2",
        )
        self._browser_lbl.pack(side="right", padx=4)
        self._browser_lbl.bind("<Button-1>", lambda e: self._open_browser())
        self._browser_port = None

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

        # === BEGIN AUTO-SYNC (cron ObServer → Render) ===
        tk.Label(self._status_bar, text="│", bg="#111113", fg=BORDER).pack(side="right", padx=8)
        self._autosync_lbl = tk.Label(
            self._status_bar,
            text="○ auto-sync off",
            font=("Segoe UI", 8),
            bg="#111113", fg=FG_DIM,
            cursor="hand2",
        )
        self._autosync_lbl.pack(side="right", padx=4)
        self._autosync_lbl.bind("<Button-1>", lambda e: self._config_autosync())
        # === END AUTO-SYNC ===

        # Indicador de PDFs pendientes en carpeta local + botón Revisar
        tk.Label(self._status_bar, text="│", bg="#111113", fg=BORDER).pack(side="right", padx=8)
        self._carpeta_lbl = tk.Label(
            self._status_bar,
            text="📁 carpeta: sin configurar",
            font=("Segoe UI", 8),
            bg="#111113", fg=FG_DIM,
            cursor="hand2",
        )
        self._carpeta_lbl.pack(side="right", padx=4)
        self._carpeta_lbl.bind("<Button-1>", lambda _e: self._revisar_carpeta())
        self._carpeta_btn = tk.Label(
            self._status_bar, text="↻ Revisar",
            font=("Segoe UI", 8, "bold"),
            bg="#111113", fg=YELLOW, cursor="hand2",
        )
        self._carpeta_btn.pack(side="right", padx=4)
        self._carpeta_btn.bind("<Button-1>", lambda _e: self._revisar_carpeta())

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

        # Puerto del browser (si web está running)
        browser_port = None
        if 'web' in running_services:
            try:
                r_port = subprocess.run(
                    "docker-compose port web 5000",
                    shell=True, cwd=cwd,
                    capture_output=True, text=True, timeout=10
                )
                # Output: "0.0.0.0:5001" o similar
                out = r_port.stdout.strip()
                if ':' in out:
                    browser_port = out.rsplit(':', 1)[-1].strip()
            except Exception:
                pass

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

            # Browser: verde con puerto si web arriba, gris si no
            self._browser_port = browser_port
            if browser_port:
                self._browser_lbl.config(text=f"● browser :{browser_port}", fg=GREEN)
            else:
                self._browser_lbl.config(text="○ browser —", fg=FG_DIM)

            now = datetime.datetime.now().strftime("%H:%M:%S")
            self._status_time_lbl.config(text=f"actualizado {now}")

        self.after(0, _update)

    def _open_browser(self):
        """Abre http://localhost:<puerto> en el navegador default."""
        if not self._browser_port:
            return
        import webbrowser
        webbrowser.open(f"http://localhost:{self._browser_port}")

    def _revisar_carpeta(self):
        """Escanea la carpeta configurada y muestra cuántos PDFs hay pendientes."""
        carpeta = self._load_agente_config()[0]
        lbl = self._carpeta_lbl
        if not carpeta:
            lbl.config(text="📁 carpeta: sin configurar", fg=FG_DIM)
            return
        if not os.path.isdir(carpeta):
            lbl.config(text="📁 carpeta: no existe", fg=RED)
            return
        try:
            pdfs = [f for f in os.listdir(carpeta)
                    if f.lower().endswith('.pdf') and os.path.isfile(os.path.join(carpeta, f))]
        except Exception as e:
            lbl.config(text=f"📁 error: {e}", fg=RED)
            return
        n = len(pdfs)
        if n == 0:
            lbl.config(text="📁 carpeta: 0 PDFs", fg=FG_DIM)
        else:
            lbl.config(text=f"📁 {n} PDF{'s' if n != 1 else ''} pendiente{'s' if n != 1 else ''}",
                       fg=GREEN)
        self._append(f"  ↻ Revisado: {n} PDF(s) en {carpeta}\n", "dim")

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
        # Config vive en la misma carpeta que este script (evita problemas de case en Windows)
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "agente_config.txt")

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
        # Preservar config de auto-sync que puede haberse guardado por separado
        auto_sync_cfg = self._load_auto_sync_config()
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
            # === BEGIN AUTO-SYNC ===
            f.write(f"autosync_enabled={'true' if auto_sync_cfg['enabled'] else 'false'}\n")
            f.write(f"autosync_horas={auto_sync_cfg['horas']}\n")
            f.write(f"autosync_arranque_min={auto_sync_cfg['arranque_min']}\n")
            f.write(f"autosync_url={auto_sync_cfg['url']}\n")
            f.write(f"autosync_token={auto_sync_cfg['token']}\n")
            if auto_sync_cfg.get('last_run'):
                f.write(f"autosync_last_run={auto_sync_cfg['last_run']}\n")
            # === END AUTO-SYNC ===

    # === BEGIN AUTO-SYNC ===
    def _load_auto_sync_config(self):
        """Devuelve dict con la config del cron de sync automático."""
        cfg_path = self._get_agente_config_path()
        cfg = {
            'enabled': False,
            'horas': '06,09,12,15,18,00',          # horarios fijos (HH), separados por coma
            'arranque_min': 180,                    # al abrir el panel, sync si pasaron >N min
            'url': 'http://localhost:5000',         # base URL de la app local
            'token': '',                            # X-Auto-Sync-Token opcional
            'last_run': None,                       # ISO datetime del último sync exitoso
        }
        if os.path.isfile(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("autosync_enabled="):
                        cfg['enabled'] = line.split("=", 1)[1].lower() in ("true", "1", "si")
                    elif line.startswith("autosync_horas="):
                        cfg['horas'] = line.split("=", 1)[1]
                    elif line.startswith("autosync_arranque_min="):
                        try: cfg['arranque_min'] = max(15, int(line.split("=", 1)[1]))
                        except ValueError: pass
                    elif line.startswith("autosync_url="):
                        cfg['url'] = line.split("=", 1)[1]
                    elif line.startswith("autosync_token="):
                        cfg['token'] = line.split("=", 1)[1]
                    elif line.startswith("autosync_last_run="):
                        cfg['last_run'] = line.split("=", 1)[1]
        return cfg

    def _save_auto_sync_config(self, **changes):
        """Actualiza solo los campos provistos del bloque auto-sync."""
        current = self._load_auto_sync_config()
        current.update(changes)
        # Reusar _save_agente_config para reescribir el archivo entero.
        # Necesitamos los otros bloques tal cual están ahora.
        carpeta, url, mover, ka, ka_url, ka_min = self._load_agente_config()
        # Guardar de forma manual para incluir los cambios del auto-sync
        cfg_path = self._get_agente_config_path()
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write(f"carpeta={carpeta}\n")
            f.write(f"url={url}\n")
            f.write(f"mover={'true' if mover else 'false'}\n")
            f.write(f"keepalive={'true' if ka else 'false'}\n")
            f.write(f"keepalive_url={ka_url}\n")
            f.write(f"keepalive_min={ka_min}\n")
            f.write(f"autosync_enabled={'true' if current['enabled'] else 'false'}\n")
            f.write(f"autosync_horas={current['horas']}\n")
            f.write(f"autosync_arranque_min={current['arranque_min']}\n")
            f.write(f"autosync_url={current['url']}\n")
            f.write(f"autosync_token={current['token']}\n")
            if current.get('last_run'):
                f.write(f"autosync_last_run={current['last_run']}\n")
    # === END AUTO-SYNC ===

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
        carpeta, url, mover, *_ = self._load_agente_config()
        if not carpeta:
            messagebox.showwarning(
                "Configurar agente",
                "Primero configurá la carpeta de PDFs con el botón '⚙ Configurar agente…'",
                parent=self
            )
            return

        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agente_pendientes.py")
        if not os.path.isfile(script):
            self._append(f"  ✖  No se encontró {script}\n", "err")
            return

        cmd = f'python "{script}" --carpeta "{carpeta}" --url "{url}"'
        if mover:
            cmd += ' --mover'

        self._queue.put(("cmd", cmd))
        self._update_queue_badge()
        self._append(f"  ↳ agente encolado: {carpeta} → {url}\n", "dim")

    def _run_pull_render(self):
        """Corre scripts/pull_from_render.py del proyecto actual.
        Reemplaza toda la DB local por un snapshot fresco de Render."""
        proyecto = self.dir_var.get()
        if not proyecto or not os.path.isdir(proyecto):
            self._append("  ✖  Seleccioná primero el directorio del proyecto.\n", "err")
            return

        script = os.path.join(proyecto, "scripts", "pull_from_render.py")
        if not os.path.isfile(script):
            self._append(f"  ✖  No se encontró {script}\n", "err")
            return

        if not messagebox.askyesno(
            "Traer DB de Render",
            "Se va a REEMPLAZAR toda la data local por el snapshot de Render.\n"
            "Perdés cualquier prueba local que hayas hecho.\n\n"
            "Esto tarda ~1 minuto. Después se reinicia el contenedor web.\n\n"
            "¿Continuar?",
            parent=self
        ):
            return

        # Encadenamos el pull + restart web en una sola shell (cwd = proyecto)
        cmd = f'python "{script}" && docker-compose restart web'
        self._set_pull_running(True)
        self._queue.put(("cmd", cmd))
        self._queue.put(("cb", lambda: self._set_pull_running(False)))
        self._update_queue_badge()
        self._append("  ↳ pull de Render encolado (dump + restore + restart)\n", "dim")

    def _set_pull_running(self, running):
        """Cambia el botón 'Traer DB de Render' a estado procesando/normal."""
        if running:
            self.btn_pull.config(text="⏳  Procesando… (~1 min)",
                                 state="disabled", bg="#3a3a1a", fg="#ffef8f")
        else:
            self.btn_pull.config(text="🔄  Traer DB de Render",
                                 state="normal", bg="#1a3a2a", fg="#7fff9f")

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

    # === BEGIN AUTO-SYNC (cron ObServer → Render) ===
    def _auto_sync_debe_correr_ahora(self, cfg, ahora=None):
        """Determina si corresponde correr un sync ahora.

        Retorna (bool, motivo). Razones posibles:
          - 'horario': la hora actual coincide con alguna de cfg['horas'] y
                        no corrimos en esa hora todavía.
          - 'arranque': nunca corrimos o último sync hace más de arranque_min.
        """
        import datetime as _dt
        ahora = ahora or _dt.datetime.now()
        last_run_str = cfg.get('last_run')
        last_run = None
        if last_run_str:
            try:
                last_run = _dt.datetime.fromisoformat(last_run_str)
            except ValueError:
                last_run = None

        # Al arranque: si nunca corrió o pasaron muchos minutos
        arr_min = int(cfg.get('arranque_min', 180))
        if not last_run:
            # Nunca hubo sync — arranque forzoso
            return True, 'primer sync'
        delta_min = (ahora - last_run).total_seconds() / 60
        if delta_min >= arr_min:
            return True, f'último sync hace {int(delta_min)} min'

        # Por horario: si la hora actual coincide con alguna configurada
        # y no corrimos ya en esa hora del día.
        horas = [int(h.strip()) for h in cfg.get('horas', '').split(',')
                 if h.strip().isdigit()]
        hora_actual = ahora.hour
        if hora_actual in horas:
            # Ya corrimos esta misma hora del día?
            if last_run.date() == ahora.date() and last_run.hour == hora_actual:
                return False, 'ya corrido esta hora'
            # Si la última corrida fue ayer o anteayer en la misma hora, igual corremos
            return True, f'horario {hora_actual:02d}:00'
        return False, f'esperando próximo horario (ahora {hora_actual:02d}:xx)'

    def _auto_sync_loop(self):
        """Thread que cada minuto chequea si corresponde correr el sync."""
        import datetime as _dt
        # Pequeña espera inicial para que el panel termine de arrancar
        time.sleep(15)
        while not self._auto_sync_stop.is_set():
            try:
                cfg = self._load_auto_sync_config()
                if cfg['enabled']:
                    debe, motivo = self._auto_sync_debe_correr_ahora(cfg)
                    if debe:
                        self.after(0, self._append,
                                   f"  🔄 auto-sync disparado ({motivo})\n", "dim")
                        self._ejecutar_auto_sync(cfg, automatico=True)
            except Exception as e:
                self.after(0, self._append, f"  ⚠ auto-sync loop error: {e}\n", "err")
            self.after(0, self._update_autosync_label)
            # Sleep 60s en chunks de 1s para salir rápido
            for _ in range(60):
                if self._auto_sync_stop.is_set():
                    return
                time.sleep(1)

    def _mostrar_overlay_sync(self, titulo='Sincronizando…', sub='Esto puede tardar unos minutos.'):
        """Muestra un overlay modal centrado mientras corre el sync.
        Se debe llamar con self.after(0, ...) porque tkinter no es thread-safe."""
        try:
            if getattr(self, '_sync_overlay', None) is not None and self._sync_overlay.winfo_exists():
                return
        except Exception:
            pass
        ov = tk.Toplevel(self)
        ov.title('')
        ov.configure(bg=BG)
        ov.transient(self)
        ov.resizable(False, False)
        ov.protocol('WM_DELETE_WINDOW', lambda: None)  # no se cierra con X
        ov.overrideredirect(True)
        # Centrar sobre el panel
        self.update_idletasks()
        w, h = 420, 130
        px = self.winfo_rootx() + (self.winfo_width() - w) // 2
        py = self.winfo_rooty() + (self.winfo_height() - h) // 2
        ov.geometry(f'{w}x{h}+{px}+{py}')

        frame = tk.Frame(ov, bg=BG, padx=24, pady=20,
                         highlightbackground=BORDER, highlightthickness=2)
        frame.pack(fill='both', expand=True)
        self._sync_overlay_title = tk.Label(frame, text=titulo,
                                             font=('Segoe UI', 11, 'bold'),
                                             bg=BG, fg='#c9a3ff')
        self._sync_overlay_title.pack(anchor='w')
        self._sync_overlay_sub = tk.Label(frame, text=sub,
                                           font=('Segoe UI', 9),
                                           bg=BG, fg=FG_DIM, wraplength=360,
                                           justify='left')
        self._sync_overlay_sub.pack(anchor='w', pady=(4, 0))
        self._sync_overlay_dots = tk.Label(frame, text='●○○',
                                            font=('Segoe UI', 14, 'bold'),
                                            bg=BG, fg='#c9a3ff')
        self._sync_overlay_dots.pack(anchor='w', pady=(10, 0))
        self._sync_overlay = ov
        # Animación de puntitos
        self._sync_overlay_step = 0
        self._animar_overlay()

    def _animar_overlay(self):
        try:
            if self._sync_overlay is None or not self._sync_overlay.winfo_exists():
                return
            frames = ['●○○', '○●○', '○○●', '○●○']
            self._sync_overlay_dots.config(text=frames[self._sync_overlay_step % len(frames)])
            self._sync_overlay_step += 1
            self.after(300, self._animar_overlay)
        except Exception:
            pass

    def _actualizar_overlay(self, titulo=None, sub=None):
        """Actualiza texto mientras el overlay está visible."""
        try:
            if self._sync_overlay is None or not self._sync_overlay.winfo_exists():
                return
            if titulo is not None:
                self._sync_overlay_title.config(text=titulo)
            if sub is not None:
                self._sync_overlay_sub.config(text=sub)
        except Exception:
            pass

    def _cerrar_overlay_sync(self):
        try:
            if self._sync_overlay is not None and self._sync_overlay.winfo_exists():
                self._sync_overlay.destroy()
        except Exception:
            pass
        self._sync_overlay = None

    def _ejecutar_auto_sync(self, cfg=None, automatico=False):
        """Ejecuta un sync ahora llamando al endpoint /api/auto-sync de la app local.
        Bloquea con lock para que 2 invocaciones no se pisen."""
        import datetime as _dt
        if not self._auto_sync_lock.acquire(blocking=False):
            self.after(0, self._append,
                       "  ⏳ auto-sync: ya hay uno en curso, skip\n", "dim")
            return
        # Mostrar overlay + thread de polling para actualizar el estado en vivo
        poll_stop = threading.Event()
        if not automatico:
            self.after(0, self._mostrar_overlay_sync,
                       'Sincronizando ObServer → Render',
                       'Iniciando…')

            def _polling():
                labels = {
                    'laboratorios':     ('1/9', 'Trayendo laboratorios…'),
                    'rubros':           ('2/9', 'Trayendo rubros…'),
                    'subrubros':        ('3/9', 'Trayendo subrubros…'),
                    'nombres_drogas':   ('4/9', 'Trayendo nombres de drogas…'),
                    'productos':        ('5/9', 'Trayendo productos (122k) — ~50s…'),
                    'stock':            ('6/9', 'Trayendo stock de productos — ~15s…'),
                    'ventas_mensuales': ('7/9', 'Trayendo ventas mensuales — ~25s…'),
                    'match_productos':  ('8/9', 'Auto-match EAN ↔ IdProducto…'),
                    'push_render':      ('9/9', 'Replicando a Render (COPY) — ~90s…'),
                }
                status_url = (cfg or self._load_auto_sync_config()).get('url', '').rstrip('/') + '/api/auto-sync/status'
                while not poll_stop.is_set():
                    try:
                        with urllib.request.urlopen(status_url, timeout=3) as r:
                            st = json.loads(r.read().decode('utf-8', errors='replace'))
                        paso = st.get('paso_actual')
                        if paso and paso in labels:
                            idx, texto = labels[paso]
                            self.after(0, self._actualizar_overlay,
                                       f'Sincronizando · paso {idx}',
                                       texto)
                    except Exception:
                        pass
                    poll_stop.wait(2)
            threading.Thread(target=_polling, daemon=True).start()
        try:
            if cfg is None:
                cfg = self._load_auto_sync_config()
            url = (cfg.get('url') or '').strip().rstrip('/')
            if not url:
                self.after(0, self._append,
                           "  ⚠ auto-sync: falta config autosync_url\n", "err")
                return
            endpoint = url + '/api/auto-sync'
            token = (cfg.get('token') or '').strip()
            ts_inicio = _dt.datetime.now()
            self._auto_sync_last_run = ts_inicio
            self.after(0, self._append,
                       f"  🔄 {ts_inicio.strftime('%H:%M')} auto-sync → {endpoint}\n", "dim")
            try:
                data = b''
                headers = {'User-Agent': 'DockerPanel-AutoSync',
                           'Content-Type': 'application/json'}
                if token:
                    headers['X-Auto-Sync-Token'] = token
                req = urllib.request.Request(endpoint, data=data,
                                             headers=headers, method='POST')
                with urllib.request.urlopen(req, timeout=600) as r:
                    body = r.read().decode('utf-8', errors='replace')
                try:
                    result = json.loads(body)
                except Exception:
                    result = {'ok': r.getcode() == 200, 'raw': body[:200]}
                if result.get('ok'):
                    self._auto_sync_last_ok = _dt.datetime.now()
                    self._auto_sync_fallos = 0
                    pasos = result.get('pasos', [])
                    resumen = ' · '.join(
                        f"{p.get('paso')}:{p.get('upsert') or p.get('total_filas') or '✓'}"
                        for p in pasos if p.get('ok')
                    )
                    self.after(0, self._append,
                               f"  ✓ auto-sync OK — {resumen}\n", "ok")
                    # Persistir last_run
                    self._save_auto_sync_config(
                        last_run=self._auto_sync_last_ok.isoformat()
                    )
                else:
                    self._auto_sync_fallos += 1
                    self._auto_sync_last_error = result.get('error') or 'falló'
                    pasos_fail = [p for p in result.get('pasos', []) if not p.get('ok')]
                    detalle = '; '.join(f"{p.get('paso')}: {p.get('error')}" for p in pasos_fail)
                    self.after(0, self._append,
                               f"  ✗ auto-sync FALLÓ ({self._auto_sync_fallos}x) — {detalle or self._auto_sync_last_error}\n",
                               "err")
            except (urllib.error.URLError, OSError, socket.timeout) as e:
                self._auto_sync_fallos += 1
                self._auto_sync_last_error = str(e)
                self.after(0, self._append,
                           f"  ✗ auto-sync conexión falló ({self._auto_sync_fallos}x): {e}\n", "err")
        finally:
            poll_stop.set()
            self._auto_sync_lock.release()
            self.after(0, self._update_autosync_label)
            if not automatico:
                self.after(0, self._cerrar_overlay_sync)

    def _config_autosync(self):
        """Diálogo para configurar el auto-sync: enabled, horarios, URL, token."""
        cfg = self._load_auto_sync_config()

        dlg = tk.Toplevel(self)
        dlg.title("Configurar auto-sync ObServer → Render")
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        dlg.grab_set()

        # Enabled
        enabled_var = tk.BooleanVar(value=cfg['enabled'])
        tk.Checkbutton(dlg, text="Activar sincronización automática",
                       variable=enabled_var, font=("Segoe UI", 10, "bold"),
                       bg=BG, fg=FG, selectcolor=SURFACE,
                       activebackground=BG, activeforeground=FG).pack(
                           anchor="w", padx=16, pady=(14, 8))

        # Horarios
        tk.Label(dlg, text="Horarios diarios (HH separado por coma):",
                 font=("Segoe UI", 9, "bold"), bg=BG, fg=FG).pack(anchor="w", padx=16)
        horas_var = tk.StringVar(value=cfg['horas'])
        tk.Entry(dlg, textvariable=horas_var, font=("Consolas", 9),
                 bg=SURFACE, fg=FG, insertbackground=FG, relief="flat", bd=4
                 ).pack(fill="x", padx=16, pady=(2, 2))
        tk.Label(dlg, text="Ej: 06,09,12,15,18,00 — corre en cada una de esas horas.",
                 font=("Segoe UI", 8), bg=BG, fg=FG_DIM
                 ).pack(anchor="w", padx=16, pady=(0, 8))

        # Al arranque
        tk.Label(dlg, text="Al abrir el panel, sincronizar si pasaron más de N minutos:",
                 font=("Segoe UI", 9, "bold"), bg=BG, fg=FG).pack(anchor="w", padx=16)
        arr_var = tk.StringVar(value=str(cfg['arranque_min']))
        tk.Entry(dlg, textvariable=arr_var, font=("Consolas", 9), width=8,
                 bg=SURFACE, fg=FG, insertbackground=FG, relief="flat", bd=4
                 ).pack(anchor="w", padx=16, pady=(2, 8))

        # URL de la app
        tk.Label(dlg, text="URL base de la app (Flask):",
                 font=("Segoe UI", 9, "bold"), bg=BG, fg=FG).pack(anchor="w", padx=16)
        url_var = tk.StringVar(value=cfg['url'] or 'http://localhost:5000')
        tk.Entry(dlg, textvariable=url_var, font=("Consolas", 9),
                 bg=SURFACE, fg=FG, insertbackground=FG, relief="flat", bd=4
                 ).pack(fill="x", padx=16, pady=(2, 8))

        # Token opcional
        tk.Label(dlg, text="Token X-Auto-Sync-Token (opcional):",
                 font=("Segoe UI", 9, "bold"), bg=BG, fg=FG).pack(anchor="w", padx=16)
        token_var = tk.StringVar(value=cfg['token'])
        tk.Entry(dlg, textvariable=token_var, font=("Consolas", 9),
                 bg=SURFACE, fg=FG, insertbackground=FG, relief="flat", bd=4, show="•"
                 ).pack(fill="x", padx=16, pady=(2, 12))

        # Estado actual
        if cfg.get('last_run'):
            tk.Label(dlg, text=f"Último sync exitoso: {cfg['last_run']}",
                     font=("Segoe UI", 8), bg=BG, fg=FG_DIM
                     ).pack(anchor="w", padx=16, pady=(0, 8))

        def guardar():
            try:
                arr_min = max(15, int(arr_var.get()))
            except ValueError:
                arr_min = 180
            self._save_auto_sync_config(
                enabled=bool(enabled_var.get()),
                horas=(horas_var.get() or '').strip(),
                arranque_min=arr_min,
                url=(url_var.get() or '').strip(),
                token=(token_var.get() or '').strip(),
            )
            self._update_autosync_label()
            dlg.destroy()

        btn_row = tk.Frame(dlg, bg=BG)
        btn_row.pack(fill="x", padx=16, pady=(0, 14))
        tk.Button(btn_row, text="Cancelar", font=("Segoe UI", 9),
                  bg=SURFACE, fg=FG_DIM, activebackground=BORDER,
                  activeforeground=FG, relief="flat", cursor="hand2",
                  command=dlg.destroy).pack(side="right", padx=(6, 0))
        tk.Button(btn_row, text="Guardar", font=("Segoe UI", 9, "bold"),
                  bg=BRAND, fg="#1a1100", activebackground="#D9A91C",
                  activeforeground="#1a1100", relief="flat", cursor="hand2",
                  command=guardar).pack(side="right")

        dlg.update_idletasks()
        w, h = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        x = self.winfo_x() + (self.winfo_width() - w) // 2
        y = self.winfo_y() + (self.winfo_height() - h) // 2
        dlg.geometry(f"+{x}+{y}")

    def _update_autosync_label(self):
        """Refresca el indicador de auto-sync en la status bar."""
        if not hasattr(self, "_autosync_lbl"):
            return
        try:
            cfg = self._load_auto_sync_config()
        except Exception:
            return
        if not cfg['enabled']:
            self._autosync_lbl.config(text="○ auto-sync off", fg=FG_DIM)
            return
        if self._auto_sync_fallos >= 3:
            self._autosync_lbl.config(
                text=f"● auto-sync {self._auto_sync_fallos} fallos", fg=RED)
            return
        if self._auto_sync_last_ok:
            mins = int((datetime.datetime.now() - self._auto_sync_last_ok).total_seconds() / 60)
            self._autosync_lbl.config(
                text=f"● auto-sync · último hace {mins}m", fg=GREEN)
        else:
            self._autosync_lbl.config(text="● auto-sync · pendiente", fg="#EAB308")
    # === END AUTO-SYNC ===

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
            elif kind == "cb":
                try:
                    self.after(0, payload)
                except Exception:
                    pass
            self._queue.task_done()
            self.after(0, self._update_queue_badge)

    def _update_queue_badge(self):
        n = self._queue.qsize()
        if n == 0:
            self.queue_badge.config(text="Cola: vacía", fg=FG_DIM)
        else:
            self.queue_badge.config(text=f"Cola: {n} pendiente{'s' if n > 1 else ''}", fg=YELLOW)

    # Patrones que delatan que la app web crasheó al arrancar — Docker
    # devuelve exit 0 al "reiniciar" pero la app adentro explota después.
    _POST_CHECK_ERROR_PATTERNS = (
        'traceback',
        'syntaxerror',
        'importerror',
        'modulenotfounderror',
        'gunicorn.errors.haltserver',
        'worker failed to boot',
        'application startup failed',
        'exited with code',
    )

    def _post_check_web(self):
        """Después de un restart/up/build, lee los logs últimos del web y
        avisa si hay tracebacks. Docker reporta exit 0 incluso cuando
        gunicorn crashea, así que sin este check te enterás recién al
        abrir el browser y ver un 500.

        Mantiene self._running=True durante el chequeo para que el panel
        no acepte otros comandos mientras esperamos.
        """
        cwd = self.dir_var.get()
        try:
            r = subprocess.run(
                "docker-compose logs --tail=40 web",
                shell=True, cwd=cwd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", timeout=15,
            )
            logs = (r.stdout or '').lower()
        except Exception as e:
            self.after(0, self._append,
                       f"  ⚠  No pude verificar logs post-restart: {e}\n", "dim")
            self._running = False
            return

        hits = [p for p in self._POST_CHECK_ERROR_PATTERNS if p in logs]
        if hits:
            self.after(0, self._append,
                       "\n⚠  Post-check: la app parece haber crasheado al arrancar.\n",
                       "err")
            self.after(0, self._append,
                       f"   Detecté: {', '.join(hits)}\n", "err")
            self.after(0, self._append,
                       "   Mirá los logs completos: 'Logs Web (50 líneas)'.\n",
                       "dim")
            self.after(0, self._set_status, "● Error post-restart", RED)
        else:
            self.after(0, self._append,
                       "  ✔  Post-check: app respondiendo, sin tracebacks.\n",
                       "ok")
            self.after(0, self._set_status, "● Listo", GREEN)
        self._running = False

    def _exec(self, cmd):
        self._running = True
        cwd = self.dir_var.get()
        self.after(0, self._append, f"\n$ {cmd}\n", "cmd")
        self.after(0, self._append, f"  dir: {cwd}\n", "dim")
        self.after(0, self._set_status, "● Ejecutando…", YELLOW)

        # ¿Es un comando que arranca/reinicia el web? Si lo es, post-check.
        cmd_lower = cmd.lower()
        debe_post_check = (
            'restart web' in cmd_lower
            or 'up -d' in cmd_lower
            or 'build web' in cmd_lower
        )

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
            post_check_pendiente = False
            if rc == 0:
                self.after(0, self._append, "\n✔  Completado (exit 0)\n", "ok")
                if debe_post_check:
                    # Mantenemos el panel "ocupado" mientras esperamos al
                    # post-check. Status queda en Verificando hasta que el
                    # check decide Listo o Error.
                    self.after(0, self._append,
                               "  ⏳  Post-check: esperando 3s para verificar que la app levantó OK…\n",
                               "dim")
                    self.after(0, self._set_status, "● Verificando arranque…", YELLOW)
                    self.after(3000, self._post_check_web)
                    post_check_pendiente = True
                else:
                    self.after(0, self._set_status, "● Listo", GREEN)
                self.after(500, self._refresh_status)
            else:
                self.after(0, self._append, f"\n✖  Terminó con código {rc}\n", "err")
                self.after(0, self._set_status, f"● Error ({rc})", RED)
        except Exception as e:
            self.after(0, self._append, f"\n✖  {e}\n", "err")
            self.after(0, self._set_status, "● Error", RED)
            post_check_pendiente = False
        finally:
            self._proc = None
            # Si dejamos un post-check programado, NO liberamos _running —
            # lo va a hacer _post_check_web cuando termine.
            if not post_check_pendiente:
                self._running = False

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
