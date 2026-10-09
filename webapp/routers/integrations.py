"""Google integrations (optional): connect/disconnect, Drive import."""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from auth import CurrentUser
from deps import engine, limiter
from jobseeker import documents, google, normalize
from jobseeker.database import knowledge_documents, resumes
from jobseeker.resume_hub import store_resume
from routers.profile import _owned_resume, _public
from schemas import DriveImport

router = APIRouter(prefix="/api/v2", tags=["integrations"])


def _raise(e: google.IntegrationError):
    status = {"not_configured": 503, "not_connected": 400, "reconnect": 400, "not_found": 404, "unsupported": 422}.get(e.code, 502)
    raise HTTPException(status, {"message": str(e), "code": e.code}) from e


@router.get("/integrations")
def status(user: CurrentUser):
    with engine().begin() as conn:
        return google.status(conn, user.account)


@router.post("/integrations/google/{service}/connect")
def connect(service: str, user: CurrentUser):
    if service not in google.SCOPES:
        raise HTTPException(404, "Unknown service")
    with engine().begin() as conn:
        try:
            return {"authorization_url": google.start_authorization(conn, user.account, service)}
        except google.IntegrationError as e:
            _raise(e)


@router.get("/integrations/google/callback", include_in_schema=False)
def callback(user: CurrentUser, state: str = "", code: str = "", error: str = ""):
    if error:
        return RedirectResponse(f"/settings?integration_error={quote(error)}", status_code=303)
    try:
        with engine().begin() as conn:
            service = google.complete_authorization(conn, user.account, state, code)
    except google.IntegrationError as e:
        return RedirectResponse(f"/settings?integration_error={quote(str(e))}", status_code=303)
    return RedirectResponse(f"/settings?connected={service}", status_code=303)


@router.delete("/integrations/google/{service}", status_code=204)
def disconnect(service: str, user: CurrentUser, delete_imported: bool = False):
    if service not in google.SCOPES:
        raise HTTPException(404, "Unknown service")
    with engine().begin() as conn:
        google.disconnect(conn, user.account, service)
        if service == "drive" and delete_imported:
            now = datetime.now(timezone.utc)
            conn.execute(sa.update(knowledge_documents).where(knowledge_documents.c.owner == user.account)
                         .values(deleted_at=now, extracted_text=None))


@router.get("/integrations/drive/picker")
def picker_config(user: CurrentUser):
    """Config for Google's Drive Picker. The access token here is the one
    intentional exception to "tokens stay server-side": the Picker runs in
    the browser by design. It is short-lived (<=1h) and limited to
    drive.file -- it can only reach files the user picks."""
    cfg = google.client_config()
    if not cfg or not cfg.get("api_key") or not cfg.get("app_id"):
        raise HTTPException(503, "Drive Picker needs GOOGLE_API_KEY and GOOGLE_APP_ID on the server.")
    limiter.check(user.account, "Drive", 20)
    with engine().begin() as conn:
        try:
            token = google.access_token(conn, user.account, "drive")
        except google.IntegrationError as e:
            _raise(e)
    return {"api_key": cfg["api_key"], "app_id": cfg["app_id"], "client_id": cfg["client_id"], "access_token": token}


@router.post("/integrations/drive/import")
def drive_import(body: DriveImport, user: CurrentUser):
    limiter.check(user.account, "Drive", 20)
    results = []
    for file_id in body.file_ids:
        try:
            with engine().begin() as conn:
                info, data, content_type = google.drive_file(conn, user.account, file_id)
                modified = normalize.parse_datetime(info.get("modifiedTime"))
                if body.kind == "resume":
                    prior = conn.execute(sa.select(resumes.c.id).where(
                        resumes.c.owner == user.account, resumes.c.drive_file_id == file_id, resumes.c.deleted_at.is_(None))
                        .order_by(resumes.c.version.desc()).limit(1)).scalar()
                    filename = info["name"] if "." in info["name"] else f"{info['name']}.docx"
                    rid = store_resume(conn, user.account, data, filename, info["name"], "Imported from Google Drive",
                                       False, prior, "drive", file_id, modified)
                    results.append({"file_id": file_id, "ok": True, "resume": _public(conn, rid)})
                else:
                    results.append({"file_id": file_id, "ok": True,
                                    "document": _upsert_knowledge(conn, user.account, file_id, info, data, content_type, modified)})
        except google.IntegrationError as e:
            results.append({"file_id": file_id, "ok": False, "error": str(e)})
        except documents.InvalidDocument as e:
            results.append({"file_id": file_id, "ok": False, "error": str(e)})
    return {"results": results}


def _upsert_knowledge(conn, account, file_id, info, data, content_type, modified) -> dict:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    try:
        documents.detect_type(data, info["name"] + (".txt" if content_type == "text/plain" else ""))
        text, status, error = documents.extract_text(data, content_type), "done", None
    except documents.InvalidDocument as e:
        text, status, error = None, "failed", str(e)
    now = datetime.now(timezone.utc)
    values = dict(owner=account, source="drive", drive_file_id=file_id, name=info["name"][:300], mime_type=info.get("mimeType"),
                  drive_modified_time=modified, extracted_text=text, status=status, error=error, refreshed_at=now, deleted_at=None)
    row = conn.execute(pg_insert(knowledge_documents).values(**values).on_conflict_do_update(
        constraint="knowledge_documents_owner_file_key", set_={k: v for k, v in values.items() if k not in ("owner", "drive_file_id")},
    ).returning(*[c for c in knowledge_documents.c if c.name != "extracted_text"])).mappings().one()
    return {**dict(row), "chars": len(text or "")}


@router.get("/knowledge")
def list_knowledge(user: CurrentUser):
    with engine().begin() as conn:
        rows = conn.execute(sa.select(*[c for c in knowledge_documents.c if c.name != "extracted_text"],
                                      sa.func.length(knowledge_documents.c.extracted_text).label("chars"))
                            .where(knowledge_documents.c.owner == user.account, knowledge_documents.c.deleted_at.is_(None))
                            .order_by(knowledge_documents.c.imported_at.desc())).mappings().all()
    return {"items": [dict(r) for r in rows]}


@router.post("/knowledge/{doc_id}/refresh")
def refresh_knowledge(doc_id: int, user: CurrentUser):
    limiter.check(user.account, "Drive", 20)
    with engine().begin() as conn:
        row = conn.execute(sa.select(knowledge_documents).where(knowledge_documents.c.id == doc_id,
                           knowledge_documents.c.owner == user.account, knowledge_documents.c.deleted_at.is_(None))).first()
        if row is None:
            raise HTTPException(404, "Document not found")
        try:
            info, data, content_type = google.drive_file(conn, user.account, row.drive_file_id)
        except google.IntegrationError as e:
            _raise(e)
        modified = normalize.parse_datetime(info.get("modifiedTime"))
        if row.drive_modified_time and modified and modified <= row.drive_modified_time and row.status == "done":
            return {"changed": False}
        return {"changed": True, "document": _upsert_knowledge(conn, user.account, row.drive_file_id, info, data, content_type, modified)}


@router.delete("/knowledge/{doc_id}", status_code=204)
def delete_knowledge(doc_id: int, user: CurrentUser):
    """Removes JobLookup's copy only; the file in Drive is untouched."""
    with engine().begin() as conn:
        result = conn.execute(sa.update(knowledge_documents).where(
            knowledge_documents.c.id == doc_id, knowledge_documents.c.owner == user.account,
        ).values(deleted_at=datetime.now(timezone.utc), extracted_text=None))
        if result.rowcount == 0:
            raise HTTPException(404, "Document not found")


@router.post("/resumes/{resume_id}/refresh")
def refresh_drive_resume(resume_id: int, user: CurrentUser):
    """Re-imports a Drive resume as a new version if it changed in Drive."""
    limiter.check(user.account, "Drive", 20)
    with engine().begin() as conn:
        row = _owned_resume(conn, user.account, resume_id)
        if row.source != "drive" or not row.drive_file_id:
            raise HTTPException(422, "Only resumes imported from Google Drive can be refreshed.")
        try:
            info, data, _ = google.drive_file(conn, user.account, row.drive_file_id)
        except google.IntegrationError as e:
            _raise(e)
        modified = normalize.parse_datetime(info.get("modifiedTime"))
        if row.drive_modified_time and modified and modified <= row.drive_modified_time:
            return {"changed": False, "resume": _public(conn, resume_id)}
        filename = info["name"] if "." in info["name"] else f"{info['name']}.docx"
        new_id = store_resume(conn, user.account, data, filename, row.display_name, row.purpose, row.is_default,
                              resume_id, "drive", row.drive_file_id, modified)
        return {"changed": True, "resume": _public(conn, new_id)}
