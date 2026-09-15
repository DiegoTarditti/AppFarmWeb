"""Sin sesión, una API tiene que contestar JSON 401 y no el HTML del login.

El caso real: el diálogo "¿Quién emite el pedido?" mostraba
«Error al cargar (Unexpected token '<'», que es lo que pasa cuando un `fetch`
recibe la pantalla de login y trata de parsearla como JSON. Quien lo ve no tiene
forma de saber que lo único que le pasó fue que se le venció la sesión.
"""
from flask import Flask, jsonify
from flask_login import login_required

import auth


def _app():
    app = Flask(__name__)
    app.secret_key = 'test-con-al-menos-16-chars'
    app.config['TESTING'] = True
    auth.login_manager.init_app(app)

    @app.route('/auth/login')
    def auth_login():
        return '<html>login</html>'

    @app.route('/api/algo')
    @login_required
    def api_algo():
        return jsonify({'ok': True})

    @app.route('/una/pantalla')
    @login_required
    def pantalla():
        return '<html>pantalla</html>'

    return app


def test_api_sin_sesion_devuelve_json_401():
    c = _app().test_client()
    r = c.get('/api/algo')
    assert r.status_code == 401
    d = r.get_json()                      # si devolviera HTML, esto es None
    assert d['sesion_expirada'] is True
    assert 'sesión' in d['error'].lower()


def test_pantalla_sin_sesion_sigue_redirigiendo_al_login():
    """Las pantallas no cambian: ahí el redirect es lo correcto."""
    c = _app().test_client()
    r = c.get('/una/pantalla')
    assert r.status_code == 302
    assert '/auth/login' in r.headers['Location']


def test_fetch_de_pantalla_tambien_recibe_json():
    """Un fetch a una ruta que no empieza con /api igual pide JSON."""
    c = _app().test_client()
    r = c.get('/una/pantalla', headers={'X-Requested-With': 'XMLHttpRequest'})
    assert r.status_code == 401
    assert r.get_json()['sesion_expirada'] is True
