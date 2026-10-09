"""Pipeline run records and the health summary derived from them.

Health is computed from facts, never assumed: a source that errored makes
the run "partial", an exception makes it "failed", and a pipeline whose
last successful collect is older than its schedule allows is "stale" even
if that last run itself was fine.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from .database import jobs, runs

COLLECT_STALE_AFTER = timedelta(days=4)  # weekday schedule: Fri -> Mon is 3 days


class RunRecorder:
    def __init__(self, run_type: str):
        self.run_type = run_type
        self.started = datetime.now(timezone.utc)
        self._t0 = time.monotonic()
        self.values: dict = {"jobs_found": 0, "jobs_new": 0, "jobs_duplicate": 0, "jobs_rejected": 0, "jobs_updated": 0}
        self.source_stats: dict = {}
        self.details: dict = {}
        self.errors: list[str] = []

    def source(self, name: str, found: int, error: str | None = None) -> None:
        self.source_stats[name] = {"found": found, "error": error}
        if error:
            self.errors.append(f"{name}: {error}")

    def status(self, failed: bool) -> str:
        if failed:
            return "failed"
        if any(s.get("error") for s in self.source_stats.values()) or self.errors:
            return "partial"
        return "ok"


@contextmanager
def recorded(engine: Engine, run_type: str):
    """Always writes a run row -- including when the body raises, which is
    exactly when the dashboard most needs to show it."""
    rec = RunRecorder(run_type)
    failed = None
    try:
        yield rec
    except Exception as e:
        failed = e
        rec.errors.append(f"{type(e).__name__}: {e}")
        raise
    finally:
        with engine.begin() as conn:
            conn.execute(sa.insert(runs).values(
                run_type=run_type, started_at=rec.started, finished_at=datetime.now(timezone.utc),
                status=rec.status(failed is not None), duration_ms=int((time.monotonic() - rec._t0) * 1000),
                source_stats=rec.source_stats or None, details=rec.details or None,
                error="; ".join(rec.errors)[:4000] or None, **rec.values,
            ))


def list_runs(conn, limit: int = 30) -> list[dict]:
    rows = conn.execute(sa.select(runs).order_by(runs.c.started_at.desc()).limit(limit)).mappings().all()
    out = []
    for r in rows:
        r = dict(r)
        if r["status"] is None:  # v1 rows predate the status column
            r["status"] = "failed" if r["error"] else "ok"
        out.append(r)
    return out


def health(conn, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    recent = list_runs(conn, 50)
    by_type: dict[str, dict] = {}
    for run_type in ("collect", "verify", "digest", "enrich"):
        of_type = [r for r in recent if r["run_type"] == run_type]
        last = of_type[0] if of_type else None
        last_ok = next((r for r in of_type if r["status"] in ("ok", "partial")), None)
        by_type[run_type] = {"last": last, "last_success_at": last_ok["finished_at"] if last_ok else None}

    collect = by_type["collect"]
    if collect["last"] is None:
        state, message = "unknown", "No collection run recorded yet."
    elif collect["last"]["status"] == "failed":
        state, message = "failing", f"Last collection failed: {(collect['last']['error'] or '')[:200]}"
    elif collect["last_success_at"] and now - collect["last_success_at"] > COLLECT_STALE_AFTER:
        state, message = "stale", "No successful collection in over 4 days."
    elif collect["last"]["status"] == "partial":
        state, message = "degraded", "Last collection succeeded, but some sources failed."
    else:
        state, message = "healthy", "Collection is running on schedule."

    counts = conn.execute(sa.select(
        sa.func.count().label("total"),
        sa.func.count().filter(jobs.c.verification_status == "active").label("active"),
        sa.func.count().filter(jobs.c.verification_status == "closed").label("closed"),
        sa.func.count().filter(sa.or_(
            jobs.c.verification_status.in_(("verification_failed", "source_unavailable")),
            jobs.c.last_verified_at < now - timedelta(days=14),
        )).label("stale"),
        sa.func.count().filter(jobs.c.verification_status == "unchecked").label("unchecked"),
        sa.func.count().filter(jobs.c.listing_quality == "not_a_listing").label("not_a_listing"),
    )).mappings().one()
    return {"state": state, "message": message, "runs": by_type, "listings": dict(counts)}
