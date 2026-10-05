"""Dashboard backend: a thin read/write API in front of jobseeker.jobs and
jobseeker.runs, plus the static frontend. The Supabase service-role key
lives only here, server-side — the browser never sees it, unlike the
client-side-Supabase approach this deliberately avoids.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles

from jobseeker import config
from jobseeker.storage import SupabaseStore

from auth import require_auth
from schemas import VALID_STATUSES, JobUpdate

app = FastAPI(title="joblookup dashboard")


def get_store() -> SupabaseStore:
    url = config.require_env("SUPABASE_URL")
    key = config.require_env("SUPABASE_SERVICE_ROLE_KEY")
    return SupabaseStore(url, key)


@app.get("/api/jobs")
def list_jobs(
    _user: Annotated[str, Depends(require_auth)],
    status: str | None = None,
    source: str | None = None,
    min_score: float | None = None,
    q: str | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
):
    return get_store().query_jobs(
        status=status, source=source, min_score=min_score, search=q, limit=limit, offset=offset
    )


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int, _user: Annotated[str, Depends(require_auth)]):
    job = get_store().get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.patch("/api/jobs/{job_id}")
def update_job(job_id: int, update: JobUpdate, _user: Annotated[str, Depends(require_auth)]):
    store = get_store()
    if get_store().get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")

    fields = update.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="Nothing to update")
    if "status" in fields and fields["status"] not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of {sorted(VALID_STATUSES)}")

    store.update_job(job_id, fields)
    return store.get_job(job_id)


@app.get("/api/stats")
def stats(_user: Annotated[str, Depends(require_auth)]):
    return get_store().stats()


@app.get("/api/runs")
def list_runs(_user: Annotated[str, Depends(require_auth)], limit: int = Query(default=20, le=100)):
    return get_store().list_runs(limit=limit)


@app.get("/api/health")
def health():
    """Unauthenticated — just confirms the server is up, not that Supabase is."""
    return {"ok": True}


app.mount("/", StaticFiles(directory=Path(__file__).resolve().parent / "static", html=True), name="static")
