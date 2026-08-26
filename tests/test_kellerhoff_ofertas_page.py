"""La página /kellerhoff/ofertas: sube el CSV, procesa el vigente y lo muestra.

Fija que el flujo learn (subida manual) → persistencia → render anda de punta a
punta, y que las reglas del análisis (descartar mínimo 1, quedarse con la
todo-o-nada) llegan a la tabla.
"""
import database
from flask import Flask, url_for as _real_url_for
from flask_login import LoginManager, UserMixin

from database import now_ar
from services.ofertas_kellerhoff import importar_desde_texto

_CSV = (
    'Nombre;Codigo Barra;Unidades Minimas;Unidades Maximas;Unidades Multiplo;'
    'Unidades Bonificadas;Descripcion Oferta\n'
    'IBUPIRAC 600 X 20;7791234567890;20;0;0;0;40% de Dto. s/PVP\n'
    'IBUPIRAC 600 X 20;7791234567890;1;0;0;0;5% de Dto. s/PVP\n'   # mínimo 1 → se descarta
    'ALMAXIMO 100 CPR X 2;7790000000001;0;0;3;0;10% de Dto. s/PVP\n'  # múltiplo → afuera
)


def _app():
    app = Flask(__name__, template_folder='../templates')
    app.secret_key = 'test'
    app.config['TESTING'] = True

    class _U(UserMixin):
        id = '1'
        nombre_completo = 'Test'
        username = 'test'
        rol = 'dev'

    lm = LoginManager(app)

    @lm.request_loader
    def _load(_req):
        return _U()

    class _E:
        codigo = 'test'
        label = 'Test'
        color = '#888'

    app.jinja_env.globals['entorno'] = _E()
    app.jinja_env.globals['tiene_permiso'] = lambda *a, **k: True

    def _tol(endpoint, **values):
        try:
            return _real_url_for(endpoint, **values)
        except Exception:
            return '#'
    app.jinja_env.globals['url_for'] = _tol

    import routes.kellerhoff as k
    k.init_app(app)

    @app.route('/')
    def index():
        return '', 200

    return app


def test_pagina_ofertas_muestra_vigente():
    s = database.SessionLocal()
    res = importar_desde_texto(s, _CSV, descargado_en=now_ar())
    s.commit()
    # Solo la todo-o-nada (mínimo 20). La de mínimo 1 y el múltiplo quedan afuera.
    assert res['vigentes'] == 1

    c = _app().test_client()
    r = c.get('/kellerhoff/ofertas')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'IBUPIRAC 600 X 20' in html
    assert '40%' in html          # descuento al mínimo
    assert '+35' in html          # delta = 40 − 5 (la fila de mínimo 1)
    assert 'ALMAXIMO' not in html  # múltiplo no entra


def test_subida_por_http_importa():
    import io
    c = _app().test_client()
    data = {'archivo': (io.BytesIO(_CSV.encode('utf-8')), 'ProductosEnOferta.csv')}
    r = c.post('/kellerhoff/ofertas/importar', data=data,
               content_type='multipart/form-data', follow_redirects=True)
    assert r.status_code == 200
    from database import KellerhoffOferta
    s = database.SessionLocal()
    assert s.query(KellerhoffOferta).count() == 1
