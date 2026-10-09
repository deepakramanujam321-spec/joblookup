"""One function per source family. Each returns a list[JobListing] and never
raises — a source going down shouldn't take the whole run with it.
"""

from __future__ import annotations

import re
import sys
from urllib.parse import urlparse

import feedparser
import requests

from . import ats, brave_search, normalize, scrapling_fetch
from .models import JobListing
from .parsing import parse_fetched_page

BOARD_EXPANSION_LIMIT = 15  # extra relevant postings pulled per discovered ATS board

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


listing_from_ats = ats.to_listing  # kept for callers of the old name


def _title_is_relevant(title: str, profile: dict) -> bool:
    """Board expansion keeps postings in the candidate's own role families
    (derived from their preferred titles), not every opening a company has."""
    preferred = {normalize.detect_category(t) for t in profile["role_focus"]["titles"]} - {"other"}
    return normalize.detect_category(title) in preferred


_INDIA_HINTS = ("india", "bengaluru", "bangalore", "hyderabad", "pune", "mumbai", "chennai", "delhi", "gurgaon",
                "gurugram", "noida", "apac", "asia", "anywhere", "worldwide", "global")


def _location_is_plausible(job: ats.AtsJob) -> bool:
    """Board expansion only: skip postings explicitly tied to an office or
    region nowhere near the candidate's stated preferences. Search hits are
    never dropped by this -- only the extra postings pulled from a board."""
    loc = (job.location or "").lower()
    if not loc:
        return True
    if any(h in loc for h in _INDIA_HINTS):
        return True
    return job.remote_type == "remote" and not re.search(
        r"\b(us|usa|united states|canada|uk|europe|emea|latam|americas|germany|france)\b", loc
    )


def discover_ats_boards(profile: dict, brave_api_key: str) -> list[JobListing]:
    """Brave Search finds ATS postings matching the target titles; each hit
    is then read from that ATS's official public API (full structured
    description, real publication date), and the board it came from is
    expanded with its other relevant openings."""
    titles = profile["role_focus"]["titles"]
    listings: dict[str, JobListing] = {}
    boards: dict[tuple[str, str], str] = {}
    ashby = ats.AshbyBoardCache()

    for query in _build_ats_queries(titles):
        for result in brave_search.search(query, brave_api_key, count=8):
            url = result["url"]
            ref = ats.parse_ats_url(url)
            if ref is None:
                continue
            fallback_company = parse_company_from_board(ref.board)
            boards.setdefault((ref.source, ref.board), fallback_company)
            canonical = normalize.canonical_url(url)
            if not ref.job_id or canonical in listings:
                continue
            fetched = ats.fetch_posting(ref, ashby)
            if fetched.job:
                listings[canonical] = listing_from_ats(fetched.job, fallback_company)
            elif not fetched.closed:
                # API unavailable: fall back to reading the page itself.
                page = (
                    scrapling_fetch.fetch_stealth(url) if ref.source in STEALTH_SOURCES else scrapling_fetch.fetch_fast(url)
                )
                listing = parse_fetched_page(page, url, ref.source)
                if listing:
                    listings[canonical] = listing

    for (source, board), fallback_company in boards.items():
        board_jobs = ats.list_board(source, board, ashby) or []
        added = 0
        for job in board_jobs:
            if added >= BOARD_EXPANSION_LIMIT:
                break
            canonical = normalize.canonical_url(job.url)
            if canonical in listings or not _title_is_relevant(job.title, profile) or not _location_is_plausible(job):
                continue
            listings[canonical] = listing_from_ats(job, fallback_company)
            added += 1
        print(f"[sources] {source}/{board}: {len(board_jobs)} on board, {added} relevant added", file=sys.stderr)

    return list(listings.values())


def parse_company_from_board(board: str) -> str:
    return board.replace("-", " ").replace("_", " ").title()


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
                salary_text=_remoteok_salary_text(row),
                description=normalize.html_to_text(row.get("description") or ""),
                external_id=str(row.get("id", "")),
                posted_at=row.get("date", "") or "",
                description_html=normalize.sanitize_html(row.get("description")) or "",
                description_source="remoteok_api",
                description_is_partial=False,
                posted_at_evidence="remoteok_api.date" if row.get("date") else "",
                salary_min=float(row["salary_min"]) if row.get("salary_min") else None,
                salary_max=float(row["salary_max"]) if row.get("salary_max") else None,
                salary_currency="USD" if row.get("salary_min") else "",
                salary_period="year" if row.get("salary_min") else "",
            )
        )

    return listings


def _remoteok_salary_text(row: dict) -> str:
    low, high = row.get("salary_min"), row.get("salary_max")
    if low and high:
        return f"${int(low):,} - ${int(high):,} USD/year"
    return row.get("salary", "") or ""


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
                location=f"Remote ({entry.get('region')})" if entry.get("region") else "Remote",
                remote_type="remote",
                description=normalize.html_to_text(summary),
                posted_at=entry.get("published", ""),
                description_html=normalize.sanitize_html(summary) or "",
                description_source="wwr_rss",
                description_is_partial=True,  # RSS bodies are not guaranteed to be the full posting
                posted_at_evidence="wwr_rss.pubDate" if entry.get("published") else "",
                employment_type=normalize.normalize_employment_type(entry.get("type")) or "",
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

    # /jobs/view/ and viewjob target individual postings; the bare
    # /jobs/ path returned search-result pages, which are not vacancies.
    queries = [f'site:linkedin.com/jobs/view "{t}" India' for t in titles]
    queries += [f'site:in.indeed.com/viewjob "{t}"' for t in titles]

    for query in queries:
        for result in brave_search.search(query, brave_api_key, count=5):
            url = result["url"]
            domain = _domain(url)
            source = "linkedin" if "linkedin.com" in domain else "indeed" if "indeed.com" in domain else None
            if not source or url in seen_urls or not normalize.is_specific_listing_url(url, source):
                continue
            seen_urls.add(url)

            page = scrapling_fetch.fetch_stealth(url)
            listing = parse_fetched_page(page, url, source)
            if listing:
                listings.append(listing)

    return listings
