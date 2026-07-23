"""Alembic environment config.

Resuelve `DATABASE_URL` desde la env var (igual que `database.py`) en lugar
de tomarla del .ini. Asi alembic apunta a la MISMA db que la app en cada
instancia (local Badia, Pieri, Render) sin hardcoding.

`target_metadata = database.Base.metadata` habilita autogenerate.
"""
import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Aseguramos que el dir del proyecto este en sys.path para importar database/.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_HERE)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Importamos despues de ajustar sys.path. database.Base es el Base de SQLAlchemy.
import database  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ── Resolver URL: prioridad ALEMBIC_DATABASE_URL > DATABASE_URL > .ini ──
# ALEMBIC_DATABASE_URL permite override solo para alembic (apuntar a una db
# de staging, por ejemplo) sin tocar la de la app.
_url = (os.environ.get('ALEMBIC_DATABASE_URL')
        or os.environ.get('DATABASE_URL')
        or config.get_main_option('sqlalchemy.url'))
if _url:
    config.set_main_option('sqlalchemy.url', _url)

# Metadata para autogenerate
target_metadata = database.Base.metadata


def run_migrations_offline() -> None:
    """'Offline' mode — escribe SQL al stdout sin conectarse a la db."""
    url = config.get_main_option('sqlalchemy.url')
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={'paramstyle': 'named'},
        compare_type=True,            # detecta cambios de tipo de columna
        compare_server_default=True,  # detecta cambios de DEFAULT
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """'Online' mode — corre las migraciones contra la db real."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix='sqlalchemy.',
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
