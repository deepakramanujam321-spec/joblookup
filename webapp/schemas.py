"""Request bodies. Every field is validated here, on the server; nothing
the browser sends is trusted (ids in paths are checked for ownership in
the handlers, owners are never accepted from the client)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from jobseeker import lifecycle
from jobseeker.learning import FEEDBACK_EFFECTS
from jobseeker.profile import CandidateProfile

# v1 statuses (jobs.status), kept for the legacy endpoint
VALID_STATUSES = {
    "new", "scored", "queued_for_digest", "sent_in_digest",
    "applied", "rejected", "excluded",
}


class JobUpdate(BaseModel):
    """v1 PATCH /api/jobs/{id} body (kept for compatibility)."""

    status: str | None = None
    outreach_draft: str | None = None


def _http_url(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    if not value.startswith(("https://", "http://")) or len(value) > 2000:
        raise ValueError("must be an http(s) URL")
    return value


class ApplicationUpdate(BaseModel):
    status: str | None = None
    saved: bool | None = None
    notes: str | None = Field(default=None, max_length=20_000)
    resume_id: int | None = None
    application_url: str | None = None
    recruiter_name: str | None = Field(default=None, max_length=200)
    recruiter_contact: str | None = Field(default=None, max_length=300)
    next_follow_up_at: datetime | None = None
    final_draft_id: int | None = None
    note: str | None = Field(default=None, max_length=2000, description="Optional note recorded with a status change")

    @field_validator("status")
    @classmethod
    def _status(cls, v):
        if v is not None and v not in lifecycle.STATUSES:
            raise ValueError(f"must be one of {list(lifecycle.STATUSES)}")
        return v

    @field_validator("application_url")
    @classmethod
    def _url(cls, v):
        return _http_url(v)


class TaskCreate(BaseModel):
    kind: Literal["interview", "follow_up", "reminder"]
    title: str = Field(min_length=1, max_length=300)
    due_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=5000)


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    due_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=5000)
    done: bool | None = None


class FeedbackCreate(BaseModel):
    category: str
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("category")
    @classmethod
    def _category(cls, v):
        if v not in FEEDBACK_EFFECTS:
            raise ValueError(f"must be one of {list(FEEDBACK_EFFECTS)}")
        return v


class PreferenceUpdate(BaseModel):
    disabled: bool


class DraftGenerate(BaseModel):
    resume_id: int | None = None
    include_cover_letter: bool = False
    questions: list[str] = Field(default_factory=list, max_length=15)

    @field_validator("questions")
    @classmethod
    def _questions(cls, v):
        return [q.strip()[:500] for q in v if q.strip()]


class DraftEdit(BaseModel):
    subject: str | None = Field(default=None, max_length=300)
    body: str = Field(min_length=1, max_length=20_000)
    cover_letter: str | None = Field(default=None, max_length=20_000)
    parent_id: int | None = None


class GmailSave(BaseModel):
    to: str | None = Field(default=None, max_length=300)

    @field_validator("to")
    @classmethod
    def _to(cls, v):
        if v and ("@" not in v or "\n" in v or "\r" in v):
            raise ValueError("must be a single email address")
        return v or None


class ProfileUpdate(BaseModel):
    data: CandidateProfile
    additional_info: str | None = Field(default=None, max_length=10_000)
    version: int | None = None


class ResumeUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    purpose: str | None = Field(default=None, max_length=500)
    is_default: bool | None = None


class ResumeApply(BaseModel):
    sections: list[Literal["headline", "summary", "experience", "skills", "projects", "education", "certifications", "total_experience_years"]]


class DriveImport(BaseModel):
    file_ids: list[str] = Field(min_length=1, max_length=10)
    kind: Literal["resume", "knowledge"] = "knowledge"
