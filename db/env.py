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
config.set_main_option("sqlalchemy.url", database_url)

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
        connection.execute(text(f"set search_path to {TARGET_SCHEMA}, public"))
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
