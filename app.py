import os
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
    from flask import request, abort
    if request.path.startswith('/descuentos'):
        abort(404)


@app.before_request
def exigir_login():
    from flask import request, redirect, url_for
    from flask_login import current_user
    # Rutas públicas (no requieren login)
    rutas_publicas = {'auth_login', 'static', 'health'}
    if request.endpoint in rutas_publicas or request.endpoint is None:
        return None
    if not current_user.is_authenticated:
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

from routes import register_routes
register_routes(app)


if __name__ == '__main__':
    app.run(debug=True)
