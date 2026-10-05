"""initial schema: jobseeker.jobs, jobseeker.runs

This migration's DDL matches what was applied directly against the project
on 2026-10-05 when the schema was first created (via Supabase's SQL runner,
before this Alembic project existed). The live database's
jobseeker.alembic_version table is already stamped at this revision, so
running `alembic upgrade head` against it is a no-op that confirms sync,
not a re-create. See db/README.md.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-10-05
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None

JOBS_STATUS_VALUES = (
    "new", "scored", "queued_for_digest", "sent_in_digest",
    "applied", "rejected", "excluded",
)


def upgrade() -> None:
    op.execute("create schema if not exists jobseeker")

    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text()),
        sa.Column("url", sa.Text(), nullable=False, unique=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("company", sa.Text(), nullable=False),
        sa.Column("location", sa.Text()),
        sa.Column("remote_type", sa.Text()),
        sa.Column("salary_text", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("posted_at", sa.DateTime(timezone=True)),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("fit_score", sa.Numeric()),
        sa.Column("fit_rationale", sa.Text()),
        sa.Column("outreach_draft", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False, server_default="new"),
        sa.Column("digest_batch_date", sa.Date()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "status in (" + ",".join(f"'{v}'" for v in JOBS_STATUS_VALUES) + ")",
            name="jobs_status_check",
        ),
        schema="jobseeker",
    )
    op.create_index("jobs_status_idx", "jobs", ["status"], schema="jobseeker")
    op.create_index("jobs_fit_score_idx", "jobs", [sa.text("fit_score DESC")], schema="jobseeker")
    op.execute("alter table jobseeker.jobs enable row level security")
    # No RLS policies added, by design: this table is only ever written to
    # via scripts/db.py using the service-role key (bypasses RLS), never
    # from a public/anon client.

    op.create_table(
        "runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("run_type", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("jobs_found", sa.Integer(), server_default="0"),
        sa.Column("jobs_new", sa.Integer(), server_default="0"),
        sa.Column("error", sa.Text()),
        sa.CheckConstraint("run_type in ('collect','digest')", name="runs_run_type_check"),
        schema="jobseeker",
    )
    op.execute("alter table jobseeker.runs enable row level security")

    # PostgREST only serves `public` by default -- expose jobseeker too so
    # scripts/db.py can reach it via Accept-Profile/Content-Profile headers.
    op.execute("alter role authenticator set pgrst.db_schemas = 'public, jobseeker'")
    op.execute("notify pgrst, 'reload config'")


def downgrade() -> None:
    op.drop_table("runs", schema="jobseeker")
    op.drop_table("jobs", schema="jobseeker")
    op.execute("drop schema if exists jobseeker cascade")
