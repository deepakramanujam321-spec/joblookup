"""Pipeline health and run history."""

from __future__ import annotations

from fastapi import APIRouter, Query

from auth import CurrentUser
from deps import engine
from jobseeker import runs

router = APIRouter(prefix="/api/v2", tags=["pipeline"])


@router.get("/pipeline")
def pipeline_health(user: CurrentUser, limit: int = Query(default=30, le=100)):
    with engine().begin() as conn:
        return {**runs.health(conn), "recent_runs": runs.list_runs(conn, limit)}
