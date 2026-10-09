"""Resume / career-document handling: validation, text extraction, private
file storage, and structured profile extraction.

Uploads are untrusted: type is decided by magic bytes (not the filename or
the browser's Content-Type), size is capped, DOCX archives are checked for
the expected structure and for zip bombs, and extracted text is only ever
passed to the model inside an untrusted-document block.

File bytes live in Postgres (jobseeker.document_blobs), written in the
same transaction as the resume row that references them -- no second
storage service, no second set of credentials, no orphaned uploads. Files
are never served from a public URL; downloads go through the authenticated
API.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile

import sqlalchemy as sa

from . import llm, normalize
from .database import document_blobs

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_DOCX_UNCOMPRESSED = 30 * 1024 * 1024
PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TEXT = "text/plain"


class InvalidDocument(ValueError):
    pass


def detect_type(data: bytes, filename: str) -> str:
    if len(data) > MAX_UPLOAD_BYTES:
        raise InvalidDocument(f"File is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    if not data:
        raise InvalidDocument("File is empty.")
    if data.startswith(b"%PDF-"):
        return PDF
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = set(zf.namelist())
                if "word/document.xml" not in names:
                    raise InvalidDocument("ZIP file isn't a Word document.")
                if sum(i.file_size for i in zf.infolist()) > MAX_DOCX_UNCOMPRESSED:
                    raise InvalidDocument("Document expands to an unreasonable size.")
                if any(n.lower().endswith(("vbaproject.bin",)) for n in names):
                    raise InvalidDocument("Macro-enabled documents aren't accepted.")
        except zipfile.BadZipFile as e:
            raise InvalidDocument("Corrupt DOCX file.") from e
        return DOCX
    if filename.lower().endswith(".txt"):
        try:
            data.decode("utf-8")
            return TEXT
        except UnicodeDecodeError as e:
            raise InvalidDocument("Text file isn't valid UTF-8.") from e
    raise InvalidDocument("Only PDF, DOCX or plain-text files are accepted.")


def extract_text(data: bytes, content_type: str) -> str:
    if content_type == PDF:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise InvalidDocument("Password-protected PDFs can't be read.")
        text = "\n".join((page.extract_text() or "") for page in reader.pages[:30])
    elif content_type == DOCX:
        import docx

        document = docx.Document(io.BytesIO(data))
        parts = [p.text for p in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text for cell in row.cells))
        text = "\n".join(parts)
    else:
        text = data.decode("utf-8", errors="replace")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) < 50:
        raise InvalidDocument("Couldn't find readable text in this file (is it a scanned image?).")
    return text[:100_000]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ------------------------------------------------------------------ storage


def put_file(conn, key: str, owner: str, data: bytes, content_type: str) -> None:
    conn.execute(sa.insert(document_blobs).values(
        key=key, owner=owner, content=data, content_type=content_type, size_bytes=len(data)))


def get_file(conn, key: str, owner: str) -> bytes | None:
    return conn.execute(sa.select(document_blobs.c.content).where(
        document_blobs.c.key == key, document_blobs.c.owner == owner)).scalar()


def delete_file(conn, key: str, owner: str) -> None:
    conn.execute(sa.delete(document_blobs).where(document_blobs.c.key == key, document_blobs.c.owner == owner))


# ------------------------------------------------------ profile extraction

RESUME_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_resume_profile",
        "description": "Submit structured facts extracted from the resume.",
        "parameters": {
            "type": "object",
            "properties": {
                "headline": {"type": "string"},
                "summary": {"type": "string", "description": "2-3 sentences, factual, from the resume only."},
                "total_experience_years": {"type": ["number", "null"], "description": "Only if computable from stated dates."},
                "experience": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"}, "company": {"type": "string"},
                            "start": {"type": ["string", "null"]}, "end": {"type": ["string", "null"]},
                            "summary": {"type": "string"}, "technologies": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["title", "company", "start", "end", "summary", "technologies"],
                    },
                },
                "skills": {"type": "array", "items": {"type": "string"}},
                "projects": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}, "description": {"type": "string"},
                                       "achievements": {"type": "array", "items": {"type": "string"}}},
                        "required": ["name", "description", "achievements"],
                    },
                },
                "education": {
                    "type": "array",
                    "items": {"type": "object", "properties": {"institution": {"type": "string"}, "degree": {"type": "string"},
                                                                "year": {"type": ["string", "null"]}},
                              "required": ["institution", "degree", "year"]},
                },
                "certifications": {
                    "type": "array",
                    "items": {"type": "object", "properties": {"name": {"type": "string"}, "issuer": {"type": "string"},
                                                                "year": {"type": ["string", "null"]}},
                              "required": ["name", "issuer", "year"]},
                },
            },
            "required": ["headline", "summary", "total_experience_years", "experience", "skills", "projects", "education", "certifications"],
        },
    },
}


def extract_profile(text: str) -> dict:
    """Structured facts from a resume: a *proposal* the user reviews before
    anything is applied to their profile. Works without an LLM (skills only)."""
    vocab_skills = normalize.extract_skills(text)
    if not llm.is_configured():
        return {"skills": vocab_skills, "method": "vocabulary"}
    prompt = (
        "Extract only facts explicitly stated in this resume. Do not infer, embellish or add skills that "
        "aren't written in it. Leave fields empty when the resume doesn't say.\n\n"
        + llm.untrusted("resume", text, 12000)
        + "\n\nCall submit_resume_profile."
    )
    raw, model = llm.call_tool(prompt, RESUME_TOOL, 3000)
    skills = [s for s in raw.get("skills") or [] if isinstance(s, str) and s.lower() in text.lower()]
    raw["skills"] = sorted(set(skills) | set(vocab_skills), key=str.lower)
    raw["method"] = model
    return raw
