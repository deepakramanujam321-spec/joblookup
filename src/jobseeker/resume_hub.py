"""Resume storage + the "resume hub": a Google Drive folder shared as
"anyone with the link" that JobLookup keeps in sync with its resumes.

Sync rules (idempotent; safe to run every day):
  * new file in the folder        -> new resume (source 'drive')
  * file whose content changed    -> new version of that resume (old kept)
  * unchanged file                -> nothing
  * resume deleted in JobLookup   -> never re-imported
  * file removed from the folder  -> resume kept (your drafts may use it)
On the first sync, if the default resume is still the repo seed, the
hub's most recently modified file becomes the default.

Only Drive folder/file ids are taken from user input; every URL fetched is
built here from a validated id (no arbitrary URLs, so no SSRF). Public
folders need no Google login or API key.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests
import sqlalchemy as sa
from bs4 import BeautifulSoup
from sqlalchemy.engine import Connection

from . import documents
from .database import audit_log, resumes

_ID = r"[A-Za-z0-9_-]{10,}"
SUPPORTED = (".pdf", ".docx", ".txt")
USER_AGENT = "Mozilla/5.0 (compatible; joblookup/2.0)"
TIMEOUT = 30


class HubError(RuntimeError):
    pass


# ------------------------------------------------------------------ storage


def store_resume(conn: Connection, account: str, data: bytes, filename: str, display_name: str, purpose: str | None,
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
        previous = conn.execute(sa.select(resumes).where(resumes.c.id == replaces_id, resumes.c.owner == account)).first()
        if previous is None:
            raise LookupError("resume not found")
        version = previous.version + 1
        display_name = display_name or previous.display_name
        purpose = purpose if purpose is not None else previous.purpose
        make_default = make_default or previous.is_default
        conn.execute(sa.update(resumes).where(resumes.c.id == replaces_id).values(is_default=False))
    display_name = display_name or filename
    extension = {documents.PDF: ".pdf", documents.DOCX: ".docx", documents.TEXT: ".txt"}.get(content_type, "")
    key = f"{account}/resumes/{uuid.uuid4().hex}{extension}"
    documents.put_file(conn, key, account, data, content_type)
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


# ------------------------------------------------------------ public Drive


def folder_id(url_or_id: str) -> str:
    text = (url_or_id or "").strip()
    match = re.search(rf"/folders/({_ID})", text) or re.search(rf"[?&]id=({_ID})", text) or re.fullmatch(f"({_ID})", text)
    if not match:
        raise HubError("That doesn't look like a Google Drive folder link (…/drive/folders/<id>).")
    return match.group(1)


@dataclass
class HubFile:
    id: str
    name: str
    kind: str  # "file" | "gdoc"
    modified: str = ""


def parse_folder_listing(html: str) -> list[HubFile]:
    """Google's embeddable folder view: one `.flip-entry` per item."""
    soup = BeautifulSoup(html, "html.parser")
    files = []
    for entry in soup.select(".flip-entry"):
        link = entry.find("a", href=True)
        title = entry.select_one(".flip-entry-title")
        if not link or not title:
            continue
        href = link["href"]
        gdoc = re.search(rf"docs\.google\.com/document/d/({_ID})", href)
        plain = re.search(rf"/file/d/({_ID})", href)
        name = title.get_text(strip=True)
        modified = entry.select_one(".flip-entry-last-modified")
        if gdoc:
            files.append(HubFile(gdoc.group(1), name, "gdoc", modified.get_text(strip=True) if modified else ""))
        elif plain and name.lower().endswith(SUPPORTED):
            files.append(HubFile(plain.group(1), name, "file", modified.get_text(strip=True) if modified else ""))
    return files


def list_folder(fid: str) -> list[HubFile]:
    resp = requests.get(f"https://drive.google.com/embeddedfolderview?id={fid}", headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    if resp.status_code == 404:
        raise HubError("Folder not found. Check the link.")
    if resp.status_code != 200 or "flip-entry" not in resp.text and "flip-list" not in resp.text:
        raise HubError("Couldn't read the folder. Make sure it's shared as “Anyone with the link can view”.")
    return parse_folder_listing(resp.text)


def download(file: HubFile) -> tuple[bytes, str]:
    """(bytes, filename). Google Docs are exported as DOCX."""
    if not re.fullmatch(_ID, file.id):
        raise HubError("invalid file id")
    if file.kind == "gdoc":
        url = f"https://docs.google.com/document/d/{file.id}/export?format=docx"
        filename = file.name if file.name.lower().endswith(".docx") else f"{file.name}.docx"
    else:
        url = f"https://drive.google.com/uc?export=download&id={file.id}"
        filename = file.name
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60, allow_redirects=True)
    if resp.status_code != 200 or resp.headers.get("content-type", "").startswith("text/html"):
        raise HubError(f"Couldn't download “{file.name}” (is it shared with the link?).")
    if len(resp.content) > documents.MAX_UPLOAD_BYTES:
        raise HubError(f"“{file.name}” is larger than {documents.MAX_UPLOAD_BYTES // 1048576} MB.")
    return resp.content, filename


# --------------------------------------------------------------------- sync


@dataclass
class SyncResult:
    imported: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    default_set_to: str | None = None

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def sync(conn: Connection, account: str, folder_url: str, *, lister=None, downloader=None) -> SyncResult:
    lister, downloader = lister or list_folder, downloader or download  # resolved at call time (patchable)
    fid = folder_id(folder_url)
    result = SyncResult()
    stored: list[tuple[HubFile, int]] = []
    for file in lister(fid):
        latest = conn.execute(
            sa.select(resumes).where(resumes.c.owner == account, resumes.c.drive_file_id == file.id)
            .order_by(resumes.c.version.desc()).limit(1)
        ).first()
        if latest is not None and latest.deleted_at is not None:
            result.skipped.append(f"{file.name} (you deleted it)")
            continue
        try:
            data, filename = downloader(file)
            if latest is not None and latest.sha256 == documents.sha256(data):
                result.unchanged.append(file.name)
                continue
            rid = store_resume(
                conn, account, data, filename, re.sub(r"\.(pdf|docx|txt)$", "", file.name, flags=re.I),
                "From your resume hub folder", False, latest.id if latest else None, "drive", file.id,
            )
        except (HubError, documents.InvalidDocument, requests.RequestException) as e:
            result.failed.append(f"{file.name}: {e}")
            continue
        (result.updated if latest else result.imported).append(file.name)
        stored.append((file, rid))

    current_default = conn.execute(sa.select(resumes.c.source).where(
        resumes.c.owner == account, resumes.c.is_default.is_(True), resumes.c.deleted_at.is_(None))).scalar()
    if stored and current_default in (None, "seed"):
        chosen, rid = pick_default(stored)
        conn.execute(sa.update(resumes).where(resumes.c.owner == account).values(is_default=False))
        conn.execute(sa.update(resumes).where(resumes.c.id == rid).values(is_default=True))
        result.default_set_to = chosen.name

    conn.execute(sa.insert(audit_log).values(
        owner=account, action="resume_hub.sync", target_type="drive_folder", target_id=fid,
        detail={**result.as_dict(), "at": datetime.now(timezone.utc).isoformat()},
    ))
    return result


_DATE_FORMATS = ("%b %d, %Y", "%d %b %Y", "%m/%d/%y", "%m/%d/%Y", "%Y-%m-%d", "%b %d")


def _parse_modified(text: str) -> datetime | None:
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue
        if fmt == "%b %d":  # Drive omits the year for dates in the current year
            parsed = parsed.replace(year=datetime.now().year)
        return parsed
    return None


def pick_default(stored: list[tuple[HubFile, int]]) -> tuple[HubFile, int]:
    """Most recently modified file; ties or unknown dates prefer names that
    look like a primary resume ("master", then "resume" but not "template");
    remaining ties go to the first listed."""
    def rank(item):
        file, _ = item
        modified = _parse_modified(file.modified) or datetime.min
        lowered = file.name.lower()
        hint = 2 if "master" in lowered else 1 if "resume" in lowered and "template" not in lowered else 0
        return (modified, hint)
    return max(stored, key=rank)


def last_sync(conn: Connection, account: str) -> dict | None:
    row = conn.execute(sa.select(audit_log.c.detail, audit_log.c.created_at).where(
        audit_log.c.owner == account, audit_log.c.action == "resume_hub.sync",
    ).order_by(audit_log.c.created_at.desc(), audit_log.c.id.desc()).limit(1)).first()
    return {**(row.detail or {}), "synced_at": row.created_at} if row else None
