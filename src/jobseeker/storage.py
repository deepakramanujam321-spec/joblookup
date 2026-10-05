"""Supabase access via plain REST (PostgREST), not the MCP connector.

Routine-fired sessions in this account get a fresh container with no MCP
connectors attached, so anything that needs to run unattended on a schedule
has to work over plain HTTP instead. This talks directly to Supabase's
auto-generated REST API using the service-role key, which bypasses the
table's (deliberately policy-less) Row-Level Security.
"""

from __future__ import annotations

from datetime import datetime, timezone

import requests

from .models import JobListing

TABLE_JOBS = "jobseeker_jobs"
TABLE_RUNS = "jobseeker_runs"


class SupabaseStore:
    def __init__(self, url: str, service_role_key: str):
        self.base = f"{url.rstrip('/')}/rest/v1"
        self.headers = {
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
            "Content-Type": "application/json",
        }

    def insert_new_jobs(self, listings: list[JobListing]) -> int:
        """Upserts on `url`, ignoring rows that already exist. Returns the
        count of rows actually inserted (existing ones come back empty)."""
        if not listings:
            return 0
        rows = [{**l.to_dict(), "status": "new"} for l in listings]
        resp = requests.post(
            f"{self.base}/{TABLE_JOBS}?on_conflict=url",
            headers={
                **self.headers,
                "Prefer": "resolution=ignore-duplicates,return=representation",
            },
            json=rows,
            timeout=30,
        )
        resp.raise_for_status()
        return len(resp.json())

    def list_jobs(self, status: str) -> list[dict]:
        resp = requests.get(
            f"{self.base}/{TABLE_JOBS}",
            headers=self.headers,
            params={"status": f"eq.{status}", "select": "*", "order": "discovered_at.asc"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def update_job(self, job_id: int, fields: dict) -> None:
        resp = requests.patch(
            f"{self.base}/{TABLE_JOBS}",
            headers={**self.headers, "Prefer": "return=minimal"},
            params={"id": f"eq.{job_id}"},
            json=fields,
            timeout=30,
        )
        resp.raise_for_status()

    def log_run(self, run_type: str, jobs_found: int = 0, jobs_new: int = 0, error: str = "") -> None:
        resp = requests.post(
            f"{self.base}/{TABLE_RUNS}",
            headers={**self.headers, "Prefer": "return=minimal"},
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
