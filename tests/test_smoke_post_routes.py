"""Smoke tests de rutas POST principales.

Filosofía: cada POST debe responder sin 500 ante body vacío o inválido.
Aceptable: 302 (redirect con flash), 400 (validación falló), 404 (record no
encontrado), 401/403 (auth/permiso). Lo que NO aceptamos: 500.

Esto NO testea la lógica funcional — solo verifica que el endpoint valide
inputs antes de tirar excepciones.

Excluye rutas destructivas a propósito (admin/reset-datos, admin/cleanup,
etc.). Esas las testeamos con scenarios concretos cuando hagan falta.
"""
import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin


@pytest.fixture(scope='module')
def smoke_app(init_test_db, tmp_path_factory):
    upload_dir = str(tmp_path_factory.mktemp('uploads_smoke_post'))

    app = Flask(__name__, template_folder='../templates')
    app.secret_key = 'smoke-post-test'
    app.config['TESTING'] = True
    app.config['UPLOAD_FOLDER'] = upload_dir
    app.config['SERVER_NAME'] = 'localhost.localdomain'

    class _Entorno:
        codigo = 'test'
        label = 'TEST'
        color = '#888'
        descripcion = 'Test environment'
    app.jinja_env.globals['entorno'] = _Entorno()
    app.jinja_env.globals['tiene_permiso'] = lambda *a, **k: True

    from flask import url_for as _real_url_for

    def _tolerant_url_for(endpoint, **values):
        try:
            return _real_url_for(endpoint, **values)
        except Exception:
            return '#'
    app.jinja_env.globals['url_for'] = _tolerant_url_for

    lm = LoginManager(app)

    class _DummyUser(UserMixin):
        id = '1'
        username = 'smoke'
        rol = 'admin'
        nombre_completo = 'Smoke'
        is_authenticated = True

    @lm.user_loader
    def _loader(uid):
        return _DummyUser()

    @lm.request_loader
    def _req_loader(_req):
        return _DummyUser()

    @app.context_processor
    def _inject_entorno():
        return {'entorno': _Entorno()}

    @app.template_filter('abs')
    def _abs_f(v):
        return abs(v) if v is not None else 0

    @app.template_filter('arg_currency')
    def _arg_currency(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return '—'
        int_p, dec_p = f'{v:.2f}'.split('.')
        out = ''
        for i, ch in enumerate(reversed(int_p)):
            if i and i % 3 == 0:
                out = '.' + out
            out = ch + out
        return f'{out},{dec_p}'

    from routes import register_routes
    register_routes(app)
    return app


@pytest.fixture
def smoke_client(smoke_app):
    with smoke_app.test_client() as c:
        yield c


# ── Rutas POST a smoke-testear con body vacío ─────────────────────────────────
# Tupla: (path, descripcion). Body siempre {} (form vacío).
# EXCLUIDAS a propósito: rutas destructivas (admin reset, cleanup, backup).

RUTAS_POST = [
    # Core / settings
    ('/settings', 'settings_save'),

    # Facturas
    ('/upload', 'upload_invoice'),
    ('/invoice/1/header', 'invoice_header'),
    ('/invoice/1/auto-table', 'invoice_auto_table'),
    ('/invoice/1/apply-mapping', 'invoice_apply_mapping'),
    ('/invoice/1/pick-fields', 'invoice_pick_fields_save'),
    ('/invoice/1/pick-items/infer', 'invoice_pick_items_infer'),
    ('/invoice/1/pick-items/save', 'invoice_pick_items_save'),

    # Reclamos
    ('/claim/create', 'claim_create'),
    ('/claim/1/complete', 'claim_complete'),

    # Laboratorios
    ('/laboratorio/create', 'lab_create'),
    ('/laboratorio/1/edit', 'lab_edit'),
    ('/laboratorio/1/delete', 'lab_delete'),
    ('/laboratorios/sync-observer', 'lab_sync_observer'),

    # Cuentas corrientes
    ('/provider/1/cuenta-corriente/add', 'cc_add'),
    ('/provider/1/cuenta-corriente/conciliar', 'cc_conciliar'),

    # Clientes
    ('/clientes/1/edit', 'cliente_edit'),
    ('/clientes/1/borrar-extension', 'cliente_borrar_ext'),

    # Docs pendientes
    ('/docs-pendientes/upload', 'docs_pend_upload'),
    ('/docs-pendientes/1/delete', 'docs_pend_delete'),

    # Converter
    ('/converter/upload', 'converter_upload'),
    ('/converter/abc-token/analizar', 'converter_analizar'),
    ('/converter/abc-token/delete', 'converter_delete'),
    ('/converter/delete-bulk', 'converter_delete_bulk'),

    # Batch
    ('/batch/add-pdf', 'batch_add_pdf'),
    ('/batch/process', 'batch_process'),

    # Home cards
    ('/configuracion/personalizar-home', 'personalizar_home'),
]


@pytest.mark.parametrize('path,descripcion', RUTAS_POST,
                          ids=[d for _, d in RUTAS_POST])
def test_post_no_revienta(smoke_client, path, descripcion):
    """POST con form vacío no debe devolver 500. Cualquier 4xx/3xx es OK."""
    resp = smoke_client.post(path, data={}, follow_redirects=False)
    assert resp.status_code != 500, (
        f'POST {path} ({descripcion}) devolvió 500.\n'
        f'Body: {resp.data[:500]!r}'
    )
    # El conjunto de códigos aceptables es amplio: validación, redirect, 404, etc.
    # Lo importante es que el handler haya devuelto antes de crashear.
    assert resp.status_code < 500, (
        f'POST {path} ({descripcion}) devolvió código de error: {resp.status_code}\n'
        f'Body: {resp.data[:500]!r}'
    )
