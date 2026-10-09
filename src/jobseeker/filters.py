"""Deterministic pre-filters applied before anything reaches the database.

Keep these conservative: a false negative here silently hides a job from the
candidate forever, while a false positive just costs the agent one extra
scoring call. When in doubt, let it through.
"""

from __future__ import annotations


from . import normalize
from .models import JobListing


def is_excluded_company(company: str, excluded: list[str]) -> bool:
    company_lower = company.lower()
    return any(ex.lower() in company_lower for ex in excluded if ex)


def is_staffing_agency(company: str, markers: list[str]) -> bool:
    company_lower = company.lower()
    return any(m.lower() in company_lower for m in markers)


def matches_keywords(listing: JobListing, keywords_any: list[str]) -> bool:
    haystack = f"{listing.title} {listing.description}".lower()
    return any(k.lower() in haystack for k in keywords_any)


def meets_salary_floor(salary_text: str, floor_lpa: float) -> bool:
    """Only filters out postings that explicitly state a lower figure in
    rupees ("12 LPA", "₹8-12 lakh", "₹12,00,000 per annum"). No stated
    salary, another currency, or anything unparseable passes through --
    scoring handles those (and caps fit for below-minimum pay it can judge)."""
    if not salary_text or not floor_lpa:
        return True
    salary = normalize.parse_salary(salary_text)
    if not salary or salary["currency"] != "INR":
        return True
    return normalize.annual_amount(salary["max"], salary["period"]) >= floor_lpa * 100_000


def apply_filters(listings: list[JobListing], profile: dict) -> list[JobListing]:
    exclusions = profile["exclusions"]
    role_focus = profile["role_focus"]
    salary_floor = profile.get("salary_floor_lpa", 0)

    kept = []
    for listing in listings:
        if not listing.title or not listing.url:
            continue
        if is_excluded_company(listing.company, exclusions.get("companies", [])):
            continue
        if exclusions.get("exclude_staffing_agencies") and is_staffing_agency(
            listing.company, exclusions.get("staffing_agency_markers", [])
        ):
            continue
        if not matches_keywords(listing, role_focus.get("keywords_any", [])):
            continue
        if not meets_salary_floor(listing.salary_text, salary_floor):
            continue
        kept.append(listing)

    return kept


def dedupe_by_url(listings: list[JobListing]) -> list[JobListing]:
    seen = set()
    out = []
    for listing in listings:
        if listing.url in seen:
            continue
        seen.add(listing.url)
        out.append(listing)
    return out
