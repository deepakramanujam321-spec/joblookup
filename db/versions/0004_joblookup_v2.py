"""JobLookup 2.0: job intelligence, candidate profile, resumes, applications,
drafts, feedback + learned preferences, Google integrations, verification
history, richer run observability.

Purely additive -- no existing column or table is dropped or retyped, and
every new NOT NULL column has a default, so the existing collect/digest
pipeline keeps working against this schema before its code is updated.

Ownership: every user-scoped table carries an `owner` column (the account
id the API derives from authentication, never from the request body). This
deployment is single-account today (owner = 'default'); the column is what
lets it become multi-account without another data migration.

Legacy data carried forward:
  * jobs.status 'applied' / 'rejected'  -> applications rows
  * jobs.outreach_draft                  -> application_drafts version 1 (origin 'legacy')
  * jobs.url / source / external_id      -> job_sources provenance rows
Rows needing parsing (posting dates, canonical URLs, salaries, skills) are
backfilled by the enrichment step (scripts/run_enrich.py), not in SQL, so
that logic lives in one tested place.

Revision ID: 0004_joblookup_v2
Revises: 0003_posted_at_to_text
Create Date: 2026-10-09
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0004_joblookup_v2"
down_revision = "0003_posted_at_to_text"
branch_labels = None
depends_on = None

S = "jobseeker"
ROLES = "anon, authenticated, service_role"
TS = sa.DateTime(timezone=True)
NOW = sa.text("now()")

VERIFICATION_STATUSES = ("unchecked", "active", "closed", "source_unavailable", "verification_failed")
LISTING_QUALITY = ("ok", "not_a_listing", "incomplete")
APPLICATION_STATUSES = (
    "discovered", "needs_review", "shortlisted", "draft_ready", "applied",
    "recruiter_response", "interview", "offer", "rejected", "withdrawn",
    "dismissed", "closed",
)
FEEDBACK_CATEGORIES = (
    "excellent_match", "relevant_not_priority", "wrong_seniority", "wrong_stack",
    "wrong_category", "location_mismatch", "salary_mismatch", "experience_mismatch",
    "company_mismatch", "duplicate", "inaccurate_listing", "not_interested", "other",
)
RUN_TYPES = ("collect", "digest", "enrich", "verify", "rescore")


def _in(values: tuple[str, ...]) -> str:
    return ",".join(f"'{v}'" for v in values)


def _id() -> sa.Column:
    return sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True)


def _owner() -> sa.Column:
    return sa.Column("owner", sa.Text(), nullable=False)


def _created() -> sa.Column:
    return sa.Column("created_at", TS, nullable=False, server_default=NOW)


def _job_fk(nullable: bool = False) -> sa.Column:
    return sa.Column(
        "job_id", sa.BigInteger(), sa.ForeignKey(f"{S}.jobs.id", ondelete="CASCADE"), nullable=nullable
    )


def upgrade() -> None:
    # ------------------------------------------------------------------ jobs
    add = lambda col: op.add_column("jobs", col, schema=S)  # noqa: E731
    add(sa.Column("canonical_url", sa.Text()))
    add(sa.Column("description_html", sa.Text()))
    add(sa.Column("description_source", sa.Text()))
    add(sa.Column("description_is_partial", sa.Boolean(), nullable=False, server_default=sa.true()))
    add(sa.Column("employment_type", sa.Text()))
    add(sa.Column("seniority", sa.Text()))
    add(sa.Column("job_category", sa.Text()))
    add(sa.Column("experience_min_years", sa.Numeric()))
    add(sa.Column("experience_text", sa.Text()))
    add(sa.Column("skills", pg.ARRAY(sa.Text()), nullable=False, server_default="{}"))
    add(sa.Column("salary_min", sa.Numeric()))
    add(sa.Column("salary_max", sa.Numeric()))
    add(sa.Column("salary_currency", sa.Text()))
    add(sa.Column("salary_period", sa.Text()))
    add(sa.Column("salary_source", sa.Text()))
    add(sa.Column("posted_at_ts", TS))
    add(sa.Column("posted_at_evidence", sa.Text()))
    add(sa.Column("source_updated_at", TS))
    add(sa.Column("deadline_at", TS))
    add(sa.Column("company_website", sa.Text()))
    add(sa.Column("last_seen_at", TS))
    add(sa.Column("verification_status", sa.Text(), nullable=False, server_default="unchecked"))
    add(sa.Column("last_checked_at", TS))
    add(sa.Column("last_verified_at", TS))
    add(sa.Column("verification_failures", sa.Integer(), nullable=False, server_default="0"))
    add(sa.Column("closed_at", TS))
    add(sa.Column("enriched_at", TS))
    add(sa.Column("enrichment_version", sa.Integer(), nullable=False, server_default="0"))
    add(sa.Column("listing_quality", sa.Text(), nullable=False, server_default="ok"))
    add(sa.Column("dedupe_key", sa.Text()))
    add(sa.Column("duplicate_of_id", sa.BigInteger(), sa.ForeignKey(f"{S}.jobs.id", ondelete="SET NULL")))
    add(sa.Column("priority_score", sa.Numeric()))
    add(sa.Column("priority_explanation", pg.JSONB()))
    add(sa.Column("scoring_version", sa.Text()))
    add(sa.Column("match_highlights", pg.JSONB()))
    op.execute(
        f"alter table {S}.jobs add column search_tsv tsvector generated always as ("
        "to_tsvector('simple', coalesce(title,'') || ' ' || coalesce(company,'') || ' ' || coalesce(description,''))"
        ") stored"
    )
    op.create_check_constraint(
        "jobs_verification_status_check", "jobs", f"verification_status in ({_in(VERIFICATION_STATUSES)})", schema=S
    )
    op.create_check_constraint(
        "jobs_listing_quality_check", "jobs", f"listing_quality in ({_in(LISTING_QUALITY)})", schema=S
    )
    op.create_index("jobs_source_external_id_idx", "jobs", ["source", "external_id"], schema=S)
    op.create_index("jobs_canonical_url_idx", "jobs", ["canonical_url"], schema=S)
    op.create_index("jobs_company_title_idx", "jobs", [sa.text("lower(company)"), sa.text("lower(title)")], schema=S)
    op.create_index("jobs_posted_at_ts_idx", "jobs", [sa.text("posted_at_ts DESC NULLS LAST")], schema=S)
    op.create_index("jobs_discovered_at_idx", "jobs", [sa.text("discovered_at DESC")], schema=S)
    op.create_index("jobs_priority_idx", "jobs", [sa.text("priority_score DESC NULLS LAST")], schema=S)
    op.create_index("jobs_verification_idx", "jobs", ["verification_status", "last_checked_at"], schema=S)
    op.create_index("jobs_dedupe_key_idx", "jobs", ["dedupe_key"], schema=S)
    op.create_index("jobs_search_tsv_idx", "jobs", ["search_tsv"], schema=S, postgresql_using="gin")
    op.create_index("jobs_skills_idx", "jobs", ["skills"], schema=S, postgresql_using="gin")

    # ------------------------------------------------------- provenance / health
    op.create_table(
        "job_sources",
        _id(),
        _job_fk(),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False, unique=True),
        sa.Column("external_id", sa.Text()),
        sa.Column("first_seen_at", TS, nullable=False, server_default=NOW),
        sa.Column("last_seen_at", TS, nullable=False, server_default=NOW),
        schema=S,
    )
    op.create_index("job_sources_job_idx", "job_sources", ["job_id"], schema=S)

    op.create_table(
        "job_verifications",
        _id(),
        _job_fk(),
        sa.Column("checked_at", TS, nullable=False, server_default=NOW),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("method", sa.Text()),
        sa.Column("http_status", sa.Integer()),
        sa.Column("detail", sa.Text()),
        sa.CheckConstraint(f"result in ({_in(VERIFICATION_STATUSES)})", name="job_verifications_result_check"),
        schema=S,
    )
    op.create_index("job_verifications_job_idx", "job_verifications", ["job_id", sa.text("checked_at DESC")], schema=S)

    op.drop_constraint("runs_run_type_check", "runs", schema=S)
    op.create_check_constraint("runs_run_type_check", "runs", f"run_type in ({_in(RUN_TYPES)})", schema=S)
    for col in (
        sa.Column("status", sa.Text()),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("jobs_duplicate", sa.Integer(), server_default="0"),
        sa.Column("jobs_rejected", sa.Integer(), server_default="0"),
        sa.Column("jobs_updated", sa.Integer(), server_default="0"),
        sa.Column("source_stats", pg.JSONB()),
        sa.Column("details", pg.JSONB()),
    ):
        op.add_column("runs", col, schema=S)
    op.create_index("runs_type_started_idx", "runs", ["run_type", sa.text("started_at DESC")], schema=S)

    # ------------------------------------------------------ profile + documents
    op.create_table(
        "candidate_profiles",
        _id(),
        sa.Column("owner", sa.Text(), nullable=False, unique=True),
        sa.Column("data", pg.JSONB(), nullable=False, server_default="{}"),
        sa.Column("additional_info", sa.Text()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("learning_reset_at", TS),
        _created(),
        sa.Column("updated_at", TS, nullable=False, server_default=NOW),
        schema=S,
    )

    op.create_table(
        "resumes",
        _id(),
        _owner(),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text()),
        sa.Column("source", sa.Text(), nullable=False, server_default="upload"),
        sa.Column("drive_file_id", sa.Text()),
        sa.Column("drive_modified_time", TS),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("extraction_status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("extraction_error", sa.Text()),
        sa.Column("extracted_text", sa.Text()),
        sa.Column("extracted_profile", pg.JSONB()),
        sa.Column("uploaded_at", TS, nullable=False, server_default=NOW),
        sa.Column("deleted_at", TS),
        sa.CheckConstraint("source in ('upload','drive','seed')", name="resumes_source_check"),
        sa.CheckConstraint(
            "extraction_status in ('pending','done','failed')", name="resumes_extraction_status_check"
        ),
        schema=S,
    )
    op.create_index("resumes_owner_idx", "resumes", ["owner"], schema=S)
    op.create_index(
        "resumes_one_default_idx", "resumes", ["owner"], unique=True, schema=S,
        postgresql_where=sa.text("is_default and deleted_at is null"),
    )

    op.create_table(
        "knowledge_documents",
        _id(),
        _owner(),
        sa.Column("source", sa.Text(), nullable=False, server_default="drive"),
        sa.Column("drive_file_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text()),
        sa.Column("drive_modified_time", TS),
        sa.Column("extracted_text", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text()),
        sa.Column("imported_at", TS, nullable=False, server_default=NOW),
        sa.Column("refreshed_at", TS),
        sa.Column("deleted_at", TS),
        sa.UniqueConstraint("owner", "drive_file_id", name="knowledge_documents_owner_file_key"),
        schema=S,
    )

    # ---------------------------------------------------------------- matching
    op.create_table(
        "job_assessments",
        _id(),
        _job_fk(),
        _owner(),
        sa.Column("scoring_version", sa.Text(), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("input_hash", sa.Text(), nullable=False),
        sa.Column("model", sa.Text()),
        sa.Column("overall_score", sa.Numeric(), nullable=False),
        sa.Column("llm_score", sa.Numeric()),
        sa.Column("components", pg.JSONB(), nullable=False),
        sa.Column("strengths", pg.JSONB(), nullable=False, server_default="[]"),
        sa.Column("gaps", pg.JSONB(), nullable=False, server_default="[]"),
        sa.Column("rationale", sa.Text()),
        _created(),
        sa.UniqueConstraint("job_id", "owner", "input_hash", name="job_assessments_cache_key"),
        schema=S,
    )
    op.create_index("job_assessments_job_idx", "job_assessments", ["job_id", sa.text("created_at DESC")], schema=S)

    # ------------------------------------------------------------ applications
    op.create_table(
        "applications",
        _id(),
        _owner(),
        _job_fk(),
        sa.Column("status", sa.Text(), nullable=False, server_default="needs_review"),
        sa.Column("saved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("resume_id", sa.BigInteger(), sa.ForeignKey(f"{S}.resumes.id", ondelete="SET NULL")),
        sa.Column("application_url", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.Column("recruiter_name", sa.Text()),
        sa.Column("recruiter_contact", sa.Text()),
        sa.Column("applied_at", TS),
        sa.Column("status_changed_at", TS, nullable=False, server_default=NOW),
        sa.Column("next_follow_up_at", TS),
        sa.Column("final_draft_id", sa.BigInteger()),
        _created(),
        sa.Column("updated_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint("owner", "job_id", name="applications_owner_job_key"),
        sa.CheckConstraint(f"status in ({_in(APPLICATION_STATUSES)})", name="applications_status_check"),
        schema=S,
    )
    op.create_index("applications_owner_status_idx", "applications", ["owner", "status"], schema=S)

    op.create_table(
        "application_events",
        _id(),
        sa.Column(
            "application_id", sa.BigInteger(),
            sa.ForeignKey(f"{S}.applications.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("from_status", sa.Text()),
        sa.Column("to_status", sa.Text()),
        sa.Column("note", sa.Text()),
        sa.Column("at", TS, nullable=False, server_default=NOW),
        schema=S,
    )
    op.create_index("application_events_app_idx", "application_events", ["application_id", "at"], schema=S)

    op.create_table(
        "application_tasks",
        _id(),
        sa.Column(
            "application_id", sa.BigInteger(),
            sa.ForeignKey(f"{S}.applications.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("due_at", TS),
        sa.Column("done_at", TS),
        sa.Column("notes", sa.Text()),
        _created(),
        sa.CheckConstraint("kind in ('interview','follow_up','reminder')", name="application_tasks_kind_check"),
        schema=S,
    )
    op.create_index("application_tasks_app_idx", "application_tasks", ["application_id"], schema=S)

    op.create_table(
        "application_drafts",
        _id(),
        _owner(),
        _job_fk(),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("parent_id", sa.BigInteger()),
        sa.Column("subject", sa.Text()),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("cover_letter", sa.Text()),
        sa.Column("qualifications", pg.JSONB(), nullable=False, server_default="[]"),
        sa.Column("missing_info", pg.JSONB(), nullable=False, server_default="[]"),
        sa.Column("answers", pg.JSONB(), nullable=False, server_default="[]"),
        sa.Column("resume_id", sa.BigInteger(), sa.ForeignKey(f"{S}.resumes.id", ondelete="SET NULL")),
        sa.Column("profile_version", sa.Integer()),
        sa.Column("model", sa.Text()),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("gmail_draft_id", sa.Text()),
        sa.Column("gmail_message_id", sa.Text()),
        sa.Column("gmail_status", sa.Text(), nullable=False, server_default="not_saved"),
        sa.Column("gmail_saved_at", TS),
        sa.Column("gmail_error", sa.Text()),
        _created(),
        sa.UniqueConstraint("owner", "job_id", "version", name="application_drafts_version_key"),
        sa.CheckConstraint("origin in ('generated','edited','legacy')", name="application_drafts_origin_check"),
        sa.CheckConstraint(
            "gmail_status in ('not_saved','saved','failed','missing')", name="application_drafts_gmail_status_check"
        ),
        schema=S,
    )

    # ---------------------------------------------------------------- feedback
    op.create_table(
        "job_feedback",
        _id(),
        _owner(),
        _job_fk(),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("fit_score_at", sa.Numeric()),
        sa.Column("priority_at", sa.Numeric()),
        sa.Column("scoring_version", sa.Text()),
        sa.Column("profile_version", sa.Integer()),
        _created(),
        sa.CheckConstraint(f"category in ({_in(FEEDBACK_CATEGORIES)})", name="job_feedback_category_check"),
        schema=S,
    )
    op.create_index("job_feedback_owner_idx", "job_feedback", ["owner", sa.text("created_at DESC")], schema=S)
    op.create_index("job_feedback_job_idx", "job_feedback", ["job_id"], schema=S)

    op.create_table(
        "learned_preferences",
        _id(),
        _owner(),
        sa.Column("dimension", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("weight", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("positive", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("negative", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("disabled_by_user", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("explanation", sa.Text()),
        sa.Column("updated_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint("owner", "dimension", "value", name="learned_preferences_key"),
        schema=S,
    )

    # ------------------------------------------------------------ integrations
    op.create_table(
        "oauth_integrations",
        _id(),
        _owner(),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("service", sa.Text(), nullable=False),
        sa.Column("account_email", sa.Text()),
        sa.Column("scopes", sa.Text(), nullable=False),
        sa.Column("refresh_token_enc", sa.Text()),
        sa.Column("access_token_enc", sa.Text()),
        sa.Column("access_token_expires_at", TS),
        sa.Column("status", sa.Text(), nullable=False, server_default="connected"),
        sa.Column("last_error", sa.Text()),
        sa.Column("connected_at", TS, nullable=False, server_default=NOW),
        sa.Column("updated_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint("owner", "provider", "service", name="oauth_integrations_key"),
        sa.CheckConstraint("status in ('connected','error','revoked')", name="oauth_integrations_status_check"),
        schema=S,
    )
    op.create_table(
        "oauth_states",
        sa.Column("state", sa.Text(), primary_key=True),
        _owner(),
        sa.Column("service", sa.Text(), nullable=False),
        sa.Column("code_verifier", sa.Text(), nullable=False),
        _created(),
        schema=S,
    )
    op.create_table(
        "audit_log",
        _id(),
        _owner(),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text()),
        sa.Column("target_id", sa.Text()),
        sa.Column("detail", pg.JSONB()),
        _created(),
        schema=S,
    )
    op.create_index("audit_log_owner_idx", "audit_log", ["owner", sa.text("created_at DESC")], schema=S)

    # ----------------------------------------------------------- legacy data
    op.execute(
        f"""insert into {S}.job_sources (job_id, source, url, external_id, first_seen_at, last_seen_at)
            select id, source, url, nullif(external_id, ''), discovered_at, discovered_at from {S}.jobs
            on conflict (url) do nothing"""
    )
    op.execute(f"update {S}.jobs set last_seen_at = discovered_at where last_seen_at is null")
    op.execute(
        f"""insert into {S}.applications (owner, job_id, status, applied_at, status_changed_at)
            select 'default', id, status,
                   case when status = 'applied' then updated_at end, updated_at
            from {S}.jobs where status in ('applied', 'rejected')
            on conflict (owner, job_id) do nothing"""
    )
    op.execute(
        f"""insert into {S}.application_drafts (owner, job_id, version, origin, body, content_hash, created_at)
            select 'default', id, 1, 'legacy', outreach_draft, md5(outreach_draft), updated_at
            from {S}.jobs where coalesce(outreach_draft, '') <> ''
            on conflict do nothing"""
    )

    # ------------------------------------------------------- security posture
    # Same posture as 0001: RLS on, no policies -- only the server-side
    # service connection ever touches these tables.
    for table in (
        "job_sources", "job_verifications", "candidate_profiles", "resumes", "knowledge_documents",
        "job_assessments", "applications", "application_events", "application_tasks",
        "application_drafts", "job_feedback", "learned_preferences", "oauth_integrations",
        "oauth_states", "audit_log",
    ):
        op.execute(f"alter table {S}.{table} enable row level security")
    # 0002's default privileges only cover tables created by the role that
    # ran it; grant explicitly so this holds whichever role runs this.
    op.execute(f"grant all on all tables in schema {S} to {ROLES}")
    op.execute(f"grant all on all sequences in schema {S} to {ROLES}")


def downgrade() -> None:
    for table in (
        "audit_log", "oauth_states", "oauth_integrations", "learned_preferences", "job_feedback",
        "application_drafts", "application_tasks", "application_events", "applications",
        "job_assessments", "knowledge_documents", "resumes", "candidate_profiles",
        "job_verifications", "job_sources",
    ):
        op.drop_table(table, schema=S)
    op.drop_index("runs_type_started_idx", "runs", schema=S)
    for col in ("status", "duration_ms", "jobs_duplicate", "jobs_rejected", "jobs_updated", "source_stats", "details"):
        op.drop_column("runs", col, schema=S)
    op.execute(f"delete from {S}.runs where run_type not in ('collect','digest')")
    op.drop_constraint("runs_run_type_check", "runs", schema=S)
    op.create_check_constraint("runs_run_type_check", "runs", "run_type in ('collect','digest')", schema=S)
    # Indexes over pre-existing columns survive the column drops below.
    for idx in ("jobs_source_external_id_idx", "jobs_company_title_idx", "jobs_discovered_at_idx"):
        op.drop_index(idx, "jobs", schema=S)
    op.drop_constraint("jobs_verification_status_check", "jobs", schema=S)
    op.drop_constraint("jobs_listing_quality_check", "jobs", schema=S)
    for col in (
        "search_tsv", "match_highlights", "scoring_version", "priority_explanation", "priority_score", "duplicate_of_id",
        "dedupe_key", "listing_quality", "enrichment_version", "enriched_at", "closed_at",
        "verification_failures", "last_verified_at", "last_checked_at", "verification_status",
        "last_seen_at", "company_website", "deadline_at", "source_updated_at", "posted_at_evidence",
        "posted_at_ts", "salary_source", "salary_period", "salary_currency", "salary_max", "salary_min",
        "skills", "experience_text", "experience_min_years", "job_category", "seniority",
        "employment_type", "description_is_partial", "description_source", "description_html",
        "canonical_url",
    ):
        op.drop_column("jobs", col, schema=S)
