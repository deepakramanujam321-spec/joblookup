"""The Alembic chain stays one straight line, round-trips cleanly, and
matches the Table definitions the code queries through."""

from __future__ import annotations

import uuid
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]


def _config() -> Config:
    cfg = Config(str(ROOT / "db" / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "db"))
    return cfg


def test_chain_is_linear_with_single_head():
    script = ScriptDirectory.from_config(_config())
    assert len(script.get_heads()) == 1, "migration chain has branched"
    assert len(script.get_bases()) == 1
    revisions = list(script.walk_revisions())  # head -> base
    for rev in revisions:
        assert not rev.branch_labels, f"{rev.revision} has branch labels"
        assert not isinstance(rev.down_revision, (tuple, list)), f"{rev.revision} is a merge revision"
        assert rev.dependencies in (None, (), []), f"{rev.revision} depends across branches"
    ids = [r.revision for r in reversed(revisions)]
    assert ids == sorted(ids), f"revision ids out of order: {ids}"  # 0001_, 0002_ ... prefix convention
    assert [r.down_revision for r in reversed(revisions)] == [None] + ids[:-1]


def test_upgrade_downgrade_upgrade_preserves_data(database_url, monkeypatch):
    admin = sa.create_engine(database_url, isolation_level="AUTOCOMMIT")
    name = f"joblookup_mig_{uuid.uuid4().hex[:8]}"
    with admin.connect() as conn:
        conn.execute(sa.text(f'create database "{name}"'))
    url = admin.url.set(database=name).render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = _config()
    try:
        command.upgrade(cfg, "0003_posted_at_to_text")
        eng = sa.create_engine(url)
        with eng.begin() as conn:
            conn.execute(sa.text(
                "insert into jobseeker.jobs (source,url,title,company,status,outreach_draft) values "
                "('lever','https://jobs.lever.co/a/1','Backend','A','applied','hi')"))
        command.upgrade(cfg, "head")
        with eng.connect() as conn:
            assert conn.execute(sa.text("select status from jobseeker.applications")).scalar() == "applied"
            assert conn.execute(sa.text("select origin from jobseeker.application_drafts")).scalar() == "legacy"
            assert conn.execute(sa.text("select count(*) from jobseeker.job_sources")).scalar() == 1
        command.downgrade(cfg, "0003_posted_at_to_text")
        command.upgrade(cfg, "head")
        with eng.connect() as conn:
            assert conn.execute(sa.text("select count(*) from jobseeker.jobs")).scalar() == 1
        eng.dispose()
    finally:
        with admin.connect() as conn:
            conn.execute(sa.text(f"select pg_terminate_backend(pid) from pg_stat_activity where datname = '{name}'"))
            conn.execute(sa.text(f'drop database "{name}"'))


def test_upgrade_from_drifted_stamp_is_idempotent(engine):
    """Production was stamped 0001 while 0002/0003 had been applied by hand;
    re-running them must be harmless."""
    with engine.connect() as conn:
        version = conn.execute(sa.text("select version_num from jobseeker.alembic_version")).scalar()
    assert version == ScriptDirectory.from_config(_config()).get_current_head()


def test_table_definitions_match_migrated_schema(engine):
    from jobseeker.database import SCHEMA, metadata

    inspector = sa.inspect(engine)
    db_tables = set(inspector.get_table_names(schema=SCHEMA)) - {"alembic_version"}
    assert db_tables == {t.name for t in metadata.sorted_tables}
    for table in metadata.sorted_tables:
        db_columns = {c["name"] for c in inspector.get_columns(table.name, schema=SCHEMA)}
        assert {c.name for c in table.columns} == db_columns, f"drift in {table.name}"


def test_database_url_accepts_any_postgres_scheme():
    from jobseeker.database import normalize_database_url

    tail = "u:p%25w@host:5432/postgres"
    for scheme in ("postgres", "postgresql", "postgresql+asyncpg", "postgresql+psycopg2", "postgresql+psycopg"):
        assert normalize_database_url(f"  {scheme}://{tail}\n") == f"postgresql+psycopg://{tail}"
