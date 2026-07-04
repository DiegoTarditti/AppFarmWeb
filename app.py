import os
import threading
import time
from urllib.error import URLError
from urllib.request import urlopen

import sentry_sdk
from flask import Flask
from flask_cors import CORS
from sentry_sdk.integrations.flask import FlaskIntegration

import database
from database import init_db, init_engine

_sentry_dsn = os.environ.get('SENTRY_DSN', '').strip()
if _sentry_dsn:
    sentry_sdk.init(
        dsn=_sentry_dsn,
        integrations=[FlaskIntegration()],
        traces_sample_rate=0.1,
        environment=os.environ.get('SENTRY_ENV', 'production'),
    )

app = Flask(__name__)
CORS(app)

# SECRET_KEY firma cookies de sesión. Sin un valor fuerte, las sesiones son
# falsificables. Antes había un fallback 'supersecretkey' — eso desactivaba
# la seguridad si alguien deployaba sin setear la env var. Ahora fallamos al
# arrancar si falta o es débil. En desarrollo local, exportá una clave random
# en el .env (ver .env.example).
_secret_key = os.environ.get('SECRET_KEY', '').strip()
if len(_secret_key) < 16:
    raise RuntimeError(
        "SECRET_KEY no configurada o demasiado corta (>=16 chars). "
        "Definila como env var. Generá una con: python -c \"import secrets; print(secrets.token_hex(32))\""
    )
app.secret_key = _secret_key
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'uploads')
app.config['TEMPLATES_AUTO_RELOAD'] = True

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///farmacia.db')

# init_db deshabilitado por default en startup. Migraciones se corren
# explícitamente fuera del path crítico de deploy (ver docs/lecciones_deploy_render.md
# punto "DB locks zombie"). Para correr migraciones:
#   - CLI: RUN_INIT_DB_ON_STARTUP=1 python -c "import app"
#   - Render: setear RUN_INIT_DB_ON_STARTUP=1 temporalmente en Environment,
#     deployar, verificar, y volver a desetear para futuros deploys.
init_engine(DATABASE_URL)
if os.environ.get('RUN_INIT_DB_ON_STARTUP') == '1':
    init_db(DATABASE_URL)


# Hosts del dominio propio de la tienda pública. Cuando alguien entra a
# https://farmbadia.com.ar/ (o /www) lo mandamos directo a /tienda (la landing).
# El resto de rutas (admin, atencion, etc.) siguen funcionando normal desde
# el mismo dominio con login. Diego 2026-06-24.
_TIENDA_HOSTS = {'farmbadia.com.ar', 'www.farmbadia.com.ar'}


@app.before_request
def redirigir_root_a_tienda():
    from flask import redirect, request
    if request.path != '/':
        return None
    host = (request.host or '').split(':')[0].lower()
    if host in _TIENDA_HOSTS:
        return redirect('/tienda', code=302)
    return None


@app.before_request
def exigir_login():
    from flask import jsonify, redirect, request, url_for
    from flask_login import current_user
    # Rutas públicas (no requieren login)
    rutas_publicas = {'auth_login', 'static', 'health', 'docs_pendientes_upload_api',
                      # Filtro droguería: herramienta standalone sin login.
                      'filtro_drogueria', 'filtro_drogueria_generar',
                      'api_auto_sync', 'api_auto_sync_status',
                      # WhatsApp Cloud API webhook (llamado por Meta, sin sesión)
                      'whatsapp_webhook_get', 'whatsapp_webhook_post',
                      'whatsapp_reenganche',
                      # WAHA webhook del grupo de reparto (red docker interna)
                      'reparto_whatsapp_grupo_webhook',
                      # Telegram webhook del grupo de cadetes (auth propia via
                      # X-Telegram-Bot-Api-Secret-Token header).
                      'reparto_telegram_cadetes_webhook',
                      # Crons externos: auth propia via X-Cron-Secret header.
                      'api_cron_recalcular_os_clientes',
                      'api_cron_notificar_alarmas',
                      # Panel remoto: auth propia via X-Panel-Token header.
                      'api_panel_proximo', 'api_panel_resultado',
                      # Sync local↔Render: auth propia via X-Panel-Token header.
                      'api_ofertas_sync_from_local', 'api_ofertas_from_server',
                      # Push master local→Render: auth propia via X-Auto-Sync-Token.
                      'push_productos_master', 'push_cadencias',
                      # Tienda pública (catálogo OTC + pedido por WhatsApp).
                      # Diego 2026-06-24. Kill switch via Config.tienda_activa.
                      'tienda_home', 'tienda_catalogo', 'tienda_producto',
                      'tienda_pedir', 'tienda_upload_file'}
    if request.endpoint in rutas_publicas or request.endpoint is None:
        return None
    if not current_user.is_authenticated:
        # Para rutas /api/* devolvemos 401 JSON en vez de redirect HTML, así
        # el JS puede manejar la sesión expirada con un mensaje claro
        # (en vez de tirar SyntaxError al parsear HTML como JSON).
        if request.path.startswith('/api/'):
            return jsonify({'ok': False, 'error': 'Sesión expirada. Recargá la página para iniciar sesión.'}), 401
        return redirect(url_for('auth_login', next=request.path))
    # Forzar cambio de password si corresponde
    if current_user.debe_cambiar_password and request.endpoint not in ('auth_cambiar_password', 'auth_logout'):
        return redirect(url_for('auth_cambiar_password'))
    return None


@app.template_filter('abs')
def abs_filter(value):
    return abs(value)


@app.template_filter('fromjson')
def fromjson_filter(value):
    """Parsea string JSON a objeto Python. Devuelve None si vacío o inválido."""
    if not value:
        return None
    try:
        import json as _json
        return _json.loads(value)
    except (ValueError, TypeError):
        return None


@app.template_filter('arg_currency')
def arg_currency(value):
    """Formatea un número como moneda argentina: 1234567.89 → 1.234.567,89"""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return '—'
    int_part, dec_part = f'{value:.2f}'.split('.')
    int_formatted = ''
    for i, ch in enumerate(reversed(int_part)):
        if i and i % 3 == 0:
            int_formatted = '.' + int_formatted
        int_formatted = ch + int_formatted
    return f'{int_formatted},{dec_part}'


from auth import init_auth

init_auth(app)

# Exponer detección de entorno en todos los templates
from helpers import detectar_entorno


@app.context_processor
def _inyectar_entorno():
    return {'entorno': detectar_entorno()}


@app.context_processor
def _inyectar_ciudades_envio():
    """Whitelist de ciudades que el cliente_picker filtra en el dropdown del
    geocoder. Configurable por env var ENVIO_CIUDADES_FILTRO (CSV, p.ej.
    'rosario,funes,roldan'). Default: las 3 de Badia."""
    raw = os.environ.get('ENVIO_CIUDADES_FILTRO', 'rosario,funes,roldan')
    ciudades = [c.strip().lower() for c in raw.split(',') if c.strip()]
    return {'envio_ciudades_filtro': ciudades}


@app.context_processor
def _inyectar_turno_actual():
    """Turno global compartido (Config.turno_actual): default sticky del dropdown
    de turno en /atención. Vacío si no se eligió ninguno todavía."""
    turno = ''
    try:
        with database.get_db() as s:
            cfg = s.get(database.Config, 1)
            turno = (cfg.turno_actual if cfg else '') or ''
    except Exception:  # noqa: BLE001 — defensivo (DB no disponible en algunos render iniciales)
        pass
    return {'turno_actual': turno}


@app.context_processor
def _inyectar_alias_transferencia():
    """Alias para transferencias (config en /config/envio). Se usa en /atencion
    cuando el operador elige forma de pago = Transferencia. None si no se cargó."""
    alias = ''
    try:
        from bot import envio as _envio
        c = _envio.get_config()
        alias = (c.get('alias_transferencia') or '') or ''
    except Exception:  # noqa: BLE001 — defensivo (DB no disponible en algunos render iniciales)
        pass
    return {'alias_transferencia': alias}

from routes import register_routes

register_routes(app)


def _keep_alive_loop():
    """Thread en background que pingea /health_web si keep_alive_enabled está on."""
    base_url = os.environ.get('KEEP_ALIVE_URL', 'http://127.0.0.1:5000')
    while True:
        try:
            with database.get_db() as session:
                cfg = session.get(database.Config, 1)
                enabled = bool(cfg and cfg.keep_alive_enabled)
                interval = int(cfg.keep_alive_interval_min) if cfg else 10
        except Exception:
            enabled, interval = False, 10
        interval = max(1, min(60, interval))
        # Solo mantener despierto en horario de atención (08:00–18:00 AR).
        # Fuera de ese rango se deja dormir a Render (ahorra horas de instancia
        # del plan free; decisión Diego 2026-06-18).
        try:
            hora = database.now_ar().hour
        except Exception:
            hora = 12
        en_horario = 8 <= hora < 18
        if enabled and en_horario:
            try:
                urlopen(f'{base_url}/health_web', timeout=10).read()
            except (URLError, OSError):
                pass
        time.sleep(interval * 60)


threading.Thread(target=_keep_alive_loop, daemon=True).start()

# Telegram (grupo de cadetes): polling worker. No-op si está apagado en env.
# Lo arrancamos acá (no en cada worker de gunicorn) para que solo UN proceso
# haga polling — sino se pelean por los updates.
try:
    from bot import telegram_grupo
    telegram_grupo.iniciar_polling_thread()
except Exception:  # noqa: BLE001
    pass

# Cron SLA del flujo de reparto (reaviso de publicación + desasignar retiros
# vencidos). Lock por socket para que solo un worker corra.
try:
    from services import reparto_sla_cron
    reparto_sla_cron.iniciar_cron_thread()
except Exception:  # noqa: BLE001
    pass


if __name__ == '__main__':
    app.run(debug=True)
