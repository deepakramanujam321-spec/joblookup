"""Official public job-board APIs for Greenhouse, Lever and Ashby.

These are the employers' own published feeds (documented, unauthenticated,
intended for exactly this), so they're preferred over scraping the HTML
board pages: they return the full structured description, the real
publication timestamp, employment type, workplace type and often
compensation -- and a 404 is authoritative evidence that a posting closed.

Each fetch returns an AtsResult: `job` is set when the posting is live,
`closed` is True only when the source itself says the posting is gone, and
`error` describes a failure that proves nothing either way (network,
rate-limit, 5xx). Callers must never treat `error` as "closed".
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

import requests

from . import normalize
from .models import JobListing

USER_AGENT = "joblookup/2.0 (personal job-search assistant)"
TIMEOUT = 15


@dataclass
class AtsJob:
    source: str
    board: str
    external_id: str
    url: str
    title: str
    company: str = ""
    location: str = ""
    remote_type: str | None = None
    employment_type: str | None = None
    description_html: str | None = None
    description_text: str = ""
    posted_at: object = None  # parsed by normalize.parse_datetime
    posted_at_evidence: str | None = None
    updated_at: object = None
    salary: dict | None = None
    salary_text: str = ""
    department: str = ""


@dataclass
class AtsResult:
    job: AtsJob | None = None
    closed: bool = False
    http_status: int | None = None
    error: str | None = None
    method: str = ""


@dataclass
class AtsRef:
    source: str
    board: str
    job_id: str | None = None
    extra: dict = field(default_factory=dict)


_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def parse_ats_url(url: str) -> AtsRef | None:
    parts = urlparse(url)
    host = parts.netloc.lower().removeprefix("www.")
    segments = [s for s in parts.path.split("/") if s]
    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io", "job-boards.eu.greenhouse.io") and segments:
        board = segments[0]
        if len(segments) >= 3 and segments[1] == "jobs" and segments[2].isdigit():
            return AtsRef("greenhouse", board, segments[2])
        gh_jid = parse_qs(parts.query).get("gh_jid")
        return AtsRef("greenhouse", board, gh_jid[0] if gh_jid else None)
    if host == "jobs.lever.co" and segments:
        job_id = segments[1] if len(segments) >= 2 and re.fullmatch(_UUID, segments[1], re.I) else None
        return AtsRef("lever", segments[0], job_id)
    if host == "jobs.ashbyhq.com" and segments:
        job_id = segments[1] if len(segments) >= 2 and re.fullmatch(_UUID, segments[1], re.I) else None
        return AtsRef("ashby", segments[0], job_id)
    return None


def _get(url: str, params: dict | None = None) -> requests.Response:
    return requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)


def _failure(method: str, exc: Exception | None = None, resp: requests.Response | None = None) -> AtsResult:
    status = resp.status_code if resp is not None else None
    detail = f"HTTP {status}" if resp is not None else f"{type(exc).__name__}: {exc}"
    return AtsResult(http_status=status, error=detail, method=method)


# ---------------------------------------------------------------- greenhouse


def _greenhouse_job(board: str, row: dict) -> AtsJob:
    html = normalize.sanitize_html(row.get("content"))
    location = (row.get("location") or {}).get("name", "") or ""
    # `first_published` is when the posting first went live; `updated_at`
    # moves on every edit/repost, so it's kept separately and never shown
    # as the posting date.
    first_published = row.get("first_published")
    pay = None
    pay_ranges = row.get("pay_input_ranges") or []
    if pay_ranges:
        r = pay_ranges[0]
        try:
            pay = {
                "min": float(r["min_cents"]) / 100, "max": float(r["max_cents"]) / 100,
                "currency": r.get("currency_type") or None, "period": "year",
            }
        except (KeyError, TypeError, ValueError):
            pay = None
    return AtsJob(
        source="greenhouse",
        board=board,
        external_id=str(row["id"]),
        url=row.get("absolute_url") or f"https://boards.greenhouse.io/{board}/jobs/{row['id']}",
        title=(row.get("title") or "").strip(),
        company=row.get("company_name") or "",
        location=location,
        remote_type=normalize.classify_remote(location),
        employment_type=None,
        description_html=html,
        description_text=normalize.html_to_text(html),
        posted_at=first_published,
        posted_at_evidence="greenhouse_api.first_published" if first_published else None,
        updated_at=row.get("updated_at"),
        salary=pay,
        department=", ".join(d.get("name", "") for d in row.get("departments") or []),
    )


def fetch_greenhouse(board: str, job_id: str) -> AtsResult:
    method = "greenhouse_api"
    try:
        resp = _get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}", {"pay_transparency": "true"})
    except requests.RequestException as e:
        return _failure(method, exc=e)
    if resp.status_code == 404:
        return AtsResult(closed=True, http_status=404, method=method)
    if resp.status_code != 200:
        return _failure(method, resp=resp)
    try:
        return AtsResult(job=_greenhouse_job(board, resp.json()), http_status=200, method=method)
    except (ValueError, KeyError) as e:
        return _failure(method, exc=e)


def list_greenhouse_board(board: str) -> list[AtsJob] | None:
    try:
        resp = _get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs", {"content": "true"})
        if resp.status_code != 200:
            return None
        return [_greenhouse_job(board, row) for row in resp.json().get("jobs", [])]
    except (requests.RequestException, ValueError, KeyError) as e:
        print(f"[ats] greenhouse board {board} failed: {e}", file=sys.stderr)
        return None


# --------------------------------------------------------------------- lever


def _lever_job(company: str, row: dict) -> AtsJob:
    sections = [row.get("description") or ""]
    for block in row.get("lists") or []:
        sections.append(f"<h3>{block.get('text', '')}</h3><ul>{block.get('content', '')}</ul>")
    sections.append(row.get("additional") or "")
    html = normalize.sanitize_html("".join(sections))
    categories = row.get("categories") or {}
    location = categories.get("location") or ", ".join(categories.get("allLocations") or [])
    salary_range = row.get("salaryRange") or {}
    salary = None
    if salary_range.get("min") is not None and salary_range.get("max") is not None:
        interval = (salary_range.get("interval") or "").lower()
        salary = {
            "min": float(salary_range["min"]), "max": float(salary_range["max"]),
            "currency": salary_range.get("currency"),
            "period": "hour" if "hour" in interval else "month" if "month" in interval else "year",
        }
    return AtsJob(
        source="lever",
        board=company,
        external_id=row["id"],
        url=row.get("hostedUrl") or f"https://jobs.lever.co/{company}/{row['id']}",
        title=(row.get("text") or "").strip(),
        location=location or "",
        remote_type=normalize.classify_remote(location, row.get("workplaceType")),
        employment_type=normalize.normalize_employment_type(categories.get("commitment")),
        description_html=html,
        description_text=normalize.html_to_text(html),
        posted_at=row.get("createdAt"),
        posted_at_evidence="lever_api.createdAt" if row.get("createdAt") else None,
        salary=salary,
        department=categories.get("team") or "",
    )


def fetch_lever(company: str, job_id: str) -> AtsResult:
    method = "lever_api"
    try:
        resp = _get(f"https://api.lever.co/v0/postings/{company}/{job_id}")
    except requests.RequestException as e:
        return _failure(method, exc=e)
    if resp.status_code == 404:
        return AtsResult(closed=True, http_status=404, method=method)
    if resp.status_code != 200:
        return _failure(method, resp=resp)
    try:
        return AtsResult(job=_lever_job(company, resp.json()), http_status=200, method=method)
    except (ValueError, KeyError) as e:
        return _failure(method, exc=e)


def list_lever_board(company: str) -> list[AtsJob] | None:
    try:
        resp = _get(f"https://api.lever.co/v0/postings/{company}", {"mode": "json"})
        if resp.status_code != 200:
            return None
        return [_lever_job(company, row) for row in resp.json()]
    except (requests.RequestException, ValueError, KeyError) as e:
        print(f"[ats] lever board {company} failed: {e}", file=sys.stderr)
        return None


# --------------------------------------------------------------------- ashby


def _ashby_job(org: str, row: dict) -> AtsJob:
    html = normalize.sanitize_html(row.get("descriptionHtml"))
    location = row.get("location") or ""
    salary = None
    for component in ((row.get("compensation") or {}).get("summaryComponents") or []):
        if component.get("compensationType") == "Salary" and component.get("minValue") is not None:
            interval = (component.get("interval") or "").upper()
            salary = {
                "min": float(component["minValue"]),
                "max": float(component.get("maxValue") or component["minValue"]),
                "currency": component.get("currencyCode"),
                "period": "hour" if "HOUR" in interval else "month" if "MONTH" in interval else "year",
            }
            break
    workplace = row.get("workplaceType") or ("Remote" if row.get("isRemote") else None)
    return AtsJob(
        source="ashby",
        board=org,
        external_id=row["id"],
        url=row.get("jobUrl") or f"https://jobs.ashbyhq.com/{org}/{row['id']}",
        title=(row.get("title") or "").strip(),
        location=location,
        remote_type=normalize.classify_remote(location, workplace),
        employment_type=normalize.normalize_employment_type(row.get("employmentType")),
        description_html=html,
        description_text=row.get("descriptionPlain") or normalize.html_to_text(html),
        posted_at=row.get("publishedAt"),
        posted_at_evidence="ashby_api.publishedAt" if row.get("publishedAt") else None,
        salary=salary,
        salary_text=(row.get("compensation") or {}).get("compensationTierSummary") or "",
        department=row.get("department") or "",
    )


class AshbyBoardCache:
    """Ashby has no single-posting endpoint; one board fetch answers every
    posting on it, so cache boards for the duration of a run."""

    def __init__(self) -> None:
        self._boards: dict[str, tuple[dict[str, dict] | None, AtsResult | None]] = {}

    def board(self, org: str) -> tuple[dict[str, dict] | None, AtsResult | None]:
        if org not in self._boards:
            method = "ashby_api"
            try:
                resp = _get(
                    f"https://api.ashbyhq.com/posting-api/job-board/{org}", {"includeCompensation": "true"}
                )
                if resp.status_code == 404:
                    self._boards[org] = (None, AtsResult(http_status=404, error="board not found", method=method))
                elif resp.status_code != 200:
                    self._boards[org] = (None, _failure(method, resp=resp))
                else:
                    rows = resp.json().get("jobs", [])
                    self._boards[org] = ({r["id"]: r for r in rows if r.get("isListed", True)}, None)
            except (requests.RequestException, ValueError, KeyError) as e:
                self._boards[org] = (None, _failure(method, exc=e))
        return self._boards[org]

    def fetch(self, org: str, job_id: str) -> AtsResult:
        rows, failure = self.board(org)
        if failure:
            return failure
        row = rows.get(job_id)
        if row is None:
            # The board loaded fine and this posting isn't on it: removed.
            return AtsResult(closed=True, http_status=200, method="ashby_api")
        return AtsResult(job=_ashby_job(org, row), http_status=200, method="ashby_api")

    def list(self, org: str) -> list[AtsJob] | None:
        rows, failure = self.board(org)
        if failure:
            return None
        return [_ashby_job(org, row) for row in rows.values()]


def fetch_posting(ref: AtsRef, ashby: AshbyBoardCache) -> AtsResult:
    if not ref.job_id:
        return AtsResult(error="URL has no posting id", method=f"{ref.source}_api")
    if ref.source == "greenhouse":
        return fetch_greenhouse(ref.board, ref.job_id)
    if ref.source == "lever":
        return fetch_lever(ref.board, ref.job_id)
    if ref.source == "ashby":
        return ashby.fetch(ref.board, ref.job_id)
    return AtsResult(error=f"unsupported source {ref.source}")


def list_board(source: str, board: str, ashby: AshbyBoardCache) -> list[AtsJob] | None:
    if source == "greenhouse":
        return list_greenhouse_board(board)
    if source == "lever":
        return list_lever_board(board)
    if source == "ashby":
        return ashby.list(board)
    return None


def to_listing(job: AtsJob, fallback_company: str = "") -> JobListing:
    """The common JobListing shape, marked as a complete API description."""
    salary = job.salary or {}
    return JobListing(
        source=job.source,
        url=job.url,
        title=job.title,
        company=job.company or fallback_company,
        location=job.location,
        remote_type=job.remote_type or "",
        salary_text=job.salary_text,
        description=job.description_text,
        external_id=job.external_id,
        posted_at=str(job.posted_at or ""),
        description_html=job.description_html or "",
        description_source=f"{job.source}_api",
        description_is_partial=False,
        posted_at_evidence=job.posted_at_evidence or "",
        source_updated_at=str(job.updated_at or ""),
        employment_type=job.employment_type or "",
        salary_min=salary.get("min"),
        salary_max=salary.get("max"),
        salary_currency=salary.get("currency") or "",
        salary_period=salary.get("period") or "",
    )
