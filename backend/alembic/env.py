"""Alembic environment.

This script is invoked by the `alembic` CLI. It reads the database
URL from the project's central `Settings` object (which means env
vars / .env are honoured) and points Alembic at the project's
declarative metadata.

Run migrations with:

    cd backend
    alembic upgrade head           # apply all
    alembic revision --autogenerate -m "add foo"    # generate new
    alembic downgrade -1           # roll back one
"""
from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make `core.*` importable when alembic runs from the backend dir.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from core.config import get_settings  # noqa: E402
from core.models import Base  # noqa: E402

# Alembic config object (the [alembic] section of alembic.ini).
config = context.config

# Logging config from alembic.ini.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject the real DATABASE_URL from settings.
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

# All models must be importable so Base.metadata is populated.
target_metadata = Base.metadata


def include_object(object, name, type_, reflected, compare_to):
    """Filter migration scope.

    We deliberately ignore TimescaleDB-internal tables (`_timescaledb_*`)
    so autogenerate doesn't try to drop them.
    """
    if type_ == "table" and (name.startswith("_timescaledb_") or name.startswith("timescaledb_")):
        return False
    return True


def run_migrations_offline() -> None:
    """Generate SQL without connecting to the database."""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against the actual database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
