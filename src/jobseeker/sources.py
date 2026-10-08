"""One function per source family. Each returns a list[JobListing] and never
raises — a source going down shouldn't take the whole run with it.
"""

from __future__ import annotations

import sys
from urllib.parse import urlparse

import feedparser
import requests

from . import brave_search, scrapling_fetch
from .models import JobListing
from .parsing import parse_fetched_page

ATS_DOMAINS = {
    "boards.greenhouse.io": "greenhouse",
    "job-boards.greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever",
    "jobs.ashbyhq.com": "ashby",
}

STEALTH_SOURCES = {"ashby", "linkedin", "indeed"}


def _domain(url: str) -> str:
    return urlparse(url).netloc.lower()


def _build_ats_queries(titles: list[str]) -> list[str]:
    queries = []
    for title in titles[:5]:
        queries.append(f'site:boards.greenhouse.io "{title}"')
        queries.append(f'site:jobs.lever.co "{title}"')
        queries.append(f'site:jobs.ashbyhq.com "{title}"')
    return queries


def discover_ats_boards(profile: dict, brave_api_key: str) -> list[JobListing]:
    titles = profile["role_focus"]["titles"]
    listings: list[JobListing] = []
    seen_urls: set[str] = set()

    for query in _build_ats_queries(titles):
        for result in brave_search.search(query, brave_api_key, count=8):
            url = result["url"]
            domain = _domain(url)
            source = ATS_DOMAINS.get(domain)
            if not source or url in seen_urls:
                continue
            seen_urls.add(url)

            page = (
                scrapling_fetch.fetch_stealth(url)
                if source in STEALTH_SOURCES
                else scrapling_fetch.fetch_fast(url)
            )
            listing = parse_fetched_page(page, url, source)
            if listing:
                listings.append(listing)

    return listings


def fetch_remoteok(profile: dict) -> list[JobListing]:
    """RemoteOK's public JSON API. Requires a real User-Agent or it 403s."""
    try:
        resp = requests.get(
            "https://remoteok.com/api",
            headers={"User-Agent": "Mozilla/5.0 (job-search-agent; personal use)"},
            timeout=15,
        )
        resp.raise_for_status()
        rows = resp.json()
    except (requests.RequestException, ValueError) as e:
        print(f"[sources] remoteok fetch failed: {e}", file=sys.stderr)
        return []

    titles_lower = [t.lower() for t in profile["role_focus"]["titles"]]
    keywords_lower = [k.lower() for k in profile["role_focus"]["keywords_any"]]
    listings = []

    for row in rows:
        if not isinstance(row, dict) or "position" not in row:
            continue  # first element is API metadata, not a job
        position = row.get("position", "")
        tags = " ".join(row.get("tags", []))
        haystack = f"{position} {tags}".lower()
        if not any(k in haystack for k in titles_lower + keywords_lower):
            continue

        listings.append(
            JobListing(
                source="remoteok",
                url=row.get("url", f"https://remoteok.com/remote-jobs/{row.get('id', '')}"),
                title=position,
                company=row.get("company", ""),
                location=row.get("location", "Remote"),
                remote_type="remote",
                salary_text=row.get("salary", "") or "",
                description=(row.get("description", "") or "")[:4000],
                external_id=str(row.get("id", "")),
                posted_at=row.get("date", ""),
            )
        )

    return listings


def fetch_weworkremotely(profile: dict) -> list[JobListing]:
    feed_url = "https://weworkremotely.com/categories/remote-programming-jobs.rss"
    try:
        feed = feedparser.parse(feed_url)
    except Exception as e:
        print(f"[sources] weworkremotely fetch failed: {e}", file=sys.stderr)
        return []

    keywords_lower = [k.lower() for k in profile["role_focus"]["keywords_any"]]
    listings = []

    for entry in feed.entries:
        title = entry.get("title", "")
        summary = entry.get("summary", "")
        haystack = f"{title} {summary}".lower()
        if not any(k in haystack for k in keywords_lower):
            continue

        # WWR titles are usually "Company: Job Title"
        company, _, job_title = title.partition(":")
        listings.append(
            JobListing(
                source="weworkremotely",
                url=entry.get("link", ""),
                title=(job_title or title).strip(),
                company=company.strip() if job_title else "",
                location="Remote",
                remote_type="remote",
                description=summary[:4000],
                posted_at=entry.get("published", ""),
            )
        )

    return listings


def discover_linkedin_indeed(profile: dict, brave_api_key: str) -> list[JobListing]:
    """Best-effort, lower-confidence source. Public search-result pages only —
    no login, no authenticated scraping. Rate-limited via scrapling_fetch's
    built-in delay. Treat results here as lower-priority than ATS/remote-board
    sources; LinkedIn/Indeed actively fight automated access.
    """
    titles = profile["role_focus"]["titles"][:3]  # keep this source's footprint small
    listings: list[JobListing] = []
    seen_urls: set[str] = set()

    queries = [f'site:linkedin.com/jobs "{t}" India' for t in titles]
    queries += [f'site:indeed.com "{t}" India' for t in titles]

    for query in queries:
        for result in brave_search.search(query, brave_api_key, count=5):
            url = result["url"]
            domain = _domain(url)
            source = "linkedin" if "linkedin.com" in domain else "indeed" if "indeed.com" in domain else None
            if not source or url in seen_urls:
                continue
            seen_urls.add(url)

            page = scrapling_fetch.fetch_stealth(url)
            listing = parse_fetched_page(page, url, source)
            if listing:
                listings.append(listing)

    return listings
