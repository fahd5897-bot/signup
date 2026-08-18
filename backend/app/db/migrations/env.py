"""Alembic environment.

Two things this file is deliberate about:

* The URL comes from ``app.core.config``, never from ``alembic.ini``. A
  migration run against a different database than the app uses is a class of
  incident this removes entirely.
* Migrations run over the **sync** psycopg driver even though the app is
  async. Alembic's autogenerate and transactional DDL are synchronous by
  nature, and driving them through an async engine buys nothing but a wrapper.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from app.core.config import get_settings

# Importing the package registers every model on Base.metadata. A model that is
# never imported is silently absent from autogenerate.
from app.db.models import Base  # noqa: F401
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """App DSN, rewritten for the sync driver Alembic uses."""
    dsn = str(get_settings().postgres_dsn)
    return dsn.replace("postgresql+asyncpg://", "postgresql+psycopg://")


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting — for reviewing a migration."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()

    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Catches a column whose Python type drifted from the database's.
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
