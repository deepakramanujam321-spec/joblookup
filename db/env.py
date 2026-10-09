"""Alembic environment for the `jobseeker` schema.

Deliberately not ORM-backed (target_metadata = None) — migrations here are
hand-written SQL via `op.execute`/`op.create_table`, which matches how small
this schema is and keeps the migration files as the single source of truth
for schema shape, with no model-vs-migration drift to manage.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = os.environ.get("DATABASE_URL")
if not database_url:
    raise RuntimeError(
        "DATABASE_URL is not set. This is Supabase's direct Postgres connection "
        "string (Project Settings -> Database -> Connection string -> "
        "'Transaction pooler' or 'Session pooler'), which is different from the "
        "SUPABASE_SERVICE_ROLE_KEY the app uses at runtime for REST calls. "
        "See db/README.md."
    )
# Escape % for configparser: URL-encoded passwords (e.g. %40 for "@")
# otherwise crash with "invalid interpolation syntax".
database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1).replace("postgres://", "postgresql+psycopg://", 1)
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

target_metadata = None
TARGET_SCHEMA = "jobseeker"


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        version_table_schema=TARGET_SCHEMA,
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # The version table lives in this schema, so it has to exist before
        # Alembic looks for that table -- on a brand-new database (anyone
        # reusing this repo) it doesn't yet, and 0001 never gets to run.
        connection.execute(text(f"create schema if not exists {TARGET_SCHEMA}"))
        connection.execute(text(f"set search_path to {TARGET_SCHEMA}, public"))
        # Commit that autobegun transaction: otherwise begin_transaction()
        # below joins it instead of owning it, and every migration is
        # silently rolled back when the connection closes.
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=TARGET_SCHEMA,
            include_schemas=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
