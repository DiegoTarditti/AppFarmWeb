"""Shared pytest fixtures for all tests."""

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import database


@pytest.fixture(scope='session', autouse=True)
def init_test_db():
    """Initialize SQLite in-memory DB — bypasses init_db() to avoid migration failures."""
    eng = create_engine('sqlite:///:memory:', echo=False, future=True)
    database.Base.metadata.create_all(eng)
    database.engine = eng
    database.SessionLocal = sessionmaker(
        bind=eng, autoflush=False, autocommit=False, expire_on_commit=False
    )


@pytest.fixture(autouse=True)
def _limpiar_tablas_entre_tests():
    """Trunca todas las tablas antes de cada test para aislar."""
    yield
    s = database.SessionLocal()
    try:
        for table in reversed(database.Base.metadata.sorted_tables):
            s.execute(table.delete())
        s.commit()
    finally:
        s.close()


@pytest.fixture(scope='session')
def flask_app(init_test_db, tmp_path_factory):
    upload_dir = str(tmp_path_factory.mktemp('uploads'))

    app = Flask(__name__, template_folder='../templates')
    app.secret_key = 'test-secret'
    app.config['TESTING'] = True
    app.config['UPLOAD_FOLDER'] = upload_dir

    class _AnonUser:
        is_authenticated = False
        nombre_completo = None
        username = None
        rol = None
    app.jinja_env.globals['current_user'] = _AnonUser()
    app.jinja_env.globals['tiene_permiso'] = lambda *a, **k: False

    # Mock del context processor `entorno` (que en producción se inyecta desde app.py)
    class _Entorno:
        codigo = 'test'
        label = 'Test'
        color = '#888'
    app.jinja_env.globals['entorno'] = _Entorno()

    from flask import url_for as _real_url_for
    def _tolerant_url_for(endpoint, **values):
        try:
            return _real_url_for(endpoint, **values)
        except Exception:
            return '#'
    app.jinja_env.globals['url_for'] = _tolerant_url_for

    # Flask-Login con un user dummy autenticado: bypassea @login_required.
    from flask_login import LoginManager, UserMixin
    lm = LoginManager(app)

    class _DummyUser(UserMixin):
        id = '1'
        username = 'test'
        rol = 'dev'
        nombre_completo = 'Test'

    @lm.user_loader
    def _load_user(uid):
        return _DummyUser()

    @lm.request_loader
    def _request_load_user(_req):
        # Cualquier request en tests lleva al user dummy.
        return _DummyUser()

    import routes.invoices as _inv
    import routes.claims as _claims
    import routes.plantillas as _plant
    import routes.inferencia as _infer
    _inv.init_app(app)
    _claims.init_app(app)
    _plant.init_app(app)
    _infer.init_app(app)

    return app


@pytest.fixture
def client(flask_app):
    with flask_app.test_client() as c:
        yield c
