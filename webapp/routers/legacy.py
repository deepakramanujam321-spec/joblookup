"""v1 API (the original dashboard's contract), kept working on the new
data layer for anything still calling it. New clients use /api/v2."""

from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Query

from auth import CurrentUser
from deps import engine
from jobseeker import drafting, lifecycle, runs
from jobseeker.database import jobs
from routers.applications import _initial_status
from schemas import VALID_STATUSES, JobUpdate

router = APIRouter(prefix="/api", tags=["v1 (legacy)"])
V1_COLUMNS = [jobs.c[name] for name in (
    "id", "source", "external_id", "url", "title", "company", "location", "remote_type", "salary_text", "description",
    "posted_at", "discovered_at", "fit_score", "fit_rationale", "outreach_draft", "status", "digest_batch_date",
    "created_at", "updated_at")]


@router.get("/jobs")
def list_jobs(user: CurrentUser, status: str | None = None, source: str | None = None, min_score: float | None = None,
              q: str | None = None, limit: int = Query(default=50, le=200), offset: int = 0):
    query = sa.select(*V1_COLUMNS)
    if status:
        query = query.where(jobs.c.status == status)
    if source:
        query = query.where(jobs.c.source == source)
    if min_score is not None:
        query = query.where(jobs.c.fit_score >= min_score)
    if q:
        like = f"%{q[:200]}%"
        query = query.where(sa.or_(jobs.c.title.ilike(like), jobs.c.company.ilike(like)))
    with engine().begin() as conn:
        rows = conn.execute(query.order_by(jobs.c.fit_score.desc().nullslast(), jobs.c.discovered_at.desc())
                            .limit(limit).offset(offset)).mappings().all()
    return [dict(r) for r in rows]


@router.get("/jobs/{job_id}")
def get_job(job_id: int, user: CurrentUser):
    with engine().begin() as conn:
        row = conn.execute(sa.select(*V1_COLUMNS).where(jobs.c.id == job_id)).mappings().first()
    if row is None:
        raise HTTPException(404, "Job not found")
    return dict(row)


@router.patch("/jobs/{job_id}")
def update_job(job_id: int, update: JobUpdate, user: CurrentUser):
    fields = update.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(400, "Nothing to update")
    if "status" in fields and fields["status"] not in VALID_STATUSES:
        raise HTTPException(400, f"Invalid status. Must be one of {sorted(VALID_STATUSES)}")
    with engine().begin() as conn:
        job = conn.execute(sa.select(jobs).where(jobs.c.id == job_id)).first()
        if job is None:
            raise HTTPException(404, "Job not found")
        conn.execute(sa.update(jobs).where(jobs.c.id == job_id).values(**fields, updated_at=datetime.now(timezone.utc)))
        # Keep the v2 records consistent with what a v1 client just did.
        if fields.get("status") in ("applied", "rejected"):
            target = "applied" if fields["status"] == "applied" else "dismissed"
            try:
                lifecycle.transition(conn, user.account, job_id, target, _initial_status(conn, user.account, job), "via v1 API")
            except lifecycle.TransitionError as e:
                raise HTTPException(409, str(e)) from e
        if fields.get("outreach_draft"):
            drafting.save_version(conn, user.account, job_id, "edited", {"body": fields["outreach_draft"]})
        return dict(conn.execute(sa.select(*V1_COLUMNS).where(jobs.c.id == job_id)).mappings().one())


@router.get("/stats")
def stats(user: CurrentUser):
    with engine().begin() as conn:
        return dict(conn.execute(sa.select(jobs.c.status, sa.func.count()).group_by(jobs.c.status)).all())


@router.get("/runs")
def list_runs(user: CurrentUser, limit: int = Query(default=20, le=100)):
    with engine().begin() as conn:
        return runs.list_runs(conn, limit)
