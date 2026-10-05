"""The one data shape every source converges to before it reaches storage."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass
class JobListing:
    source: str  # "greenhouse" | "lever" | "ashby" | "remoteok" | "weworkremotely" | "linkedin" | "indeed"
    url: str
    title: str
    company: str
    location: str = ""
    remote_type: str = ""  # "remote" | "hybrid" | "onsite" | ""
    salary_text: str = ""
    description: str = ""
    external_id: str = ""
    posted_at: str = ""  # ISO date string if known, else ""

    def to_dict(self) -> dict:
        return asdict(self)
