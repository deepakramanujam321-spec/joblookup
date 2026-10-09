"""Job discovery: overview metrics, filtered/paginated list, detail."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from auth import CurrentUser
from deps import engine, limiter, thresholds
from jobseeker import ats, preferences, queries, verification
from jobseeker.queries import JobFilters

router = APIRouter(prefix="/api/v2", tags=["jobs"])


@router.get("/overview")
def overview(user: CurrentUser):
    with engine().begin() as conn:
        t = thresholds(conn, user.account)
        return queries.overview(conn, user.account, t["review"], t["high_priority"], t["stale_days"])


@router.get("/jobs")
def list_jobs(
    user: CurrentUser,
    q: str | None = Query(default=None, max_length=200),
    view: Literal[queries.VIEWS] = "recommended",  # type: ignore[valid-type]
    status: str | None = None,
    min_score: float | None = Query(default=None, ge=0, le=100),
    seniority: str | None = None,
    remote_type: str | None = None,
    employment_type: str | None = None,
    location: str | None = Query(default=None, max_length=100),
    source: str | None = None,
    posted_within_days: int | None = Query(default=None, ge=1, le=365),
    sort: Literal[tuple(queries.SORTS)] = "priority",  # type: ignore[valid-type]
    page: int = Query(default=1, ge=1, le=10_000),
    page_size: int = Query(default=25, ge=1, le=100),
):
    filters = JobFilters(q=q, view=view, status=status, min_score=min_score, seniority=seniority,
                         remote_type=remote_type, employment_type=employment_type, location=location,
                         source=source, posted_within_days=posted_within_days, sort=sort, page=page,
                         page_size=page_size)
    with engine().begin() as conn:
        t = thresholds(conn, user.account)
        return queries.list_jobs(conn, user.account, filters, t["review"], t["stale_days"])


@router.get("/jobs/facets")
def facets(user: CurrentUser):
    with engine().begin() as conn:
        return queries.facets(conn)


@router.get("/jobs/{job_id}")
def job_detail(job_id: int, user: CurrentUser):
    with engine().begin() as conn:
        t = thresholds(conn, user.account)
        detail = queries.job_detail(conn, user.account, job_id, t["review"])
    if detail is None:
        raise HTTPException(404, "Job not found")
    detail["thresholds"] = t
    return detail


@router.post("/jobs/{job_id}/verify")
def verify_now(job_id: int, user: CurrentUser):
    """Live check against the source, on demand. Only fetches the stored,
    already-known posting URL for supported sources (official ATS APIs,
    RemoteOK/WWR pages) -- never an arbitrary user-supplied URL."""
    limiter.check(user.account, "verification", 20)
    with engine().begin() as conn:
        detail = queries.job_detail(conn, user.account, job_id, 0)
    if detail is None:
        raise HTTPException(404, "Job not found")
    job = detail["job"]
    check = verification.check_job(job, ats.AshbyBoardCache())
    if check is None:
        raise HTTPException(422, "This listing can't be checked automatically (no official API for its URL, or its source forbids automated access). Open the original posting to confirm.")
    now = datetime.now(timezone.utc)
    with engine().begin() as conn:
        if check.ats_job:
            verification.apply_ats_data(conn, job, check.ats_job, now)
        result = verification.apply_check(conn, job, check, now)
        preferences.rerank(conn, user.account)
    return {"result": check.result, "verification_status": result, "detail": check.detail, "checked_at": now}
