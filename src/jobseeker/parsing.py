"""Turns a fetched page (or an API payload) into a JobListing.

Deliberately generic rather than pinned to one exact DOM shape: ATS board
markup drifts over time and breaking silently on a selector change is worse
than extracting slightly-loose text. We only need enough signal for the
keyword filter — the agent reads the full description later for real
scoring, so over-extraction here is cheap and under-extraction is costly.
"""

from __future__ import annotations

import json
import re
import sys
from urllib.parse import urlparse

from . import normalize
from .models import JobListing

MAX_DESCRIPTION_CHARS = 4000

LOCATION_HINTS = [
    "remote", "hybrid", "on-site", "onsite", "bengaluru", "bangalore",
    "hyderabad", "mumbai", "pune", "delhi", "ncr", "chennai", "india",
]


def company_from_url(url: str, source: str) -> str:
    """ATS board URLs encode the company as the first path segment."""
    path_parts = [p for p in urlparse(url).path.split("/") if p]
    if source in ("greenhouse", "lever", "ashby") and path_parts:
        return path_parts[0].replace("-", " ").title()
    return ""


def guess_location(text: str) -> str:
    lowered = text.lower()
    hits = [h for h in LOCATION_HINTS if h in lowered]
    return ", ".join(dict.fromkeys(hits))[:120]  # dedupe, preserve order


def guess_remote_type(location_text: str) -> str:
    lowered = location_text.lower()
    if "remote" in lowered:
        return "remote"
    if "hybrid" in lowered:
        return "hybrid"
    if any(h in lowered for h in LOCATION_HINTS if h not in ("remote", "hybrid")):
        return "onsite"
    return ""


def _iter_jsonld(blocks: list[str]):
    for raw in blocks:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                if "@graph" in item:
                    stack.extend(item["@graph"] if isinstance(item["@graph"], list) else [item["@graph"]])
                yield item


def extract_jobposting_jsonld(blocks: list[str]) -> dict | None:
    """schema.org JobPosting, which most career sites embed for search
    engines: the employer's own structured statement of title, dates,
    location, pay and description."""
    for item in _iter_jsonld(blocks):
        kind = item.get("@type")
        if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
            return item
    return None


def listing_from_jsonld(item: dict, url: str, source: str) -> JobListing | None:
    title = (item.get("title") or "").strip()
    if not title:
        return None
    org = item.get("hiringOrganization") or {}
    company = org.get("name", "") if isinstance(org, dict) else str(org)
    locations = item.get("jobLocation") or []
    locations = locations if isinstance(locations, list) else [locations]
    place_names = []
    for loc in locations:
        address = (loc or {}).get("address", {}) if isinstance(loc, dict) else {}
        if isinstance(address, dict):
            parts = [address.get("addressLocality"), address.get("addressRegion"), address.get("addressCountry")]
            parts = [p if isinstance(p, str) else (p or {}).get("name") for p in parts]
            place_names.append(", ".join(p for p in parts if p))
    location = "; ".join(p for p in place_names if p)
    remote = "remote" if item.get("jobLocationType") == "TELECOMMUTE" else normalize.classify_remote(location)
    salary = item.get("baseSalary") or {}
    value = salary.get("value") if isinstance(salary, dict) else None
    salary_min = salary_max = None
    period = ""
    if isinstance(value, dict):
        salary_min = value.get("minValue") or value.get("value")
        salary_max = value.get("maxValue") or salary_min
        unit = (value.get("unitText") or "YEAR").upper()
        period = "hour" if unit == "HOUR" else "month" if unit == "MONTH" else "year"
    employment = item.get("employmentType")
    employment = employment[0] if isinstance(employment, list) and employment else employment
    description_html = normalize.sanitize_html(item.get("description")) or ""
    return JobListing(
        source=source,
        url=url,
        title=title[:200],
        company=company,
        location=location,
        remote_type=remote or "",
        description=normalize.html_to_text(description_html),
        external_id=str((item.get("identifier") or {}).get("value", "")) if isinstance(item.get("identifier"), dict) else "",
        posted_at=item.get("datePosted", "") or "",
        description_html=description_html,
        description_source="jsonld",
        description_is_partial=False,
        posted_at_evidence="jsonld.datePosted" if item.get("datePosted") else "",
        deadline_at=item.get("validThrough", "") or "",
        employment_type=normalize.normalize_employment_type(employment) or "",
        salary_min=float(salary_min) if salary_min else None,
        salary_max=float(salary_max) if salary_max else None,
        salary_currency=salary.get("currency", "") if isinstance(salary, dict) else "",
        salary_period=period if salary_min else "",
    )


def parse_fetched_page(page, url: str, source: str) -> JobListing | None:
    """`page` is a Scrapling page object (see scrapling_fetch.py)."""
    if page is None:
        return None

    try:
        jsonld = extract_jobposting_jsonld(page.css('script[type="application/ld+json"]::text').getall() or [])
        if jsonld:
            listing = listing_from_jsonld(jsonld, url, source)
            if listing:
                return listing

        title = (page.css("h1::text").get() or "").strip()
        if not title:
            raw_title = page.css("title::text").get() or ""
            title = raw_title.split("|")[0].split("-")[0].strip()

        body_text = page.get_all_text(ignore_tags=("script", "style")) or ""
        body_text = re.sub(r"\s+", " ", body_text).strip()

        if not title or not body_text:
            return None

        company = company_from_url(url, source)
        location = guess_location(body_text[:2000])

        return JobListing(
            source=source,
            url=url,
            title=title[:200],
            company=company,
            location=location,
            remote_type=guess_remote_type(location),
            description=body_text[:MAX_DESCRIPTION_CHARS],
            description_source="page_text",
            description_is_partial=True,  # raw page text, not a structured description
        )
    except Exception as e:
        print(f"[parsing] failed to parse {url}: {e}", file=sys.stderr)
        return None
