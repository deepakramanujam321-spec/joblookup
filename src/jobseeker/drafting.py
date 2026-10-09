"""Application drafts: generated from the selected resume + the posting,
grounded in the candidate's own evidence, versioned, never auto-sent.

Every generation and every user edit is a new immutable version
(application_drafts.version), so regenerating can never overwrite text the
user wrote. `origin` records whether a version came from the model, from
the user editing a previous version, or from the v1 pipeline.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from . import llm
from .database import application_drafts

PACKAGE_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_application_package",
        "description": "Submit the application materials for this posting.",
        "parameters": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Email subject line, specific to the role."},
                "email_body": {
                    "type": "string",
                    "description": "A short professional application email (120-200 words) in the candidate's voice. "
                                   "Use only experience, metrics and skills present in the resume/profile.",
                },
                "cover_letter": {
                    "type": ["string", "null"],
                    "description": "A cover letter (250-400 words) if requested, else null.",
                },
                "qualifications": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "requirement": {"type": "string"},
                            "evidence": {"type": "string", "description": "Quote or close paraphrase from the resume/profile."},
                        },
                        "required": ["requirement", "evidence"],
                    },
                    "description": "Up to 6 posting requirements the candidate meets, each with resume evidence.",
                },
                "missing_info": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Things the posting asks for that the resume/profile doesn't show, phrased as questions "
                                   "the candidate could answer (e.g. 'Do you have production Kubernetes experience?').",
                },
                "answers": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string"},
                            "answer": {"type": ["string", "null"]},
                            "needs_input": {"type": "boolean"},
                        },
                        "required": ["question", "answer", "needs_input"],
                    },
                    "description": "Answers to the supplied application questions. If the profile doesn't support an "
                                   "answer, set answer null and needs_input true.",
                },
            },
            "required": ["subject", "email_body", "cover_letter", "qualifications", "missing_info", "answers"],
        },
    },
}


def content_hash(subject: str | None, body: str, cover_letter: str | None) -> str:
    return hashlib.sha256(f"{subject or ''}\x00{body}\x00{cover_letter or ''}".encode()).hexdigest()


def build_prompt(job: dict, profile: dict, resume_text: str, additional_info: str, include_cover_letter: bool, questions: list[str]) -> str:
    q_block = "\n".join(f"- {q}" for q in questions) if questions else "(none supplied)"
    return f"""Write application materials for this candidate and this job.

CANDIDATE RESUME:
{resume_text[:8000]}

CANDIDATE PROFILE NOTES: {profile.get("summary") or ""}
Additional information from the candidate: {additional_info.strip()[:1500] or "none"}
Candidate name: {profile.get("name") or "(not given)"}

JOB: {job.get("title")} at {job.get("company")} ({job.get("location") or "location not stated"})
{llm.untrusted("job_description", job.get("description") or "", 6000)}

APPLICATION QUESTIONS TO ANSWER:
{q_block}

Cover letter requested: {"yes" if include_cover_letter else "no"}

Hard rules:
- Never invent employment history, projects, achievements, metrics, certifications or skills.
  Every concrete claim must be traceable to the resume or profile notes above.
- Where the posting wants something the candidate hasn't documented, don't claim it -- list it in missing_info.
- Plain professional tone, no clichés ("I am thrilled", "passionate"), no placeholders like [Company].
Call submit_application_package."""


def generate(job: dict, profile: dict, resume_text: str, evidence: str, additional_info: str,
             include_cover_letter: bool, questions: list[str]) -> tuple[dict, str]:
    raw, model = llm.call_tool(
        build_prompt(job, profile, resume_text, additional_info, include_cover_letter, questions), PACKAGE_TOOL, 2500
    )
    qualifications = []
    unsupported = []
    for q in raw.get("qualifications") or []:
        if not isinstance(q, dict) or not q.get("requirement"):
            continue
        if llm.grounded(q.get("evidence", ""), evidence, 0.5):
            qualifications.append({"requirement": q["requirement"], "evidence": q.get("evidence", "")})
        else:
            unsupported.append(q["requirement"])
    missing = [m for m in raw.get("missing_info") or [] if isinstance(m, str)]
    missing += [f"Confirm before claiming: {r}" for r in unsupported]
    answers = [
        {"question": a.get("question", ""), "answer": a.get("answer"), "needs_input": bool(a.get("needs_input") or not a.get("answer"))}
        for a in raw.get("answers") or [] if isinstance(a, dict)
    ]
    package = {
        "subject": (raw.get("subject") or f"Application: {job.get('title')}").strip()[:300],
        "body": (raw.get("email_body") or "").strip(),
        "cover_letter": (raw.get("cover_letter") or None) if include_cover_letter else None,
        "qualifications": qualifications,
        "missing_info": missing,
        "answers": answers,
    }
    return package, model


def next_version(conn: Connection, owner: str, job_id: int) -> int:
    current = conn.execute(
        sa.select(sa.func.max(application_drafts.c.version)).where(
            application_drafts.c.owner == owner, application_drafts.c.job_id == job_id
        )
    ).scalar()
    return (current or 0) + 1


def save_version(conn: Connection, owner: str, job_id: int, origin: str, package: dict, *, parent_id: int | None = None,
                 resume_id: int | None = None, profile_version: int | None = None, model: str | None = None):
    """Inserts a new immutable version. Saving an edit identical to the
    latest version is a no-op that returns that version."""
    body = (package.get("body") or "").strip()
    if not body:
        raise ValueError("Draft body can't be empty.")
    digest = content_hash(package.get("subject"), body, package.get("cover_letter"))
    latest = latest_version(conn, owner, job_id)
    if latest is not None and origin == "edited" and latest.content_hash == digest:
        return latest
    # Serialise version numbering per (owner, job): concurrent saves would
    # otherwise race to the same version and one would hit the unique key.
    conn.execute(sa.text("select pg_advisory_xact_lock(hashtext(:k))"), {"k": f"draft:{owner}:{job_id}"})
    version = next_version(conn, owner, job_id)
    return conn.execute(
        sa.insert(application_drafts).values(
            owner=owner, job_id=job_id, version=version, origin=origin, parent_id=parent_id,
            subject=package.get("subject"), body=body, cover_letter=package.get("cover_letter"),
            qualifications=package.get("qualifications") or [], missing_info=package.get("missing_info") or [],
            answers=package.get("answers") or [], resume_id=resume_id, profile_version=profile_version,
            model=model, content_hash=digest, created_at=datetime.now(timezone.utc),
        ).returning(application_drafts)
    ).one()


def latest_version(conn: Connection, owner: str, job_id: int):
    return conn.execute(
        sa.select(application_drafts)
        .where(application_drafts.c.owner == owner, application_drafts.c.job_id == job_id)
        .order_by(application_drafts.c.version.desc()).limit(1)
    ).first()


def list_versions(conn: Connection, owner: str, job_id: int) -> list:
    return conn.execute(
        sa.select(application_drafts)
        .where(application_drafts.c.owner == owner, application_drafts.c.job_id == job_id)
        .order_by(application_drafts.c.version.desc())
    ).mappings().all()
