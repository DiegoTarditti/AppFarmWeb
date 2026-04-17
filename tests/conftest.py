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


@pytest.fixture(scope='session')
def flask_app(init_test_db, tmp_path_factory):
    upload_dir = str(tmp_path_factory.mktemp('uploads'))

    app = Flask(__name__, template_folder='../templates')
    app.secret_key = 'test-secret'
    app.config['TESTING'] = True
    app.config['UPLOAD_FOLDER'] = upload_dir

    import routes.invoices as _inv
    import routes.claims as _claims
    _inv.init_app(app)
    _claims.init_app(app)

    return app


@pytest.fixture
def client(flask_app):
    with flask_app.test_client() as c:
        yield c
