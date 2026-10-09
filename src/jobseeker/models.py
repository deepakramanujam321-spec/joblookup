"""The one data shape every source converges to before it reaches storage."""

from __future__ import annotations

from dataclasses import asdict, dataclass


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
    posted_at: str = ""  # raw source value, as given; parsed during ingestion
    # Richer fields, set by sources that actually provide them (official
    # ATS APIs, JSON-LD). Left empty they mean "unknown", never "none".
    description_html: str = ""
    description_source: str = ""  # which feed/method produced the description
    description_is_partial: bool = True
    posted_at_evidence: str = ""  # e.g. "lever_api.createdAt"; "" when no source date
    source_updated_at: str = ""
    deadline_at: str = ""
    employment_type: str = ""
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str = ""
    salary_period: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, row: dict) -> "JobListing":
        """Tolerates older collect output (fewer keys) and ignores extras,
        so a collect artifact from one version can be scored by the next."""
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in row.items() if k in known})
