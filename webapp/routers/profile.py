"""Candidate profile + resumes."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile

from auth import CurrentUser
from deps import engine, limiter
from jobseeker import documents, llm, pipeline, profile as profile_mod
from jobseeker.database import resumes
from jobseeker.profile import CandidateProfile
from schemas import ProfileUpdate, ResumeApply, ResumeUpdate

router = APIRouter(prefix="/api/v2", tags=["profile"])

RESUME_PUBLIC_COLUMNS = [c for c in resumes.c if c.name not in ("extracted_text", "storage_key", "owner")]


@router.get("/profile")
def get_profile(user: CurrentUser):
    with engine().begin() as conn:
        return profile_mod.get_or_seed(conn, user.account)


@router.put("/profile")
def save_profile(body: ProfileUpdate, user: CurrentUser):
    with engine().begin() as conn:
        try:
            saved = profile_mod.save(conn, user.account, body.data, body.additional_info, body.version)
        except profile_mod.VersionConflict as e:
            raise HTTPException(409, f"Your profile was changed elsewhere (now version {e.current_version}). Reload and re-apply your edits.") from e
        rescored = pipeline.rescore_all(conn, user.account)
    return {**saved, "rescored_jobs": rescored}


# ---------------------------------------------------------------- resumes


def _owned_resume(conn, account: str, resume_id: int):
    row = conn.execute(sa.select(resumes).where(
        resumes.c.id == resume_id, resumes.c.owner == account, resumes.c.deleted_at.is_(None))).first()
    if row is None:
        raise HTTPException(404, "Resume not found")
    return row


def _public(conn, resume_id: int) -> dict:
    return dict(conn.execute(sa.select(*RESUME_PUBLIC_COLUMNS).where(resumes.c.id == resume_id)).mappings().one())


@router.get("/resumes")
def list_resumes(user: CurrentUser):
    with engine().begin() as conn:
        profile_mod.get_or_seed(conn, user.account)  # seeds the repo resume on first use
        rows = conn.execute(sa.select(*RESUME_PUBLIC_COLUMNS).where(
            resumes.c.owner == user.account, resumes.c.deleted_at.is_(None))
            .order_by(resumes.c.is_default.desc(), resumes.c.uploaded_at.desc())).mappings().all()
    return {"items": [dict(r) for r in rows], "max_bytes": documents.MAX_UPLOAD_BYTES, "llm_configured": llm.is_configured()}


def store_resume(conn, account: str, data: bytes, filename: str, display_name: str, purpose: str | None,
                 make_default: bool, replaces_id: int | None = None, source: str = "upload",
                 drive_file_id: str | None = None, drive_modified_time=None) -> int:
    """Validates, stores privately, extracts text. Never overwrites: a new
    upload of an existing resume becomes a new version row."""
    content_type = documents.detect_type(data, filename)
    try:
        text, status, error = documents.extract_text(data, content_type), "done", None
    except documents.InvalidDocument as e:
        text, status, error = None, "failed", str(e)
    version = 1
    if replaces_id is not None:
        previous = _owned_resume(conn, account, replaces_id)
        version = previous.version + 1
        display_name = display_name or previous.display_name
        purpose = purpose if purpose is not None else previous.purpose
    display_name = display_name or filename
    key = f"{account}/resumes/{uuid.uuid4().hex}{_extension(content_type)}"
    documents.storage_from_env().put(key, data, content_type)
    has_default = conn.execute(sa.select(resumes.c.id).where(
        resumes.c.owner == account, resumes.c.is_default.is_(True), resumes.c.deleted_at.is_(None))).first()
    make_default = make_default or has_default is None
    if make_default:
        conn.execute(sa.update(resumes).where(resumes.c.owner == account).values(is_default=False))
    return conn.execute(sa.insert(resumes).values(
        owner=account, display_name=display_name[:200], purpose=purpose, version=version,
        filename=filename[:255], content_type=content_type, size_bytes=len(data), sha256=documents.sha256(data),
        storage_key=key, source=source, drive_file_id=drive_file_id, drive_modified_time=drive_modified_time,
        is_default=make_default, extraction_status=status, extraction_error=error, extracted_text=text,
    ).returning(resumes.c.id)).scalar_one()


def _extension(content_type: str) -> str:
    return {documents.PDF: ".pdf", documents.DOCX: ".docx", documents.TEXT: ".txt"}.get(content_type, "")


@router.post("/resumes", status_code=201)
async def upload_resume(user: CurrentUser, file: UploadFile = File(...), display_name: str = Form(""),
                        purpose: str | None = Form(None), make_default: bool = Form(False),
                        replaces_id: int | None = Form(None)):
    data = await file.read(documents.MAX_UPLOAD_BYTES + 1)
    with engine().begin() as conn:
        try:
            resume_id = store_resume(conn, user.account, data, file.filename or "resume", display_name.strip(),
                                     purpose, make_default, replaces_id)
        except documents.InvalidDocument as e:
            raise HTTPException(422, str(e)) from e
        return _public(conn, resume_id)


@router.patch("/resumes/{resume_id}")
def update_resume(resume_id: int, body: ResumeUpdate, user: CurrentUser):
    with engine().begin() as conn:
        _owned_resume(conn, user.account, resume_id)
        fields = body.model_dump(exclude_unset=True)
        if fields.get("is_default"):
            conn.execute(sa.update(resumes).where(resumes.c.owner == user.account).values(is_default=False))
        elif fields.get("is_default") is False:
            fields.pop("is_default")  # there's always a default; choose another instead
        if fields:
            conn.execute(sa.update(resumes).where(resumes.c.id == resume_id).values(**fields))
        return _public(conn, resume_id)


@router.delete("/resumes/{resume_id}", status_code=204)
def delete_resume(resume_id: int, user: CurrentUser):
    """Soft delete: the row stays for drafts/applications that reference
    it; the stored file is removed."""
    with engine().begin() as conn:
        row = _owned_resume(conn, user.account, resume_id)
        conn.execute(sa.update(resumes).where(resumes.c.id == resume_id).values(
            deleted_at=datetime.now(timezone.utc), is_default=False))
        if row.is_default:
            nxt = conn.execute(sa.select(resumes.c.id).where(resumes.c.owner == user.account, resumes.c.deleted_at.is_(None))
                               .order_by(resumes.c.uploaded_at.desc()).limit(1)).scalar()
            if nxt:
                conn.execute(sa.update(resumes).where(resumes.c.id == nxt).values(is_default=True))
    if row.storage_key:
        try:
            documents.storage_from_env().delete(row.storage_key)
        except Exception:
            pass  # the row is already detached; an orphaned private object is harmless


@router.get("/resumes/{resume_id}/file")
def download_resume(resume_id: int, user: CurrentUser):
    with engine().begin() as conn:
        row = _owned_resume(conn, user.account, resume_id)
    if not row.storage_key:
        return Response(row.extracted_text or "", media_type="text/plain; charset=utf-8")
    data = documents.storage_from_env().get(row.storage_key)
    safe_name = "".join(ch for ch in row.filename if ch.isalnum() or ch in "._- ") or "resume"
    return Response(data, media_type=row.content_type, headers={"Content-Disposition": f'attachment; filename="{safe_name}"'})


@router.post("/resumes/{resume_id}/extract")
def extract(resume_id: int, user: CurrentUser):
    """Structured facts from the resume -- a proposal; nothing is applied
    to the profile until the user chooses sections via /apply."""
    limiter.check(user.account, "resume extraction", 5)
    with engine().begin() as conn:
        row = _owned_resume(conn, user.account, resume_id)
    if not row.extracted_text:
        raise HTTPException(422, row.extraction_error or "No text could be extracted from this file.")
    try:
        proposal = documents.extract_profile(row.extracted_text)
    except Exception as e:
        raise HTTPException(502, f"Extraction failed: {type(e).__name__}") from e
    with engine().begin() as conn:
        conn.execute(sa.update(resumes).where(resumes.c.id == resume_id).values(extracted_profile=proposal))
        return _public(conn, resume_id)


@router.post("/resumes/{resume_id}/apply")
def apply_to_profile(resume_id: int, body: ResumeApply, user: CurrentUser):
    with engine().begin() as conn:
        row = _owned_resume(conn, user.account, resume_id)
        proposal = row.extracted_profile or {}
        if not proposal:
            raise HTTPException(422, "Run extraction first.")
        record = profile_mod.get_or_seed(conn, user.account)
        data = record["data"]
        for section in body.sections:
            if section == "skills":
                have = {s["name"].lower() for s in data["skills"]}
                data["skills"] += [{"name": s, "source": "resume"} for s in proposal.get("skills", []) if s.lower() not in have]
            elif section in ("experience", "projects", "education", "certifications"):
                existing = {str(sorted(e.items())) for e in data[section]}
                data[section] += [e for e in proposal.get(section, []) if str(sorted(e.items())) not in existing]
            elif proposal.get(section) not in (None, ""):
                data[section] = proposal[section]
        data["evidence_sources"] = [e for e in data.get("evidence_sources", []) if e.get("resume_id") != resume_id] + [{
            "type": "resume", "resume_id": resume_id, "name": row.display_name, "version": row.version,
            "sections": list(body.sections), "applied_at": datetime.now(timezone.utc).isoformat(),
        }]
        saved = profile_mod.save(conn, user.account, CandidateProfile.model_validate(data),
                                 record["additional_info"], record["version"])
        pipeline.rescore_all(conn, user.account)
        return saved
