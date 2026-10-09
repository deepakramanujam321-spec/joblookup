"""Normalizes collected listings and stores them with deduplication.

Identity, strongest evidence first:
  1. exact source URL already recorded (job_sources.url)
  2. canonical URL match
  3. same source + same external listing id
  4. cross-source duplicate: same normalized company + title + location
     from a *different* source (dedupe_key). Two postings from the same
     source are never merged this way -- a company can genuinely list two
     identical-looking vacancies.
A match on 1-3 is the same posting seen again (last_seen_at moves). A
match on 4 adds a provenance row (job_sources) to the existing job instead
of creating a second job, so the original source is never lost.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from . import normalize
from .database import job_sources, jobs
from .models import JobListing

ENRICHMENT_VERSION = 2  # bump when normalization logic changes; run_enrich.py re-processes older rows
MAX_DESCRIPTION_CHARS = 50_000
API_SOURCES = {"greenhouse_api", "lever_api", "ashby_api"}


@dataclass
class IngestStats:
    found: int = 0
    new: int = 0
    seen_again: int = 0
    merged_duplicates: int = 0
    rejected: int = 0
    new_ids: list[int] = field(default_factory=list)
    rejected_reasons: dict[str, int] = field(default_factory=dict)

    def reject(self, reason: str) -> None:
        self.rejected += 1
        self.rejected_reasons[reason] = self.rejected_reasons.get(reason, 0) + 1


def normalized_fields(listing: JobListing) -> dict:
    """Everything derivable from a listing, with provenance for dates and
    salary. Returns only columns that have a value, so callers can merge
    without overwriting known data with unknowns."""
    description = (listing.description or "")[:MAX_DESCRIPTION_CHARS]
    title = (listing.title or "").strip()[:300]
    text_for_skills = f"{title}\n{description}"

    posted_ts = None
    evidence = listing.posted_at_evidence or None
    if listing.posted_at:
        posted_ts = normalize.parse_datetime(listing.posted_at)
        if posted_ts and not evidence:
            evidence = f"{listing.source}.posted_at"
        if posted_ts is None:
            evidence = None

    salary_source = None
    salary = None
    if listing.salary_min is not None:
        salary = {"min": listing.salary_min, "max": listing.salary_max or listing.salary_min,
                  "currency": listing.salary_currency or None, "period": listing.salary_period or "year"}
        salary_source = "source_structured"
    else:
        salary = normalize.parse_salary(listing.salary_text)
        salary_source = "source_text" if salary else None

    years, years_text = normalize.extract_experience(description)
    employment = listing.employment_type or normalize.normalize_employment_type(description[:1500]) or None
    location = (listing.location or "").strip()[:300]

    fields = {
        "title": title,
        "company": normalize.normalize_company(listing.company),
        "canonical_url": normalize.canonical_url(listing.url),
        "location": location or None,
        "remote_type": listing.remote_type or normalize.classify_remote(location, None, description) or None,
        "employment_type": employment,
        "seniority": normalize.detect_seniority(title),
        "job_category": normalize.detect_category(title),
        "experience_min_years": years,
        "experience_text": years_text,
        "skills": normalize.extract_skills(text_for_skills),
        "salary_text": listing.salary_text or None,
        "salary_min": salary["min"] if salary else None,
        "salary_max": salary["max"] if salary else None,
        "salary_currency": salary["currency"] if salary else None,
        "salary_period": salary["period"] if salary else None,
        "salary_source": salary_source,
        "description": description or None,
        "description_html": listing.description_html or None,
        "description_source": listing.description_source or None,
        "description_is_partial": listing.description_is_partial,
        "posted_at": listing.posted_at or None,
        "posted_at_ts": posted_ts,
        "posted_at_evidence": evidence,
        "source_updated_at": normalize.parse_datetime(listing.source_updated_at),
        "deadline_at": normalize.parse_datetime(listing.deadline_at),
        "external_id": listing.external_id or None,
        "dedupe_key": normalize.dedupe_key(listing.company, title, location),
    }
    return fields


def _find_existing(conn: Connection, listing: JobListing, fields: dict):
    row = conn.execute(
        sa.select(jobs.c.id, jobs.c.source).join(job_sources, job_sources.c.job_id == jobs.c.id)
        .where(job_sources.c.url.in_({listing.url, fields["canonical_url"]}))
    ).first()
    if row:
        return row, "seen"
    row = conn.execute(sa.select(jobs.c.id, jobs.c.source).where(jobs.c.canonical_url == fields["canonical_url"])).first()
    if row:
        return row, "seen"
    if listing.external_id:
        row = conn.execute(
            sa.select(jobs.c.id, jobs.c.source).where(jobs.c.source == listing.source, jobs.c.external_id == listing.external_id)
        ).first()
        if row:
            return row, "seen"
    company_part, title_part, _ = fields["dedupe_key"].split("|")
    if company_part and title_part:
        row = conn.execute(
            sa.select(jobs.c.id, jobs.c.source).where(
                jobs.c.dedupe_key == fields["dedupe_key"], jobs.c.source != listing.source,
                jobs.c.verification_status != "closed", jobs.c.duplicate_of_id.is_(None),
            ).order_by(jobs.c.id).limit(1)
        ).first()
        if row:
            return row, "merged"
    return None, None


def _fill_missing(conn: Connection, job_id: int, fields: dict, now: datetime) -> None:
    """A re-sighting only fills columns that are still unknown; it never
    replaces data already established (often from a better source)."""
    current = conn.execute(sa.select(jobs).where(jobs.c.id == job_id)).mappings().one()
    updates = {"last_seen_at": now}
    for key, value in fields.items():
        if key in ("dedupe_key", "canonical_url", "description_is_partial"):
            continue
        if value not in (None, "", []) and current.get(key) in (None, "", []):
            updates[key] = value
    if not fields["description_is_partial"] and current["description_is_partial"] and fields.get("description"):
        updates.update(
            description=fields["description"], description_html=fields["description_html"],
            description_source=fields["description_source"], description_is_partial=False,
        )
    conn.execute(sa.update(jobs).where(jobs.c.id == job_id).values(**updates))


def upsert_listings(conn: Connection, listings: list[JobListing], now: datetime | None = None) -> IngestStats:
    now = now or datetime.now(timezone.utc)
    stats = IngestStats(found=len(listings))
    for listing in listings:
        if not listing.url or not (listing.title or "").strip():
            stats.reject("missing title or URL")
            continue
        if not normalize.is_specific_listing_url(listing.url, listing.source):
            stats.reject("search/category page, not a single posting")
            continue
        fields = normalized_fields(listing)
        existing, kind = _find_existing(conn, listing, fields)
        if existing is not None:
            if kind == "merged":
                stats.merged_duplicates += 1
            else:
                stats.seen_again += 1
            _fill_missing(conn, existing.id, fields, now)
            _record_source(conn, existing.id, listing, now)
            continue

        enriched = fields["description_source"] in API_SOURCES or fields["description_source"] == "jsonld"
        job_id = conn.execute(
            sa.insert(jobs).values(
                source=listing.source, url=listing.url, status="new", discovered_at=now, last_seen_at=now,
                enrichment_version=ENRICHMENT_VERSION if enriched else 0,
                enriched_at=now if enriched else None,
                **{k: v for k, v in fields.items() if k not in ("company",)},
                company=fields["company"] or "",
            ).returning(jobs.c.id)
        ).scalar_one()
        _record_source(conn, job_id, listing, now)
        stats.new += 1
        stats.new_ids.append(job_id)
    return stats


def _record_source(conn: Connection, job_id: int, listing: JobListing, now: datetime) -> None:
    # Canonical form, so tracking-parameter variants of one URL don't
    # multiply provenance rows.
    stmt = pg_insert(job_sources).values(
        job_id=job_id, source=listing.source, url=normalize.canonical_url(listing.url) or listing.url, external_id=listing.external_id or None,
        first_seen_at=now, last_seen_at=now,
    )
    conn.execute(stmt.on_conflict_do_update(index_elements=["url"], set_={"last_seen_at": now}))
