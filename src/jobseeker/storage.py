"""Supabase access via plain REST (PostgREST).

Everything lives in the `jobseeker` schema — a dedicated schema, not
`public`, so this project's tables never collide with the other apps
sharing this Supabase project (gym tracking, learning log, etc). PostgREST
only serves `public` by default; reaching a non-public schema requires the
schema to be added to the project's exposed-schemas list (already done —
see db/README.md) and every request to carry an Accept-Profile (reads) or
Content-Profile (writes) header naming it.
"""

from __future__ import annotations

from datetime import datetime, timezone

import requests

from .models import JobListing

SCHEMA = "jobseeker"
TABLE_JOBS = "jobs"
TABLE_RUNS = "runs"


class SupabaseStore:
    def __init__(self, url: str, service_role_key: str):
        self.base = f"{url.rstrip('/')}/rest/v1"
        self.auth_headers = {
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
            "Content-Type": "application/json",
        }

    def _read_headers(self) -> dict:
        return {**self.auth_headers, "Accept-Profile": SCHEMA}

    def _write_headers(self, prefer: str) -> dict:
        return {**self.auth_headers, "Content-Profile": SCHEMA, "Prefer": prefer}

    def insert_new_jobs(self, listings: list[JobListing]) -> int:
        """Upserts on `url`, ignoring rows that already exist. Returns the
        count of rows actually inserted (existing ones come back empty)."""
        if not listings:
            return 0
        rows = [{**l.to_dict(), "status": "new"} for l in listings]
        resp = requests.post(
            f"{self.base}/{TABLE_JOBS}?on_conflict=url",
            headers=self._write_headers("resolution=ignore-duplicates,return=representation"),
            json=rows,
            timeout=30,
        )
        resp.raise_for_status()
        return len(resp.json())

    def list_jobs(self, status: str) -> list[dict]:
        resp = requests.get(
            f"{self.base}/{TABLE_JOBS}",
            headers=self._read_headers(),
            params={"status": f"eq.{status}", "select": "*", "order": "discovered_at.asc"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def query_jobs(
        self,
        status: str | None = None,
        source: str | None = None,
        min_score: float | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """Flexible listing for the dashboard — all filters optional."""
        params: dict = {
            "select": "*",
            "order": "fit_score.desc.nullslast,discovered_at.desc",
            "limit": str(limit),
            "offset": str(offset),
        }
        if status:
            params["status"] = f"eq.{status}"
        if source:
            params["source"] = f"eq.{source}"
        if min_score is not None:
            params["fit_score"] = f"gte.{min_score}"
        if search:
            # matches title OR company, case-insensitive substring
            params["or"] = f"(title.ilike.*{search}*,company.ilike.*{search}*)"
        resp = requests.get(f"{self.base}/{TABLE_JOBS}", headers=self._read_headers(), params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def get_job(self, job_id: int) -> dict | None:
        resp = requests.get(
            f"{self.base}/{TABLE_JOBS}",
            headers=self._read_headers(),
            params={"id": f"eq.{job_id}", "select": "*"},
            timeout=30,
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else None

    def stats(self) -> dict:
        """Counts grouped by status, for the dashboard's summary header."""
        resp = requests.get(
            f"{self.base}/{TABLE_JOBS}",
            headers=self._read_headers(),
            params={"select": "status"},
            timeout=30,
        )
        resp.raise_for_status()
        counts: dict[str, int] = {}
        for row in resp.json():
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        return counts

    def list_runs(self, limit: int = 20) -> list[dict]:
        resp = requests.get(
            f"{self.base}/{TABLE_RUNS}",
            headers=self._read_headers(),
            params={"select": "*", "order": "started_at.desc", "limit": str(limit)},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def update_job(self, job_id: int, fields: dict) -> None:
        resp = requests.patch(
            f"{self.base}/{TABLE_JOBS}",
            headers=self._write_headers("return=minimal"),
            params={"id": f"eq.{job_id}"},
            json=fields,
            timeout=30,
        )
        resp.raise_for_status()

    def log_run(self, run_type: str, jobs_found: int = 0, jobs_new: int = 0, error: str = "") -> None:
        resp = requests.post(
            f"{self.base}/{TABLE_RUNS}",
            headers=self._write_headers("return=minimal"),
            json={
                "run_type": run_type,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "jobs_found": jobs_found,
                "jobs_new": jobs_new,
                "error": error or None,
            },
            timeout=30,
        )
        resp.raise_for_status()
