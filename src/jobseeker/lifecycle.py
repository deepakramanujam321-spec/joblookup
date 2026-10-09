"""Application status model and its transition rules.

A job with no application row is implicitly "discovered" (or
"needs_review" once it scores high enough) -- rows are created the first
time the user acts on a job. Every status change writes an
application_events row in the same transaction.

Rules that prevent contradictory states:
  * Employer-side outcomes (recruiter response, interview, offer,
    rejection) and withdrawing require having applied first.
  * "applied" is only ever set by an explicit user action. Opening the
    original posting, generating a draft or saving one to Gmail never
    marks anything as submitted.
  * Generating a draft may advance discovered/needs_review/shortlisted to
    draft_ready -- a fact about the draft, not about the application.
"""

from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from .database import application_events, applications

STATUSES = (
    "discovered", "needs_review", "shortlisted", "draft_ready", "applied", "recruiter_response",
    "interview", "offer", "rejected", "withdrawn", "dismissed", "closed",
)
PRE_APPLICATION = {"discovered", "needs_review", "shortlisted", "draft_ready", "dismissed", "closed"}
REQUIRES_APPLIED = {"recruiter_response", "interview", "offer", "rejected", "withdrawn"}
ACTIVE_PIPELINE = {"shortlisted", "draft_ready", "applied", "recruiter_response", "interview", "offer"}

TRANSITIONS: dict[str, set[str]] = {
    "discovered": {"needs_review", "shortlisted", "draft_ready", "applied", "dismissed", "closed"},
    "needs_review": {"shortlisted", "draft_ready", "applied", "dismissed", "closed", "discovered"},
    "shortlisted": {"needs_review", "draft_ready", "applied", "dismissed", "closed"},
    "draft_ready": {"shortlisted", "applied", "dismissed", "closed"},
    "applied": {"recruiter_response", "interview", "offer", "rejected", "withdrawn", "closed", "draft_ready"},
    "recruiter_response": {"interview", "offer", "rejected", "withdrawn", "applied"},
    "interview": {"offer", "rejected", "withdrawn", "recruiter_response"},
    "offer": {"withdrawn", "rejected", "interview"},
    "rejected": {"applied", "interview"},  # correcting a mistaken status
    "withdrawn": {"applied", "interview", "offer"},
    "dismissed": {"needs_review", "shortlisted", "discovered"},  # restore
    "closed": {"needs_review", "shortlisted", "applied"},  # reopened / was a mistake
}
DRAFT_ADVANCES_FROM = {"discovered", "needs_review", "shortlisted"}


class TransitionError(ValueError):
    pass


def validate(current: str, target: str, has_applied: bool) -> None:
    if target not in STATUSES:
        raise TransitionError(f"Unknown status '{target}'.")
    if current == target:
        return
    if target in REQUIRES_APPLIED and not has_applied and current != "applied":
        raise TransitionError(f"'{target}' requires the application to be marked as applied first.")
    if target not in TRANSITIONS.get(current, set()):
        raise TransitionError(f"Can't move an application from '{current}' to '{target}'.")


def implicit_status(fit_or_priority: float | None, review_threshold: float, closed: bool) -> str:
    if closed:
        return "closed"
    if fit_or_priority is not None and fit_or_priority >= review_threshold:
        return "needs_review"
    return "discovered"


def get(conn: Connection, owner: str, job_id: int):
    return conn.execute(
        sa.select(applications).where(applications.c.owner == owner, applications.c.job_id == job_id)
    ).first()


def ensure(conn: Connection, owner: str, job_id: int, initial_status: str):
    conn.execute(
        pg_insert(applications)
        .values(owner=owner, job_id=job_id, status=initial_status)
        .on_conflict_do_nothing(constraint="applications_owner_job_key")
    )
    return conn.execute(
        sa.select(applications).where(applications.c.owner == owner, applications.c.job_id == job_id).with_for_update()
    ).one()


def transition(conn: Connection, owner: str, job_id: int, target: str, initial_status: str, note: str | None = None, now: datetime | None = None):
    """Must run inside a transaction (engine.begin()); the row lock from
    ensure() serialises concurrent changes to the same application."""
    now = now or datetime.now(timezone.utc)
    app = ensure(conn, owner, job_id, initial_status)
    if app.status == target:
        return app
    validate(app.status, target, app.applied_at is not None)
    values = {"status": target, "status_changed_at": now, "updated_at": now}
    if target == "applied" and app.applied_at is None:
        values["applied_at"] = now
    conn.execute(sa.update(applications).where(applications.c.id == app.id).values(**values))
    conn.execute(
        sa.insert(application_events).values(
            application_id=app.id, kind="status_change", from_status=app.status, to_status=target, note=note, at=now
        )
    )
    return conn.execute(sa.select(applications).where(applications.c.id == app.id)).one()


def record_event(conn: Connection, application_id: int, kind: str, note: str) -> None:
    conn.execute(sa.insert(application_events).values(application_id=application_id, kind=kind, note=note))
