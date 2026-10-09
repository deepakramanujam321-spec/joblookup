"""Test harness: a real Postgres database migrated with the real Alembic
chain (db/versions), so tests exercise the exact schema production runs.

Set TEST_DATABASE_URL to an admin connection on a disposable server, e.g.
    postgresql+psycopg://postgres@localhost:5432/postgres
A fresh database is created per test session and dropped afterwards.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "webapp"))

SUPABASE_ROLES = ("anon", "authenticated", "service_role", "authenticator")


def _admin_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set; database tests need a disposable Postgres", allow_module_level=True)
    return url


@pytest.fixture(scope="session")
def database_url():
    admin_url = _admin_url()
    name = f"joblookup_test_{uuid.uuid4().hex[:8]}"
    admin = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f'create database "{name}"'))
        for role in SUPABASE_ROLES:  # migrations grant to Supabase's built-in roles
            conn.execute(sa.text(
                f"do $$ begin if not exists (select from pg_roles where rolname = '{role}') then create role {role}; end if; end $$"
            ))
    url = admin.url.set(database=name).render_as_string(hide_password=False)
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "db" / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "db"))
    os.environ["DATABASE_URL"] = url
    command.upgrade(cfg, "head")
    yield url
    from jobseeker.database import get_engine

    get_engine.cache_clear()
    with admin.connect() as conn:
        conn.execute(sa.text(f"select pg_terminate_backend(pid) from pg_stat_activity where datname = '{name}'"))
        conn.execute(sa.text(f'drop database "{name}"'))


@pytest.fixture()
def engine(database_url):
    from jobseeker.database import get_engine, metadata

    eng = get_engine(database_url)
    yield eng
    tables = ", ".join(f"jobseeker.{t.name}" for t in metadata.sorted_tables)
    with eng.begin() as conn:
        conn.execute(sa.text(f"truncate {tables} restart identity cascade"))


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    for key in ("LLM_MODEL", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY",
                "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "DASHBOARD_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-key")


# ---------------------------------------------------------------- factories

def make_listing(**overrides):
    from jobseeker.models import JobListing

    base = dict(
        source="lever", url=f"https://jobs.lever.co/acme/{uuid.uuid4()}", title="Senior Backend Engineer",
        company="Acme", location="Remote - India", remote_type="remote",
        description="We build distributed systems in Python with FastAPI and PostgreSQL. 4+ years of experience required. Kubernetes a plus.",
        posted_at="1759900000000", posted_at_evidence="lever_api.createdAt", description_source="lever_api",
        description_is_partial=False,
    )
    base.update(overrides)
    return JobListing(**base)


@pytest.fixture()
def make_job(engine):
    from jobseeker import ingest

    def _make(**overrides) -> int:
        with engine.begin() as conn:
            stats = ingest.upsert_listings(conn, [make_listing(**overrides)])
        assert stats.new_ids, stats
        return stats.new_ids[0]

    return _make


@pytest.fixture()
def client(engine, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DASHBOARD_USERNAME", "tom")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "s3cret")
    import main

    c = TestClient(main.app)
    c.auth = ("tom", "s3cret")
    return c
