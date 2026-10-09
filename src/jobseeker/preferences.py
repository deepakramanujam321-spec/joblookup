"""Persistence around learning.py: gathers feedback/application events,
recomputes learned preferences, re-ranks, and evaluates.

Recompute is cheap (hundreds of rows, pure Python), so it runs
synchronously after every feedback submission -- the user sees the effect
of their feedback immediately, not after the next pipeline run.
"""

from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from . import assessment, learning, matching, profile as profile_mod
from .database import applications, job_feedback, jobs, learned_preferences

JOB_COLUMNS = [jobs.c.id, jobs.c.title, jobs.c.company, jobs.c.location, jobs.c.remote_type, jobs.c.seniority,
               jobs.c.job_category, jobs.c.skills, jobs.c.fit_score]


def _events(conn: Connection, owner: str, since: datetime | None) -> list[dict]:
    feedback_q = sa.select(job_feedback.c.job_id, job_feedback.c.category, job_feedback.c.created_at).where(job_feedback.c.owner == owner)
    apps_q = sa.select(applications.c.job_id, applications.c.status, applications.c.status_changed_at).where(applications.c.owner == owner)
    if since:
        feedback_q = feedback_q.where(job_feedback.c.created_at > since)
        apps_q = apps_q.where(applications.c.status_changed_at > since)
    feedback_rows = conn.execute(feedback_q).all()
    app_rows = conn.execute(apps_q).all()
    job_ids = {r.job_id for r in feedback_rows} | {r.job_id for r in app_rows}
    job_rows = {r["id"]: dict(r) for r in conn.execute(sa.select(*JOB_COLUMNS).where(jobs.c.id.in_(job_ids))).mappings()} if job_ids else {}
    events = []
    for r in feedback_rows:
        if r.job_id in job_rows:
            events.append({"kind": "feedback", "job_id": r.job_id, "category": r.category, "job": job_rows[r.job_id]})
    for r in app_rows:
        if r.job_id in job_rows:
            events.append({"kind": "application", "job_id": r.job_id, "status": r.status, "job": job_rows[r.job_id]})
    return events


def recompute(conn: Connection, owner: str) -> list[dict]:
    record = profile_mod.get_or_seed(conn, owner)
    _, resume = profile_mod.default_resume_text(conn, owner)
    skills = matching.candidate_skill_set(record["data"], resume)
    events = _events(conn, owner, record["learning_reset_at"])
    for e in events:
        e["signals"] = learning.job_signals(e["job"], skills)
    overrides = {(r.dimension, r.value): r.disabled_by_user for r in conn.execute(
        sa.select(learned_preferences.c.dimension, learned_preferences.c.value, learned_preferences.c.disabled_by_user)
        .where(learned_preferences.c.owner == owner))}
    rows = learning.compute_preferences(events, record["data"], skills, overrides)
    now = datetime.now(timezone.utc)
    keys = [(r["dimension"], r["value"]) for r in rows]
    conn.execute(sa.delete(learned_preferences).where(
        learned_preferences.c.owner == owner,
        sa.tuple_(learned_preferences.c.dimension, learned_preferences.c.value).notin_(keys) if keys else sa.true(),
    ))
    for r in rows:
        values = {**r, "owner": owner, "updated_at": now}
        values["active"] = r["active"] and not r["disabled_by_user"]
        conn.execute(pg_insert(learned_preferences).values(**values).on_conflict_do_update(
            constraint="learned_preferences_key",
            set_={k: values[k] for k in ("weight", "positive", "negative", "active", "explanation", "updated_at")},
        ))
    rerank(conn, owner, record, resume)
    return rows


def rerank(conn: Connection, owner: str, record: dict | None = None, resume: str | None = None) -> int:
    """Recomputes priority for every scored job (fit is untouched)."""
    record = record or profile_mod.get_or_seed(conn, owner)
    if resume is None:
        _, resume = profile_mod.default_resume_text(conn, owner)
    skills = matching.candidate_skill_set(record["data"], resume)
    learned = assessment.load_learned(conn, owner)
    now = datetime.now(timezone.utc)
    count = 0
    for job in conn.execute(sa.select(jobs).where(jobs.c.fit_score.is_not(None))).mappings():
        job = dict(job)
        assessment.persist_priority(conn, job, float(job["fit_score"]), record["data"], skills, learned, now)
        count += 1
    return count


def evaluation(conn: Connection, owner: str) -> dict:
    record = profile_mod.get_or_seed(conn, owner)
    _, resume = profile_mod.default_resume_text(conn, owner)
    skills = matching.candidate_skill_set(record["data"], resume)
    events = _events(conn, owner, record["learning_reset_at"])
    labelled: dict[int, dict] = {}
    for e in events:
        if e["kind"] == "feedback":
            label = 1 if e["category"] in learning.POSITIVE_FEEDBACK else 0 if e["category"] in learning.NEGATIVE_FEEDBACK else None
        else:
            label = 1 if e["status"] in learning.APPLICATION_POSITIVE_STATUSES else None
        if label is None or e["job"].get("fit_score") is None:
            continue
        e["signals"] = learning.job_signals(e["job"], skills)
        labelled[e["job_id"]] = {"job_id": e["job_id"], "label": label, "base_score": float(e["job"]["fit_score"]),
                                 "signals": e["signals"], "event": e}
    return learning.evaluate(list(labelled.values()), record["data"], skills)
