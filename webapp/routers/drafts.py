"""Application drafts: generate (LLM), edit (new version), save to Gmail.

States are kept distinct end to end:
  generated in JobLookup -> application_drafts row (origin generated/edited)
  saved in Gmail         -> gmail_status 'saved' + gmail_draft_id
  missing from Gmail     -> gmail_status 'missing' (sent OR deleted; we can't tell which)
  applied                -> only when the user marks the application applied
"""

from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException

from auth import CurrentUser
from deps import engine, limiter
from jobseeker import drafting, google, lifecycle, llm, profile as profile_mod
from jobseeker.database import application_drafts, jobs, knowledge_documents
from routers.applications import _initial_status, _job_or_404
from schemas import DraftEdit, DraftGenerate, GmailSave

router = APIRouter(prefix="/api/v2", tags=["drafts"])


def _serialize(row) -> dict:
    d = dict(row._mapping if hasattr(row, "_mapping") else row)
    d["gmail_open_url"] = google.gmail_open_url(d.get("gmail_message_id")) if d.get("gmail_status") == "saved" else None
    return d


@router.get("/jobs/{job_id}/drafts")
def list_drafts(job_id: int, user: CurrentUser):
    with engine().begin() as conn:
        _job_or_404(conn, job_id)
        return {"items": [_serialize(r) for r in drafting.list_versions(conn, user.account, job_id)],
                "llm_configured": llm.is_configured()}


def _knowledge_text(conn, account: str) -> str:
    rows = conn.execute(sa.select(knowledge_documents.c.name, knowledge_documents.c.extracted_text).where(
        knowledge_documents.c.owner == account, knowledge_documents.c.deleted_at.is_(None),
        knowledge_documents.c.status == "done")).all()
    return "\n\n".join(f"[{r.name}]\n{(r.extracted_text or '')[:3000]}" for r in rows)


@router.post("/jobs/{job_id}/drafts/generate", status_code=201)
def generate_draft(job_id: int, body: DraftGenerate, user: CurrentUser):
    if not llm.is_configured():
        raise HTTPException(503, "No LLM provider key is configured on the server (e.g. OPENAI_API_KEY).")
    limiter.check(user.account, "draft generation", 10)
    with engine().begin() as conn:
        job = conn.execute(sa.select(jobs).where(jobs.c.id == job_id)).mappings().first()
        if job is None:
            raise HTTPException(404, "Job not found")
        record = profile_mod.get_or_seed(conn, user.account)
        try:
            resume_id, resume = profile_mod.resume_text(conn, user.account, body.resume_id)
        except LookupError as e:
            raise HTTPException(404, "Resume not found") from e
        knowledge = _knowledge_text(conn, user.account)
    if not resume.strip():
        raise HTTPException(422, "The selected resume has no extracted text yet.")
    evidence = profile_mod.evidence_text(record["data"], resume) + "\n" + knowledge
    additional = (record["additional_info"] or "") + (f"\n\nOther career documents:\n{knowledge[:4000]}" if knowledge else "")
    try:
        package, model = drafting.generate(dict(job), record["data"], resume, evidence, additional,
                                           body.include_cover_letter, body.questions)
    except Exception as e:
        raise HTTPException(502, f"The language model call failed: {type(e).__name__}. Try again.") from e
    with engine().begin() as conn:
        row = drafting.save_version(conn, user.account, job_id, "generated", package, resume_id=resume_id,
                                    profile_version=record["version"], model=model)
        job_row = _job_or_404(conn, job_id)
        app = lifecycle.ensure(conn, user.account, job_id, _initial_status(conn, user.account, job_row))
        if app.status in lifecycle.DRAFT_ADVANCES_FROM:
            lifecycle.transition(conn, user.account, job_id, "draft_ready", app.status, "Draft generated")
        return _serialize(row)


@router.post("/jobs/{job_id}/drafts", status_code=201)
def save_edit(job_id: int, body: DraftEdit, user: CurrentUser):
    with engine().begin() as conn:
        _job_or_404(conn, job_id)
        parent = None
        if body.parent_id is not None:
            parent = conn.execute(sa.select(application_drafts).where(
                application_drafts.c.id == body.parent_id, application_drafts.c.owner == user.account,
                application_drafts.c.job_id == job_id)).first()
            if parent is None:
                raise HTTPException(404, "Parent draft not found")
        package = {
            "subject": body.subject, "body": body.body, "cover_letter": body.cover_letter,
            "qualifications": parent.qualifications if parent else [], "missing_info": parent.missing_info if parent else [],
            "answers": parent.answers if parent else [],
        }
        try:
            row = drafting.save_version(conn, user.account, job_id, "edited", package, parent_id=body.parent_id,
                                        resume_id=parent.resume_id if parent else None)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        return _serialize(row)


def _owned_draft(conn, account: str, draft_id: int):
    row = conn.execute(sa.select(application_drafts).where(
        application_drafts.c.id == draft_id, application_drafts.c.owner == account)).first()
    if row is None:
        raise HTTPException(404, "Draft not found")
    return row


@router.post("/drafts/{draft_id}/gmail")
def save_to_gmail(draft_id: int, body: GmailSave, user: CurrentUser):
    """Saves (never sends) this version as a Gmail draft. One Gmail draft
    per job: later versions update it instead of creating duplicates."""
    limiter.check(user.account, "Gmail", 10)
    with engine().begin() as conn:
        draft = _owned_draft(conn, user.account, draft_id)
        if draft.gmail_status == "saved" and draft.gmail_draft_id:
            return _serialize(draft)  # this exact version is already in Gmail
        existing = conn.execute(sa.select(application_drafts.c.gmail_draft_id).where(
            application_drafts.c.owner == user.account, application_drafts.c.job_id == draft.job_id,
            application_drafts.c.gmail_draft_id.is_not(None), application_drafts.c.gmail_status == "saved",
        ).order_by(application_drafts.c.gmail_saved_at.desc()).limit(1)).scalar()
        body_text = draft.body + (f"\n\n---\n\n{draft.cover_letter}" if draft.cover_letter else "")
        now = datetime.now(timezone.utc)
        try:
            saved = google.save_gmail_draft(conn, user.account, draft.subject or "Application", body_text, body.to, existing)
        except google.IntegrationError as e:
            failure = e  # recorded below in its own transaction, which the raise can't roll back
        else:
            failure = None
            # The Gmail draft now holds this version; older versions no longer do.
            conn.execute(sa.update(application_drafts).where(
                application_drafts.c.owner == user.account, application_drafts.c.job_id == draft.job_id,
                application_drafts.c.id != draft_id, application_drafts.c.gmail_status == "saved",
            ).values(gmail_status="not_saved", gmail_draft_id=None, gmail_message_id=None))
            conn.execute(sa.update(application_drafts).where(application_drafts.c.id == draft_id).values(
                gmail_status="saved", gmail_draft_id=saved["draft_id"], gmail_message_id=saved["message_id"],
                gmail_saved_at=now, gmail_error=None))
            return _serialize(_owned_draft(conn, user.account, draft_id))
    with engine().begin() as conn:
        conn.execute(sa.update(application_drafts).where(application_drafts.c.id == draft_id).values(
            gmail_status="failed", gmail_error=str(failure)))
    status = 400 if failure.code in ("not_connected", "not_configured", "reconnect") else 502
    raise HTTPException(status, {"message": str(failure), "code": failure.code})


@router.post("/drafts/{draft_id}/gmail/refresh")
def refresh_gmail_status(draft_id: int, user: CurrentUser):
    with engine().begin() as conn:
        draft = _owned_draft(conn, user.account, draft_id)
        if draft.gmail_status != "saved" or not draft.gmail_draft_id:
            return _serialize(draft)
        exists = google.gmail_draft_exists(conn, user.account, draft.gmail_draft_id)
        if exists is False:
            conn.execute(sa.update(application_drafts).where(application_drafts.c.id == draft_id).values(
                gmail_status="missing", gmail_error="No longer in Gmail drafts: it was sent or deleted there."))
        return _serialize(_owned_draft(conn, user.account, draft_id))
