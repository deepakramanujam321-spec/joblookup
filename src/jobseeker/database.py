"""Direct Postgres access (SQLAlchemy Core, no ORM).

The migrations in db/versions/ are the source of truth for schema shape;
the Table objects below mirror them so queries are composed rather than
string-built. tests/test_schema_drift.py fails if the two diverge.

Why direct SQL rather than the PostgREST API v1 used: real transactions
(a status change and its audit event commit together or not at all), joins
without N+1 round trips, server-side full-text search, and the ability to
test every query against a real local Postgres.
"""

from __future__ import annotations

import os
from functools import lru_cache

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.engine import Engine

SCHEMA = "jobseeker"
metadata = sa.MetaData(schema=SCHEMA)
TS = sa.DateTime(timezone=True)


def _id() -> sa.Column:
    return sa.Column("id", sa.BigInteger, primary_key=True)


jobs = sa.Table(
    "jobs", metadata,
    _id(),
    sa.Column("source", sa.Text, nullable=False),
    sa.Column("external_id", sa.Text),
    sa.Column("url", sa.Text, nullable=False),
    sa.Column("canonical_url", sa.Text),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("company", sa.Text, nullable=False),
    sa.Column("company_website", sa.Text),
    sa.Column("location", sa.Text),
    sa.Column("remote_type", sa.Text),
    sa.Column("employment_type", sa.Text),
    sa.Column("seniority", sa.Text),
    sa.Column("job_category", sa.Text),
    sa.Column("experience_min_years", sa.Numeric),
    sa.Column("experience_text", sa.Text),
    sa.Column("skills", pg.ARRAY(sa.Text), nullable=False),
    sa.Column("salary_text", sa.Text),
    sa.Column("salary_min", sa.Numeric),
    sa.Column("salary_max", sa.Numeric),
    sa.Column("salary_currency", sa.Text),
    sa.Column("salary_period", sa.Text),
    sa.Column("salary_source", sa.Text),
    sa.Column("description", sa.Text),
    sa.Column("description_html", sa.Text),
    sa.Column("description_source", sa.Text),
    sa.Column("description_is_partial", sa.Boolean, nullable=False),
    sa.Column("posted_at", sa.Text),
    sa.Column("posted_at_ts", TS),
    sa.Column("posted_at_evidence", sa.Text),
    sa.Column("source_updated_at", TS),
    sa.Column("deadline_at", TS),
    sa.Column("discovered_at", TS, nullable=False),
    sa.Column("last_seen_at", TS),
    sa.Column("verification_status", sa.Text, nullable=False),
    sa.Column("last_checked_at", TS),
    sa.Column("last_verified_at", TS),
    sa.Column("verification_failures", sa.Integer, nullable=False),
    sa.Column("closed_at", TS),
    sa.Column("enriched_at", TS),
    sa.Column("enrichment_version", sa.Integer, nullable=False),
    sa.Column("listing_quality", sa.Text, nullable=False),
    sa.Column("dedupe_key", sa.Text),
    sa.Column("duplicate_of_id", sa.BigInteger),
    sa.Column("fit_score", sa.Numeric),
    sa.Column("fit_rationale", sa.Text),
    sa.Column("match_highlights", pg.JSONB),
    sa.Column("scoring_version", sa.Text),
    sa.Column("priority_score", sa.Numeric),
    sa.Column("priority_explanation", pg.JSONB),
    sa.Column("outreach_draft", sa.Text),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("digest_batch_date", sa.Date),
    sa.Column("search_tsv", pg.TSVECTOR),
    sa.Column("created_at", TS, nullable=False),
    sa.Column("updated_at", TS, nullable=False),
)

job_sources = sa.Table(
    "job_sources", metadata,
    _id(),
    sa.Column("job_id", sa.BigInteger, nullable=False),
    sa.Column("source", sa.Text, nullable=False),
    sa.Column("url", sa.Text, nullable=False),
    sa.Column("external_id", sa.Text),
    sa.Column("first_seen_at", TS, nullable=False),
    sa.Column("last_seen_at", TS, nullable=False),
)

job_verifications = sa.Table(
    "job_verifications", metadata,
    _id(),
    sa.Column("job_id", sa.BigInteger, nullable=False),
    sa.Column("checked_at", TS, nullable=False),
    sa.Column("result", sa.Text, nullable=False),
    sa.Column("method", sa.Text),
    sa.Column("http_status", sa.Integer),
    sa.Column("detail", sa.Text),
)

runs = sa.Table(
    "runs", metadata,
    _id(),
    sa.Column("run_type", sa.Text, nullable=False),
    sa.Column("started_at", TS, nullable=False),
    sa.Column("finished_at", TS),
    sa.Column("status", sa.Text),
    sa.Column("duration_ms", sa.Integer),
    sa.Column("jobs_found", sa.Integer),
    sa.Column("jobs_new", sa.Integer),
    sa.Column("jobs_duplicate", sa.Integer),
    sa.Column("jobs_rejected", sa.Integer),
    sa.Column("jobs_updated", sa.Integer),
    sa.Column("source_stats", pg.JSONB),
    sa.Column("details", pg.JSONB),
    sa.Column("error", sa.Text),
)

candidate_profiles = sa.Table(
    "candidate_profiles", metadata,
    _id(),
    sa.Column("owner", sa.Text, nullable=False),
    sa.Column("data", pg.JSONB, nullable=False),
    sa.Column("additional_info", sa.Text),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("learning_reset_at", TS),
    sa.Column("created_at", TS, nullable=False),
    sa.Column("updated_at", TS, nullable=False),
)

resumes = sa.Table(
    "resumes", metadata,
    _id(),
    sa.Column("owner", sa.Text, nullable=False),
    sa.Column("display_name", sa.Text, nullable=False),
    sa.Column("purpose", sa.Text),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("filename", sa.Text, nullable=False),
    sa.Column("content_type", sa.Text, nullable=False),
    sa.Column("size_bytes", sa.Integer, nullable=False),
    sa.Column("sha256", sa.Text, nullable=False),
    sa.Column("storage_key", sa.Text),
    sa.Column("source", sa.Text, nullable=False),
    sa.Column("drive_file_id", sa.Text),
    sa.Column("drive_modified_time", TS),
    sa.Column("is_default", sa.Boolean, nullable=False),
    sa.Column("extraction_status", sa.Text, nullable=False),
    sa.Column("extraction_error", sa.Text),
    sa.Column("extracted_text", sa.Text),
    sa.Column("extracted_profile", pg.JSONB),
    sa.Column("uploaded_at", TS, nullable=False),
    sa.Column("deleted_at", TS),
)

knowledge_documents = sa.Table(
    "knowledge_documents", metadata,
    _id(),
    sa.Column("owner", sa.Text, nullable=False),
    sa.Column("source", sa.Text, nullable=False),
    sa.Column("drive_file_id", sa.Text, nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("mime_type", sa.Text),
    sa.Column("drive_modified_time", TS),
    sa.Column("extracted_text", sa.Text),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("error", sa.Text),
    sa.Column("imported_at", TS, nullable=False),
    sa.Column("refreshed_at", TS),
    sa.Column("deleted_at", TS),
)

job_assessments = sa.Table(
    "job_assessments", metadata,
    _id(),
    sa.Column("job_id", sa.BigInteger, nullable=False),
    sa.Column("owner", sa.Text, nullable=False),
    sa.Column("scoring_version", sa.Text, nullable=False),
    sa.Column("profile_version", sa.Integer, nullable=False),
    sa.Column("input_hash", sa.Text, nullable=False),
    sa.Column("model", sa.Text),
    sa.Column("overall_score", sa.Numeric, nullable=False),
    sa.Column("llm_score", sa.Numeric),
    sa.Column("components", pg.JSONB, nullable=False),
    sa.Column("strengths", pg.JSONB, nullable=False),
    sa.Column("gaps", pg.JSONB, nullable=False),
    sa.Column("rationale", sa.Text),
    sa.Column("created_at", TS, nullable=False),
)

applications = sa.Table(
    "applications", metadata,
    _id(),
    sa.Column("owner", sa.Text, nullable=False),
    sa.Column("job_id", sa.BigInteger, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("saved", sa.Boolean, nullable=False),
    sa.Column("resume_id", sa.BigInteger),
    sa.Column("application_url", sa.Text),
    sa.Column("notes", sa.Text),
    sa.Column("recruiter_name", sa.Text),
    sa.Column("recruiter_contact", sa.Text),
    sa.Column("applied_at", TS),
    sa.Column("status_changed_at", TS, nullable=False),
    sa.Column("next_follow_up_at", TS),
    sa.Column("final_draft_id", sa.BigInteger),
    sa.Column("created_at", TS, nullable=False),
    sa.Column("updated_at", TS, nullable=False),
)

application_events = sa.Table(
    "application_events", metadata,
    _id(),
    sa.Column("application_id", sa.BigInteger, nullable=False),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("from_status", sa.Text),
    sa.Column("to_status", sa.Text),
    sa.Column("note", sa.Text),
    sa.Column("at", TS, nullable=False),
)

application_tasks = sa.Table(
    "application_tasks", metadata,
    _id(),
    sa.Column("application_id", sa.BigInteger, nullable=False),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("due_at", TS),
    sa.Column("done_at", TS),
    sa.Column("notes", sa.Text),
    sa.Column("created_at", TS, nullable=False),
)

application_drafts = sa.Table(
    "application_drafts", metadata,
    _id(),
    sa.Column("owner", sa.Text, nullable=False),
    sa.Column("job_id", sa.BigInteger, nullable=False),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("origin", sa.Text, nullable=False),
    sa.Column("parent_id", sa.BigInteger),
    sa.Column("subject", sa.Text),
    sa.Column("body", sa.Text, nullable=False),
    sa.Column("cover_letter", sa.Text),
    sa.Column("qualifications", pg.JSONB, nullable=False),
    sa.Column("missing_info", pg.JSONB, nullable=False),
    sa.Column("answers", pg.JSONB, nullable=False),
    sa.Column("resume_id", sa.BigInteger),
    sa.Column("profile_version", sa.Integer),
    sa.Column("model", sa.Text),
    sa.Column("content_hash", sa.Text, nullable=False),
    sa.Column("gmail_draft_id", sa.Text),
    sa.Column("gmail_message_id", sa.Text),
    sa.Column("gmail_status", sa.Text, nullable=False),
    sa.Column("gmail_saved_at", TS),
    sa.Column("gmail_error", sa.Text),
    sa.Column("created_at", TS, nullable=False),
)

job_feedback = sa.Table(
    "job_feedback", metadata,
    _id(),
    sa.Column("owner", sa.Text, nullable=False),
    sa.Column("job_id", sa.BigInteger, nullable=False),
    sa.Column("category", sa.Text, nullable=False),
    sa.Column("note", sa.Text),
    sa.Column("fit_score_at", sa.Numeric),
    sa.Column("priority_at", sa.Numeric),
    sa.Column("scoring_version", sa.Text),
    sa.Column("profile_version", sa.Integer),
    sa.Column("created_at", TS, nullable=False),
)

learned_preferences = sa.Table(
    "learned_preferences", metadata,
    _id(),
    sa.Column("owner", sa.Text, nullable=False),
    sa.Column("dimension", sa.Text, nullable=False),
    sa.Column("value", sa.Text, nullable=False),
    sa.Column("weight", sa.Numeric, nullable=False),
    sa.Column("positive", sa.Integer, nullable=False),
    sa.Column("negative", sa.Integer, nullable=False),
    sa.Column("active", sa.Boolean, nullable=False),
    sa.Column("disabled_by_user", sa.Boolean, nullable=False),
    sa.Column("explanation", sa.Text),
    sa.Column("updated_at", TS, nullable=False),
)

oauth_integrations = sa.Table(
    "oauth_integrations", metadata,
    _id(),
    sa.Column("owner", sa.Text, nullable=False),
    sa.Column("provider", sa.Text, nullable=False),
    sa.Column("service", sa.Text, nullable=False),
    sa.Column("account_email", sa.Text),
    sa.Column("scopes", sa.Text, nullable=False),
    sa.Column("refresh_token_enc", sa.Text),
    sa.Column("access_token_enc", sa.Text),
    sa.Column("access_token_expires_at", TS),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("last_error", sa.Text),
    sa.Column("connected_at", TS, nullable=False),
    sa.Column("updated_at", TS, nullable=False),
)

oauth_states = sa.Table(
    "oauth_states", metadata,
    sa.Column("state", sa.Text, primary_key=True),
    sa.Column("owner", sa.Text, nullable=False),
    sa.Column("service", sa.Text, nullable=False),
    sa.Column("code_verifier", sa.Text, nullable=False),
    sa.Column("created_at", TS, nullable=False),
)

audit_log = sa.Table(
    "audit_log", metadata,
    _id(),
    sa.Column("owner", sa.Text, nullable=False),
    sa.Column("action", sa.Text, nullable=False),
    sa.Column("target_type", sa.Text),
    sa.Column("target_id", sa.Text),
    sa.Column("detail", pg.JSONB),
    sa.Column("created_at", TS, nullable=False),
)


def normalize_database_url(url: str) -> str:
    """Supabase hands out `postgresql://` / `postgres://` URLs; SQLAlchemy
    would pick psycopg2 for those. Pin the psycopg (v3) driver we ship."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


@lru_cache(maxsize=4)
def get_engine(url: str | None = None) -> Engine:
    url = url or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Use Supabase's *Session pooler* connection string "
            "(Project -> Connect -> Session pooler). See README.md -> Setup."
        )
    return sa.create_engine(
        normalize_database_url(url),
        pool_size=int(os.environ.get("DB_POOL_SIZE", "3")),
        max_overflow=2,
        pool_pre_ping=True,  # free-tier hosts sleep; drop dead connections instead of erroring
        pool_recycle=300,
        # No search_path startup option: Supabase's pooler doesn't reliably
        # forward it, and every Table above is schema-qualified anyway.
        # prepare_threshold=None: no server-side prepared statements, which
        # break behind a transaction-mode pooler -- so either of Supabase's
        # pooler URIs (session :5432 or transaction :6543) works.
        connect_args={"connect_timeout": 10, "prepare_threshold": None},
    )
