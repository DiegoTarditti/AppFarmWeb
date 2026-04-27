import os
import threading
import time
from urllib.error import URLError
from urllib.request import urlopen

from flask import Flask
from flask_cors import CORS

import database
from database import init_db

app = Flask(__name__)
CORS(app)
app.secret_key = os.environ.get('SECRET_KEY', 'supersecretkey')
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'uploads')
app.config['TEMPLATES_AUTO_RELOAD'] = True

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///farmacia.db')
init_db(DATABASE_URL)


@app.before_request
def bloquear_descuentos():
    from flask import abort, request
    if request.path.startswith('/descuentos'):
        abort(404)


@app.before_request
def exigir_login():
    from flask import jsonify, redirect, request, url_for
    from flask_login import current_user
    # Rutas públicas (no requieren login)
    rutas_publicas = {'auth_login', 'static', 'health', 'docs_pendientes_upload_api',
                      'api_auto_sync', 'api_auto_sync_status'}
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
        if enabled:
            try:
                urlopen(f'{base_url}/health_web', timeout=10).read()
            except (URLError, OSError):
                pass
        time.sleep(interval * 60)


threading.Thread(target=_keep_alive_loop, daemon=True).start()


if __name__ == '__main__':
    app.run(debug=True)
