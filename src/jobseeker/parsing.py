"""Turns a fetched page (or an API payload) into a JobListing.

Deliberately generic rather than pinned to one exact DOM shape: ATS board
markup drifts over time and breaking silently on a selector change is worse
than extracting slightly-loose text. We only need enough signal for the
keyword filter — the agent reads the full description later for real
scoring, so over-extraction here is cheap and under-extraction is costly.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

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


def parse_fetched_page(page, url: str, source: str) -> JobListing | None:
    """`page` is a Scrapling page object (see scrapling_fetch.py)."""
    if page is None:
        return None

    try:
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
        )
    except Exception as e:
        print(f"[parsing] failed to parse {url}: {e}")
        return None
