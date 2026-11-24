"""Each person's job list is their own; a job found for someone else only
reaches you when it's a strong match for *you*.

Collection runs per person from their own profile. After it, every listing
found for one person is checked (deterministically -- no AI cost) against
each other person's profile, and only the strong matches are copied into
that person's list, where they are scored and shown like any other job.
"""

from __future__ import annotations

from sqlalchemy.engine import Connection

from . import ingest, matching, profile as profile_mod
from .models import JobListing

# A listing is offered to another person only if their profile says it's a
# strong fit on the facts alone (no AI read): a title/role they target, a
# location they accept, nothing that rules it out, and a high overall score.
SHARE_MIN_FIT = 80.0
SHARE_MIN_ROLE = 80  # title match (100) or a role family they target (80)


def _job_view(listing: JobListing) -> dict:
    fields = ingest.normalized_fields(listing)
    return {**fields, "source": listing.source, "url": listing.url, "company": fields["company"] or ""}


def high_matches_for(conn: Connection, owner: str, listings: list[JobListing]) -> list[JobListing]:
    """The listings among `listings` that are a strong match for `owner`."""
    record = profile_mod.get_or_seed(conn, owner)
    profile = record["data"]
    if not profile.get("preferred_titles"):
        return []  # nothing to judge a match against yet
    _, resume = profile_mod.default_resume_text(conn, owner)
    strong = []
    for listing in listings:
        job = _job_view(listing)
        if matching.hard_exclude(job, profile) or not matching.location_compatible(job, profile):
            continue
        result = matching.assess(job, profile, resume, None)
        role = result["components"]["role"]["score"]
        if result["overall_score"] >= SHARE_MIN_FIT and role is not None and role >= SHARE_MIN_ROLE:
            strong.append(listing)
    return strong


def share_high_matches(conn: Connection, found: dict[str, list[JobListing]]) -> dict[str, int]:
    """`found` maps each owner to the listings collected for them. Copies each
    owner's strong matches into every *other* owner's list; returns how many
    new jobs each owner received."""
    received: dict[str, int] = {}
    for target in profile_mod.list_owners(conn):
        offered = [listing for source_owner, listings in found.items() if source_owner != target for listing in listings]
        if not offered:
            continue
        strong = high_matches_for(conn, target, offered)
        if strong:
            received[target] = ingest.upsert_listings(conn, strong, owner=target).new
    return received
