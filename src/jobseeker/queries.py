"""Read-side queries for the dashboard API.

The list query returns compact rows only (no descriptions) with server-side
filtering, sorting and pagination in a single round trip (total via a
window count). Detail is fetched on demand.

"Workflow status" is the user's application status when they've acted on
a job, otherwise derived: closed listings are "closed", jobs at or above
the review threshold are "needs_review", the rest "discovered".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from . import lifecycle
from .database import (application_drafts, application_events, application_tasks, applications, job_assessments,
                       job_feedback, job_sources, job_verifications, jobs, runs)

SORTS = {
    "priority": [jobs.c.priority_score.desc().nullslast(), jobs.c.fit_score.desc().nullslast(), jobs.c.id.desc()],
    "fit": [jobs.c.fit_score.desc().nullslast(), jobs.c.id.desc()],
    "newest": [jobs.c.posted_at_ts.desc().nullslast(), jobs.c.discovered_at.desc()],
    "discovered": [jobs.c.discovered_at.desc(), jobs.c.id.desc()],
    "deadline": [jobs.c.deadline_at.asc().nullslast(), jobs.c.priority_score.desc().nullslast()],
    "company": [sa.func.lower(jobs.c.company).asc(), jobs.c.title.asc()],
}
VIEWS = ("all", "recommended", "needs_review", "saved", "recent", "stale", "applied", "dismissed", "closed")


@dataclass
class JobFilters:
    q: str | None = None
    view: str = "recommended"
    status: str | None = None
    min_score: float | None = None
    seniority: str | None = None
    remote_type: str | None = None
    employment_type: str | None = None
    location: str | None = None
    source: str | None = None
    posted_within_days: int | None = None
    sort: str = "priority"
    page: int = 1
    page_size: int = 25


def workflow_status_expr(review_threshold: float):
    return sa.func.coalesce(
        applications.c.status,
        sa.case(
            (sa.or_(jobs.c.verification_status == "closed", jobs.c.listing_quality == "not_a_listing"), "closed"),
            (sa.func.coalesce(jobs.c.priority_score, jobs.c.fit_score) >= review_threshold, "needs_review"),
            else_="discovered",
        ),
    )


def _apply_filters(query, f: JobFilters, owner: str, review_threshold: float, stale_days: int, now: datetime):
    status_expr = workflow_status_expr(review_threshold)
    live = sa.and_(jobs.c.verification_status != "closed", jobs.c.listing_quality == "ok", jobs.c.duplicate_of_id.is_(None))
    stale = sa.or_(
        jobs.c.verification_status.in_(("verification_failed", "source_unavailable")),
        jobs.c.last_verified_at < now - timedelta(days=stale_days),
        sa.and_(jobs.c.last_verified_at.is_(None), sa.func.coalesce(jobs.c.last_seen_at, jobs.c.discovered_at) < now - timedelta(days=stale_days)),
    )
    views = {
        "all": sa.true(),
        "recommended": sa.and_(live, status_expr.notin_(("dismissed", "rejected", "withdrawn", "closed"))),
        "needs_review": sa.and_(live, status_expr == "needs_review"),
        "saved": applications.c.saved.is_(True),
        "recent": sa.and_(jobs.c.listing_quality == "ok", jobs.c.discovered_at >= now - timedelta(days=7)),
        "stale": sa.and_(jobs.c.listing_quality == "ok", jobs.c.verification_status != "closed", stale),
        "applied": status_expr.in_(("applied", "recruiter_response", "interview", "offer")),
        "dismissed": status_expr.in_(("dismissed", "rejected", "withdrawn")),
        "closed": status_expr == "closed",
    }
    query = query.where(views.get(f.view, sa.true()))
    if f.q:
        terms = f.q.strip()[:200]
        like = f"%{terms.lower()}%"
        query = query.where(sa.or_(
            jobs.c.search_tsv.op("@@")(sa.func.websearch_to_tsquery("simple", terms)),
            sa.func.lower(jobs.c.title).like(like), sa.func.lower(jobs.c.company).like(like),
            sa.func.array_to_string(jobs.c.skills, " ").ilike(like),
        ))
    if f.status:
        query = query.where(status_expr == f.status)
    if f.min_score is not None:
        query = query.where(jobs.c.fit_score >= f.min_score)
    if f.seniority:
        query = query.where(jobs.c.seniority == f.seniority)
    if f.remote_type:
        query = query.where(jobs.c.remote_type == f.remote_type)
    if f.employment_type:
        query = query.where(jobs.c.employment_type == f.employment_type)
    if f.location:
        query = query.where(jobs.c.location.ilike(f"%{f.location.strip()[:100]}%"))
    if f.source:
        query = query.where(jobs.c.source == f.source)
    if f.posted_within_days:
        query = query.where(jobs.c.posted_at_ts >= now - timedelta(days=f.posted_within_days))
    return query, status_expr


def list_jobs(conn: Connection, owner: str, f: JobFilters, review_threshold: float, stale_days: int) -> dict:
    now = datetime.now(timezone.utc)
    joined = jobs.outerjoin(applications, sa.and_(applications.c.job_id == jobs.c.id, applications.c.owner == owner))
    status_expr = workflow_status_expr(review_threshold)
    query = sa.select(
        jobs.c.id, jobs.c.title, jobs.c.company, jobs.c.location, jobs.c.remote_type, jobs.c.employment_type,
        jobs.c.seniority, jobs.c.source, jobs.c.salary_text, jobs.c.salary_min, jobs.c.salary_max,
        jobs.c.salary_currency, jobs.c.salary_period, jobs.c.posted_at_ts, jobs.c.posted_at_evidence,
        jobs.c.discovered_at, jobs.c.last_verified_at, jobs.c.last_checked_at, jobs.c.verification_status,
        jobs.c.listing_quality, jobs.c.fit_score, jobs.c.priority_score, jobs.c.fit_rationale,
        jobs.c.match_highlights, jobs.c.deadline_at, jobs.c.skills,
        status_expr.label("workflow_status"), sa.func.coalesce(applications.c.saved, False).label("saved"),
        sa.func.count().over().label("_total"),
    ).select_from(joined)
    query, _ = _apply_filters(query, f, owner, review_threshold, stale_days, now)
    page_size = max(1, min(f.page_size, 100))
    page = max(1, f.page)
    rows = conn.execute(
        query.order_by(*SORTS.get(f.sort, SORTS["priority"])).limit(page_size).offset((page - 1) * page_size)
    ).mappings().all()
    total = rows[0]["_total"] if rows else _count(conn, owner, f, review_threshold, stale_days, now) if page > 1 else 0
    items = []
    for r in rows:
        item = {k: v for k, v in r.items() if k != "_total"}
        rationale = item.pop("fit_rationale") or ""
        item["summary"] = (rationale.split(". ")[0].rstrip(".") + ".") if rationale else None
        item["skills"] = (item["skills"] or [])[:8]
        items.append(item)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def _count(conn, owner, f, review_threshold, stale_days, now) -> int:
    joined = jobs.outerjoin(applications, sa.and_(applications.c.job_id == jobs.c.id, applications.c.owner == owner))
    query, _ = _apply_filters(sa.select(sa.func.count()).select_from(joined), f, owner, review_threshold, stale_days, now)
    return conn.execute(query).scalar_one()


def facets(conn: Connection) -> dict:
    def distinct(col):
        return [v for (v,) in conn.execute(sa.select(col).where(col.is_not(None)).distinct().order_by(col)) if v]
    return {
        "sources": distinct(jobs.c.source), "seniority": distinct(jobs.c.seniority),
        "remote_type": distinct(jobs.c.remote_type), "employment_type": distinct(jobs.c.employment_type),
        "statuses": list(lifecycle.STATUSES), "views": list(VIEWS),
    }


def job_detail(conn: Connection, owner: str, job_id: int, review_threshold: float) -> dict | None:
    job = conn.execute(sa.select(jobs).where(jobs.c.id == job_id)).mappings().first()
    if job is None:
        return None
    job = {k: v for k, v in job.items() if k != "search_tsv"}
    app = conn.execute(sa.select(applications).where(applications.c.owner == owner, applications.c.job_id == job_id)).mappings().first()
    app = dict(app) if app else None
    if app:
        app["events"] = [dict(e) for e in conn.execute(
            sa.select(application_events).where(application_events.c.application_id == app["id"]).order_by(application_events.c.at.desc()).limit(50)
        ).mappings()]
        app["tasks"] = [dict(t) for t in conn.execute(
            sa.select(application_tasks).where(application_tasks.c.application_id == app["id"]).order_by(application_tasks.c.due_at.asc().nullslast())
        ).mappings()]
    assessment_row = conn.execute(
        sa.select(job_assessments).where(job_assessments.c.job_id == job_id, job_assessments.c.owner == owner)
        .order_by(job_assessments.c.created_at.desc()).limit(1)
    ).mappings().first()
    drafts = conn.execute(
        sa.select(application_drafts.c.id, application_drafts.c.version, application_drafts.c.origin,
                  application_drafts.c.created_at, application_drafts.c.gmail_status)
        .where(application_drafts.c.owner == owner, application_drafts.c.job_id == job_id)
        .order_by(application_drafts.c.version.desc())
    ).mappings().all()
    feedback = conn.execute(
        sa.select(job_feedback.c.id, job_feedback.c.category, job_feedback.c.note, job_feedback.c.created_at)
        .where(job_feedback.c.owner == owner, job_feedback.c.job_id == job_id).order_by(job_feedback.c.created_at.desc())
    ).mappings().all()
    sources = conn.execute(sa.select(job_sources).where(job_sources.c.job_id == job_id).order_by(job_sources.c.first_seen_at)).mappings().all()
    checks = conn.execute(
        sa.select(job_verifications).where(job_verifications.c.job_id == job_id).order_by(job_verifications.c.checked_at.desc()).limit(10)
    ).mappings().all()
    closed = job["verification_status"] == "closed" or job["listing_quality"] == "not_a_listing"
    implicit = lifecycle.implicit_status(
        float(job["priority_score"]) if job["priority_score"] is not None else (float(job["fit_score"]) if job["fit_score"] is not None else None),
        review_threshold, closed,
    )
    return {
        "job": job,
        "workflow_status": app["status"] if app else implicit,
        "application": app,
        "assessment": dict(assessment_row) if assessment_row else None,
        "drafts": [dict(d) for d in drafts],
        "feedback": [dict(f) for f in feedback],
        "sources": [dict(s) for s in sources],
        "verifications": [dict(c) for c in checks],
    }


def overview(conn: Connection, owner: str, review_threshold: float, high_threshold: float, stale_days: int) -> dict:
    now = datetime.now(timezone.utc)
    joined = jobs.outerjoin(applications, sa.and_(applications.c.job_id == jobs.c.id, applications.c.owner == owner))
    status_expr = workflow_status_expr(review_threshold)
    live = sa.and_(jobs.c.verification_status != "closed", jobs.c.listing_quality == "ok", jobs.c.duplicate_of_id.is_(None))
    last_collect = conn.execute(
        sa.select(runs).where(runs.c.run_type == "collect").order_by(runs.c.started_at.desc()).limit(1)
    ).mappings().first()
    since = last_collect["started_at"] if last_collect else now - timedelta(days=7)
    has_feedback = sa.exists().where(job_feedback.c.job_id == jobs.c.id, job_feedback.c.owner == owner)
    counts = conn.execute(sa.select(
        sa.func.count().filter(sa.and_(jobs.c.discovered_at >= now - timedelta(days=7), jobs.c.listing_quality == "ok")).label("new_this_week"),
        sa.func.count().filter(sa.and_(jobs.c.discovered_at >= since, jobs.c.listing_quality == "ok")).label("new_last_run"),
        sa.func.count().filter(sa.and_(live, status_expr == "needs_review")).label("worth_reviewing"),
        sa.func.count().filter(sa.and_(live, jobs.c.priority_score >= high_threshold,
                                       status_expr.in_(("discovered", "needs_review", "shortlisted")))).label("high_priority"),
        sa.func.count().filter(applications.c.status.in_(tuple(lifecycle.ACTIVE_PIPELINE))).label("in_progress"),
        sa.func.count().filter(sa.and_(applications.c.status.in_(("dismissed", "rejected", "closed")), ~has_feedback)).label("awaiting_feedback"),
        sa.func.count().filter(applications.c.saved.is_(True)).label("saved"),
    ).select_from(joined)).mappings().one()
    interviews = conn.execute(
        sa.select(sa.func.count()).select_from(application_tasks.join(applications, applications.c.id == application_tasks.c.application_id))
        .where(applications.c.owner == owner, application_tasks.c.kind == "interview",
               application_tasks.c.done_at.is_(None), application_tasks.c.due_at >= now)
    ).scalar_one()
    upcoming = conn.execute(
        sa.select(application_tasks.c.id, application_tasks.c.kind, application_tasks.c.title, application_tasks.c.due_at,
                  applications.c.job_id, jobs.c.title.label("job_title"), jobs.c.company)
        .select_from(application_tasks.join(applications, applications.c.id == application_tasks.c.application_id)
                     .join(jobs, jobs.c.id == applications.c.job_id))
        .where(applications.c.owner == owner, application_tasks.c.done_at.is_(None))
        .order_by(application_tasks.c.due_at.asc().nullslast()).limit(5)
    ).mappings().all()
    return {
        **dict(counts), "interviews_scheduled": interviews,
        "upcoming_tasks": [dict(u) for u in upcoming],
        "last_collect": dict(last_collect) if last_collect else None,
        "thresholds": {"review": review_threshold, "high_priority": high_threshold, "stale_days": stale_days},
    }


def application_board(conn: Connection, owner: str) -> list[dict]:
    rows = conn.execute(
        sa.select(applications.c.id, applications.c.job_id, applications.c.status, applications.c.saved,
                  applications.c.applied_at, applications.c.status_changed_at, applications.c.next_follow_up_at,
                  applications.c.notes, jobs.c.title, jobs.c.company, jobs.c.location, jobs.c.fit_score,
                  jobs.c.priority_score, jobs.c.verification_status)
        .select_from(applications.join(jobs, jobs.c.id == applications.c.job_id))
        .where(applications.c.owner == owner, applications.c.status.notin_(("discovered", "needs_review")))
        .order_by(applications.c.status_changed_at.desc())
    ).mappings().all()
    return [dict(r) for r in rows]
