"""Candidate profile: the structured statement of who the candidate is and
what they want, which matching, drafting and learning all read from.

Storage is one JSONB document per owner (validated by CandidateProfile
below) rather than a dozen narrow tables: every field is optional, the
shape evolves with the UI, and nothing queries inside it relationally.
`version` increments on every save so cached assessments know when they
were computed against an older profile.

First access seeds the profile from config/profile.yaml + config/resume.txt
so an existing deployment carries straight over; after that the database
copy is authoritative and the YAML only drives search discovery defaults.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

import sqlalchemy as sa
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from . import config, normalize
from .database import candidate_profiles, resumes

DEFAULT_OWNER = "default"


class SkillEntry(BaseModel):
    name: str
    level: str | None = None  # beginner | intermediate | advanced | expert
    years: float | None = None
    source: str = "self"  # self (you said so) | resume (found in a resume) | evidence (project/role)


class ExperienceEntry(BaseModel):
    title: str = ""
    company: str = ""
    start: str | None = None
    end: str | None = None
    summary: str = ""
    technologies: list[str] = Field(default_factory=list)


class ProjectEntry(BaseModel):
    name: str = ""
    description: str = ""
    achievements: list[str] = Field(default_factory=list)


class EducationEntry(BaseModel):
    institution: str = ""
    degree: str = ""
    year: str | None = None


class CertificationEntry(BaseModel):
    name: str = ""
    issuer: str = ""
    year: str | None = None


class LocationPreference(BaseModel):
    label: str
    mode: str = "flexible"  # remote | hybrid | onsite | flexible
    places: list[str] = Field(default_factory=list)  # empty for a remote preference = anywhere


class Compensation(BaseModel):
    currency: str = "INR"
    min_annual: float | None = None
    target_annual: float | None = None


class Thresholds(BaseModel):
    review: float = 60  # priority at/above this lands in "Worth reviewing"
    high_priority: float = 75
    stale_days: int = 14  # unverified for longer than this = possibly stale


class CandidateProfile(BaseModel):
    name: str = ""
    email: str = ""
    headline: str = ""
    current_role: str = ""
    current_company: str = ""
    summary: str = ""
    total_experience_years: float | None = None
    experience: list[ExperienceEntry] = Field(default_factory=list)
    skills: list[SkillEntry] = Field(default_factory=list)
    projects: list[ProjectEntry] = Field(default_factory=list)
    education: list[EducationEntry] = Field(default_factory=list)
    certifications: list[CertificationEntry] = Field(default_factory=list)
    preferred_titles: list[str] = Field(default_factory=list)
    preferred_domains: list[str] = Field(default_factory=list)
    preferred_company_types: list[str] = Field(default_factory=list)
    locations: list[LocationPreference] = Field(default_factory=list)
    compensation: Compensation = Field(default_factory=Compensation)
    employment_types: list[str] = Field(default_factory=lambda: ["full_time"])
    seniority_levels: list[str] = Field(default_factory=list)
    growth_skills: list[str] = Field(default_factory=list)
    excluded_companies: list[str] = Field(default_factory=list)
    deal_breakers: list[str] = Field(default_factory=list)
    search_keywords: list[str] = Field(default_factory=list)
    work_authorization: str | None = None
    thresholds: Thresholds = Field(default_factory=Thresholds)
    weights: dict[str, float] = Field(default_factory=dict)  # optional overrides of matching.DEFAULT_WEIGHTS
    evidence_sources: list[dict] = Field(default_factory=list)  # which documents informed which sections


_INDIAN_CITIES = ["bengaluru", "bangalore", "hyderabad", "mumbai", "pune", "chennai", "delhi", "ncr",
                  "gurgaon", "gurugram", "noida", "kolkata", "ahmedabad"]
_CITY_ALIASES = {"bengaluru": ["bengaluru", "bangalore"], "bangalore": ["bengaluru", "bangalore"]}


def parse_location_label(label: str) -> LocationPreference:
    """'Remote (India)' -> remote/[india]; 'Hyderabad (on-site/hybrid)' ->
    flexible/[hyderabad]; 'Remote (global, ...)' -> remote/[] (anywhere);
    'Other Indian metro (relocation)' -> flexible/[indian cities]."""
    lowered = label.lower()
    places: list[str] = []
    for city in _INDIAN_CITIES:
        if re.search(rf"\b{city}\b", lowered):
            places.extend(_CITY_ALIASES.get(city, [city]))
    if "remote" in lowered:
        mode = "remote"
        if "global" in lowered or "anywhere" in lowered or "worldwide" in lowered:
            places = []
        elif "india" in lowered and not places:
            places = ["india"]
    else:
        mode = "hybrid" if "hybrid" in lowered and "on-site" not in lowered and "onsite" not in lowered else "flexible"
        if not places and "india" in lowered:
            places = ["india", *_INDIAN_CITIES]
    return LocationPreference(label=label, mode=mode, places=list(dict.fromkeys(places)))


def seed_from_yaml(yaml_profile: dict, resume_text: str) -> CandidateProfile:
    candidate = yaml_profile.get("candidate", {})
    role_focus = yaml_profile.get("role_focus", {})
    exclusions = yaml_profile.get("exclusions", {})
    floor_lpa = yaml_profile.get("salary_floor_lpa")
    skills = [SkillEntry(name=s, source="resume") for s in normalize.extract_skills(resume_text)]
    return CandidateProfile(
        name=candidate.get("name", ""),
        email=candidate.get("digest_email", ""),
        current_company=candidate.get("current_employer", ""),
        skills=skills,
        preferred_titles=list(role_focus.get("titles", [])),
        search_keywords=list(role_focus.get("keywords_any", [])),
        locations=[parse_location_label(l) for l in yaml_profile.get("location_preference", [])],
        compensation=Compensation(currency="INR", min_annual=float(floor_lpa) * 100_000 if floor_lpa else None),
        excluded_companies=list(exclusions.get("companies", [])),
    )


def _row_to_dict(row) -> dict:
    return {
        "owner": row.owner,
        "data": CandidateProfile.model_validate(row.data).model_dump(),
        "additional_info": row.additional_info or "",
        "version": row.version,
        "learning_reset_at": row.learning_reset_at,
        "updated_at": row.updated_at,
    }


def get_or_seed(conn: Connection, owner: str = DEFAULT_OWNER) -> dict:
    row = conn.execute(sa.select(candidate_profiles).where(candidate_profiles.c.owner == owner)).first()
    if row is not None:
        return _row_to_dict(row)
    yaml_profile = config.load_profile()
    try:
        resume_text = config.load_resume_text(yaml_profile)
    except (OSError, KeyError):
        resume_text = ""
    seeded = seed_from_yaml(yaml_profile, resume_text)
    conn.execute(
        pg_insert(candidate_profiles)
        .values(owner=owner, data=seeded.model_dump(), version=1)
        .on_conflict_do_nothing(index_elements=["owner"])
    )
    if resume_text:
        _seed_resume(conn, owner, resume_text)
    row = conn.execute(sa.select(candidate_profiles).where(candidate_profiles.c.owner == owner)).one()
    return _row_to_dict(row)


def _seed_resume(conn: Connection, owner: str, text: str) -> None:
    exists = conn.execute(
        sa.select(sa.func.count()).select_from(resumes).where(resumes.c.owner == owner)
    ).scalar_one()
    if exists:
        return
    conn.execute(
        sa.insert(resumes).values(
            owner=owner,
            display_name="Resume (from config/resume.txt)",
            purpose="Seeded from the repository's resume text",
            version=1,
            filename="resume.txt",
            content_type="text/plain",
            size_bytes=len(text.encode()),
            sha256=hashlib.sha256(text.encode()).hexdigest(),
            source="seed",
            is_default=True,
            extraction_status="done",
            extracted_text=text,
        )
    )


def save(conn: Connection, owner: str, data: CandidateProfile, additional_info: str | None, expected_version: int | None) -> dict:
    """Optimistic concurrency: a save based on a stale version is refused
    rather than silently overwriting a newer edit from another tab."""
    current = get_or_seed(conn, owner)
    if expected_version is not None and expected_version != current["version"]:
        raise VersionConflict(current["version"])
    conn.execute(
        sa.update(candidate_profiles)
        .where(candidate_profiles.c.owner == owner)
        .values(
            data=data.model_dump(),
            additional_info=additional_info,
            version=candidate_profiles.c.version + 1,
            updated_at=datetime.now(timezone.utc),
        )
    )
    return get_or_seed(conn, owner)


class VersionConflict(Exception):
    def __init__(self, current_version: int):
        super().__init__(f"profile changed since it was loaded (now version {current_version})")
        self.current_version = current_version


def default_resume_text(conn: Connection, owner: str) -> tuple[int | None, str]:
    row = conn.execute(
        sa.select(resumes.c.id, resumes.c.extracted_text)
        .where(resumes.c.owner == owner, resumes.c.deleted_at.is_(None), resumes.c.extraction_status == "done")
        .order_by(resumes.c.is_default.desc(), resumes.c.uploaded_at.desc())
        .limit(1)
    ).first()
    return (row.id, row.extracted_text or "") if row else (None, "")


def resume_text(conn: Connection, owner: str, resume_id: int | None) -> tuple[int | None, str]:
    if resume_id is None:
        return default_resume_text(conn, owner)
    row = conn.execute(
        sa.select(resumes.c.id, resumes.c.extracted_text).where(
            resumes.c.id == resume_id, resumes.c.owner == owner, resumes.c.deleted_at.is_(None)
        )
    ).first()
    if row is None:
        raise LookupError("resume not found")
    return row.id, row.extracted_text or ""


def evidence_text(profile_data: dict, resume: str) -> str:
    """Everything the candidate has actually documented -- the only text a
    claimed skill or achievement may be grounded in."""
    parts = [resume, profile_data.get("summary", ""), profile_data.get("headline", "")]
    for e in profile_data.get("experience", []):
        parts += [e.get("title", ""), e.get("summary", ""), " ".join(e.get("technologies", []))]
    for p in profile_data.get("projects", []):
        parts += [p.get("name", ""), p.get("description", ""), " ".join(p.get("achievements", []))]
    parts += [s["name"] for s in profile_data.get("skills", [])]
    parts += [c.get("name", "") for c in profile_data.get("certifications", [])]
    return "\n".join(p for p in parts if p)
