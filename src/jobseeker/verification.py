"""Listing freshness: is this posting still open, and how do we know?

Outcomes (stored per check in job_verifications, summarised on jobs):
  active               the source served the posting
  closed               the source itself says it's gone (API 404, removed
                       from an ATS board that loaded fine, HTTP 404/410,
                       an explicit "no longer accepting applications")
  source_unavailable   the source refused us (401/403/429)
  verification_failed  network error / 5xx / unparseable -- proves nothing

Only `closed` changes a job's status to closed. Failures increment a
counter; the job keeps its last known status until FAILURES_BEFORE_FLAG
consecutive failures, so one bad network minute never expires anything.

enrich_job() also lives here because it's the same ATS API call: for an
ATS posting, fetching full structured data *is* a verification.
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests
import sqlalchemy as sa
from sqlalchemy.engine import Connection

from . import ats, ingest, normalize
from .database import applications, job_verifications, jobs
from .models import JobListing

FAILURES_BEFORE_FLAG = 3
PAGE_CHECK_SOURCES = {"remoteok", "weworkremotely"}
NO_AUTOMATED_CHECK = {"linkedin", "indeed"}  # anti-automation terms; never fetched for verification
CLOSED_MARKERS = re.compile(
    r"(no longer (accepting applications|available|open)|this (job|position) (has )?(expired|been filled|is closed)|"
    r"job (is )?no longer available|position (has been )?filled)",
    re.I,
)
TRACKED_STATUSES = ("shortlisted", "draft_ready", "applied", "recruiter_response", "interview", "offer")
USER_AGENT = "Mozilla/5.0 (compatible; joblookup/2.0; personal job-search assistant)"


@dataclass
class Check:
    result: str
    method: str
    http_status: int | None = None
    detail: str | None = None
    ats_job: ats.AtsJob | None = None


def check_page(url: str) -> Check:
    method = "http_page"
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15, allow_redirects=True)
    except requests.RequestException as e:
        return Check("verification_failed", method, None, f"{type(e).__name__}")
    if resp.status_code in (404, 410):
        return Check("closed", method, resp.status_code, f"HTTP {resp.status_code}")
    if resp.status_code in (401, 403, 429):
        return Check("source_unavailable", method, resp.status_code, f"HTTP {resp.status_code}")
    if resp.status_code >= 400:
        return Check("verification_failed", method, resp.status_code, f"HTTP {resp.status_code}")
    original, final = urlparse(url), urlparse(resp.url)
    if original.path.strip("/") and not final.path.strip("/"):
        return Check("closed", method, resp.status_code, "Posting URL now redirects to the site's home page")
    if CLOSED_MARKERS.search(resp.text[:200_000]):
        return Check("closed", method, resp.status_code, "Page says the posting is no longer open")
    return Check("active", method, resp.status_code, None)


def check_job(job: dict, ashby: ats.AshbyBoardCache) -> Check | None:
    """None = this source isn't checked automatically."""
    ref = ats.parse_ats_url(job["url"])
    if ref and ref.job_id:
        result = ats.fetch_posting(ref, ashby)
        if result.job:
            return Check("active", result.method, result.http_status, None, result.job)
        if result.closed:
            return Check("closed", result.method, result.http_status, "Removed from the employer's job board")
        status = "source_unavailable" if result.http_status in (401, 403, 429) else "verification_failed"
        return Check(status, result.method, result.http_status, result.error)
    if job["source"] in PAGE_CHECK_SOURCES:
        return check_page(job["url"])
    return None


def apply_check(conn: Connection, job: dict, check: Check, now: datetime) -> str:
    """Records the check and updates the job's summary fields. Returns the
    job's resulting verification_status."""
    conn.execute(
        sa.insert(job_verifications).values(
            job_id=job["id"], checked_at=now, result=check.result, method=check.method,
            http_status=check.http_status, detail=(check.detail or "")[:500] or None,
        )
    )
    values: dict = {"last_checked_at": now, "updated_at": now}
    if check.result == "active":
        values.update(verification_status="active", last_verified_at=now, verification_failures=0, closed_at=None)
    elif check.result == "closed":
        values.update(verification_status="closed", closed_at=job.get("closed_at") or now, verification_failures=0)
    else:
        failures = (job.get("verification_failures") or 0) + 1
        values["verification_failures"] = failures
        if failures >= FAILURES_BEFORE_FLAG or job.get("verification_status") in (None, "unchecked"):
            values["verification_status"] = check.result
    conn.execute(sa.update(jobs).where(jobs.c.id == job["id"]).values(**values))
    return values.get("verification_status", job.get("verification_status"))


def apply_ats_data(conn: Connection, job: dict, ats_job: ats.AtsJob, now: datetime) -> None:
    """Official API data supersedes scraped data for the fields it covers;
    company is only filled when it was unknown."""
    listing = ats.to_listing(ats_job, job.get("company") or "")
    fields = ingest.normalized_fields(listing)
    keep_if_known = {"company"}
    updates = {}
    for key, value in fields.items():
        if key == "external_id" and job.get("external_id"):
            continue
        if key in keep_if_known and job.get(key):
            continue
        if value not in (None, "", []):
            updates[key] = value
    updates["description_is_partial"] = False
    updates.update(enrichment_version=ingest.ENRICHMENT_VERSION, enriched_at=now, updated_at=now)
    conn.execute(sa.update(jobs).where(jobs.c.id == job["id"]).values(**updates))


def renormalize(conn: Connection, job: dict, now: datetime) -> None:
    """Non-ATS rows: recompute derived fields from what's stored."""
    listing = JobListing.from_dict({
        "source": job["source"], "url": job["url"], "title": job["title"], "company": job["company"],
        "location": job.get("location") or "", "remote_type": job.get("remote_type") or "",
        "salary_text": job.get("salary_text") or "", "description": job.get("description") or "",
        "external_id": job.get("external_id") or "", "posted_at": job.get("posted_at") or "",
        "description_html": job.get("description_html") or "",
        "description_source": job.get("description_source") or ("page_text" if job["source"] in ("greenhouse", "lever", "ashby", "linkedin", "indeed") else f"{job['source']}_feed"),
        "description_is_partial": job.get("description_is_partial", True),
        "posted_at_evidence": job.get("posted_at_evidence") or "",
    })
    fields = ingest.normalized_fields(listing)
    if job["source"] not in ("remoteok", "weworkremotely") and not job.get("posted_at_evidence"):
        fields["posted_at_ts"] = None  # page-text sources never yield a trustworthy date
        fields["posted_at_evidence"] = None
    elif job["source"] == "remoteok" and fields["posted_at_ts"]:
        fields["posted_at_evidence"] = fields["posted_at_evidence"] or "remoteok_api.date"
    elif job["source"] == "weworkremotely" and fields["posted_at_ts"]:
        fields["posted_at_evidence"] = "wwr_rss.pubDate"
    quality = "ok" if normalize.is_specific_listing_url(job["url"], job["source"]) else "not_a_listing"
    fields.pop("company")
    conn.execute(
        sa.update(jobs).where(jobs.c.id == job["id"]).values(
            **fields, listing_quality=quality, enrichment_version=ingest.ENRICHMENT_VERSION, enriched_at=now, updated_at=now,
        )
    )


def enrich_pending(engine, limit: int = 200, delay: float = 0.25) -> dict:
    stats = {"processed": 0, "api_enriched": 0, "closed": 0, "not_a_listing": 0, "failed": 0}
    ashby = ats.AshbyBoardCache()
    with engine.connect() as conn:
        pending = conn.execute(
            sa.select(jobs).where(jobs.c.enrichment_version < ingest.ENRICHMENT_VERSION).order_by(jobs.c.id).limit(limit)
        ).mappings().all()
    for job in pending:
        job = dict(job)
        now = datetime.now(timezone.utc)
        with engine.begin() as conn:
            try:
                if not normalize.is_specific_listing_url(job["url"], job["source"]):
                    renormalize(conn, job, now)
                    stats["not_a_listing"] += 1
                    continue
                check = None
                ref = ats.parse_ats_url(job["url"])
                if ref and ref.job_id:
                    check = check_job(job, ashby)
                    time.sleep(delay)
                if check and check.ats_job:
                    apply_ats_data(conn, job, check.ats_job, now)
                    stats["api_enriched"] += 1
                else:
                    renormalize(conn, job, now)
                if check:
                    if apply_check(conn, job, check, now) == "closed":
                        stats["closed"] += 1
            except Exception as e:  # one bad row never blocks the rest
                print(f"[enrich] job {job['id']} failed: {e}", file=sys.stderr)
                stats["failed"] += 1
                continue
            finally:
                stats["processed"] += 1
    return stats


def verify_due(engine, limit: int = 150, tracked_hours: int = 24, other_hours: int = 72, delay: float = 0.3) -> dict:
    """Re-checks listings oldest-first, tracked applications first."""
    now = datetime.now(timezone.utc)
    tracked = sa.exists().where(applications.c.job_id == jobs.c.id, sa.or_(
        applications.c.status.in_(TRACKED_STATUSES), applications.c.saved.is_(True)))
    due = sa.or_(
        jobs.c.last_checked_at.is_(None),
        sa.and_(tracked, jobs.c.last_checked_at < now - timedelta(hours=tracked_hours)),
        jobs.c.last_checked_at < now - timedelta(hours=other_hours),
    )
    with engine.connect() as conn:
        candidates = conn.execute(
            sa.select(jobs).where(
                jobs.c.listing_quality == "ok", jobs.c.verification_status != "closed",
                jobs.c.source.notin_(NO_AUTOMATED_CHECK), due,
            ).order_by(tracked.desc(), jobs.c.last_checked_at.asc().nullsfirst()).limit(limit)
        ).mappings().all()
    stats = {"checked": 0, "active": 0, "closed": 0, "source_unavailable": 0, "verification_failed": 0}
    ashby = ats.AshbyBoardCache()
    for job in candidates:
        job = dict(job)
        check = check_job(job, ashby)
        if check is None:
            continue
        with engine.begin() as conn:
            if check.ats_job:
                apply_ats_data(conn, job, check.ats_job, datetime.now(timezone.utc))
            apply_check(conn, job, check, datetime.now(timezone.utc))
        stats["checked"] += 1
        stats[check.result] += 1
        time.sleep(delay)
    return stats
