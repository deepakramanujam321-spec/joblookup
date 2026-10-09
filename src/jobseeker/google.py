"""Google OAuth (Gmail drafts, Drive file import) -- optional integrations.

Scopes are the narrowest that do the job:
  gmail: gmail.compose  -- create/update drafts. Reading the mailbox is not
         requested, so JobLookup cannot read unrelated email.
  drive: drive.file     -- only files the user explicitly picks in Google's
         own Picker dialog (or that this app created). No crawling.
plus `openid email` to label which account is connected.

Each service is its own grant with its own stored tokens, so disconnecting
Drive leaves Gmail connected and vice versa. Tokens are encrypted at rest
(Fernet, key from TOKEN_ENCRYPTION_KEY) and never leave the server, with
one deliberate exception documented in the API: the Drive Picker runs in
the browser and needs a short-lived (<=1h) access token limited to
drive.file -- that's Google's prescribed design for per-file access.

Sending email is not implemented at all: drafts are saved to Gmail, the
user reviews and sends from Gmail themselves.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from urllib.parse import urlencode

import requests
import sqlalchemy as sa
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from .database import audit_log, oauth_integrations, oauth_states

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
SCOPES = {
    "gmail": ["openid", "email", "https://www.googleapis.com/auth/gmail.compose"],
    "drive": ["openid", "email", "https://www.googleapis.com/auth/drive.file"],
}
STATE_TTL = timedelta(minutes=15)
HTTP_TIMEOUT = 20


class IntegrationError(RuntimeError):
    """User-presentable failure (not configured, revoked, expired...)."""

    def __init__(self, message: str, code: str = "error"):
        super().__init__(message)
        self.code = code


# ------------------------------------------------------------------ config


def client_config() -> dict | None:
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    if not (client_id and client_secret):
        return None
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "api_key": os.environ.get("GOOGLE_API_KEY"),  # Drive Picker only
        "app_id": os.environ.get("GOOGLE_APP_ID"),  # project number; lets Picker grant drive.file access
    }


def redirect_uri() -> str:
    # DASHBOARD_URL is the one setting for "where the dashboard lives" (the
    # digest uses it too); Render provides RENDER_EXTERNAL_URL itself.
    base = os.environ.get("DASHBOARD_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "http://localhost:8000"
    return f"{base.rstrip('/')}/api/v2/integrations/google/callback"


def _fernet() -> Fernet:
    secret = os.environ.get("TOKEN_ENCRYPTION_KEY")
    if not secret:
        raise IntegrationError("TOKEN_ENCRYPTION_KEY is not set on the server.", "not_configured")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def encrypt(value: str | None) -> str | None:
    return _fernet().encrypt(value.encode()).decode() if value else None


def decrypt(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as e:
        raise IntegrationError("Stored credentials can't be decrypted (encryption key changed). Reconnect.", "reconnect") from e


def audit(conn: Connection, owner: str, action: str, target_type: str | None = None, target_id: str | None = None, detail: dict | None = None) -> None:
    conn.execute(sa.insert(audit_log).values(owner=owner, action=action, target_type=target_type, target_id=target_id, detail=detail))


# ------------------------------------------------------------------- oauth


def start_authorization(conn: Connection, owner: str, service: str) -> str:
    cfg = client_config()
    if cfg is None:
        raise IntegrationError("Google integration isn't configured (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET).", "not_configured")
    _fernet()  # fail before redirecting if tokens couldn't be stored afterwards
    if service not in SCOPES:
        raise IntegrationError(f"Unknown service {service}.")
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    conn.execute(sa.delete(oauth_states).where(oauth_states.c.created_at < datetime.now(timezone.utc) - STATE_TTL))
    conn.execute(sa.insert(oauth_states).values(state=state, owner=owner, service=service, code_verifier=verifier))
    params = {
        "client_id": cfg["client_id"], "redirect_uri": redirect_uri(), "response_type": "code",
        "scope": " ".join(SCOPES[service]), "access_type": "offline", "prompt": "consent",
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
        "include_granted_scopes": "false",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def complete_authorization(conn: Connection, owner: str, state: str, code: str) -> str:
    row = conn.execute(sa.select(oauth_states).where(oauth_states.c.state == state)).first()
    conn.execute(sa.delete(oauth_states).where(oauth_states.c.state == state))  # single use
    if row is None or row.owner != owner or row.created_at < datetime.now(timezone.utc) - STATE_TTL:
        raise IntegrationError("This sign-in link expired or wasn't started here. Try connecting again.", "invalid_state")
    cfg = client_config()
    if cfg is None:
        raise IntegrationError("Google integration isn't configured.", "not_configured")
    resp = requests.post(TOKEN_URL, timeout=HTTP_TIMEOUT, data={
        "code": code, "client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
        "redirect_uri": redirect_uri(), "grant_type": "authorization_code", "code_verifier": row.code_verifier,
    })
    if resp.status_code != 200:
        raise IntegrationError("Google rejected the sign-in. Try connecting again.", "exchange_failed")
    tokens = resp.json()
    granted = set((tokens.get("scope") or "").split())
    required = {s for s in SCOPES[row.service] if s.startswith("https://")}
    if not required <= granted:
        raise IntegrationError("The required permission wasn't granted (the checkbox on Google's consent screen).", "scope_denied")
    email = None
    info = requests.get(USERINFO_URL, headers={"Authorization": f"Bearer {tokens['access_token']}"}, timeout=HTTP_TIMEOUT)
    if info.status_code == 200:
        email = info.json().get("email")
    now = datetime.now(timezone.utc)
    values = dict(
        owner=owner, provider="google", service=row.service, account_email=email, scopes=" ".join(sorted(granted)),
        refresh_token_enc=encrypt(tokens.get("refresh_token")), access_token_enc=encrypt(tokens["access_token"]),
        access_token_expires_at=now + timedelta(seconds=int(tokens.get("expires_in", 3600)) - 60),
        status="connected", last_error=None, connected_at=now, updated_at=now,
    )
    stmt = pg_insert(oauth_integrations).values(**values)
    conn.execute(stmt.on_conflict_do_update(
        constraint="oauth_integrations_key", set_={k: v for k, v in values.items() if k not in ("owner", "provider", "service")},
    ))
    audit(conn, owner, "integration.connected", "google", row.service, {"account": email, "scopes": sorted(granted)})
    return row.service


def _integration(conn: Connection, owner: str, service: str):
    return conn.execute(sa.select(oauth_integrations).where(
        oauth_integrations.c.owner == owner, oauth_integrations.c.provider == "google", oauth_integrations.c.service == service,
    )).first()


def access_token(conn: Connection, owner: str, service: str) -> str:
    """Valid access token, refreshing if needed. Marks the integration
    revoked (and says so) when Google refuses the refresh token."""
    row = _integration(conn, owner, service)
    if row is None or row.status == "revoked":
        raise IntegrationError(f"Google {service.title()} isn't connected.", "not_connected")
    now = datetime.now(timezone.utc)
    if row.access_token_enc and row.access_token_expires_at and row.access_token_expires_at > now:
        return decrypt(row.access_token_enc)
    refresh = decrypt(row.refresh_token_enc)
    cfg = client_config()
    if not refresh or cfg is None:
        raise IntegrationError(f"Google {service.title()} needs reconnecting.", "reconnect")
    resp = requests.post(TOKEN_URL, timeout=HTTP_TIMEOUT, data={
        "client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
        "refresh_token": refresh, "grant_type": "refresh_token",
    })
    if resp.status_code in (400, 401) and "invalid_grant" in resp.text:
        conn.execute(sa.update(oauth_integrations).where(oauth_integrations.c.id == row.id).values(
            status="revoked", last_error="Access was revoked or expired at Google.", access_token_enc=None, updated_at=now))
        raise IntegrationError(f"Google {service.title()} access was revoked or expired. Reconnect it.", "reconnect")
    if resp.status_code != 200:
        raise IntegrationError("Couldn't reach Google to refresh access. Try again shortly.", "transient")
    tokens = resp.json()
    conn.execute(sa.update(oauth_integrations).where(oauth_integrations.c.id == row.id).values(
        access_token_enc=encrypt(tokens["access_token"]),
        access_token_expires_at=now + timedelta(seconds=int(tokens.get("expires_in", 3600)) - 60),
        status="connected", last_error=None, updated_at=now,
    ))
    return tokens["access_token"]


def disconnect(conn: Connection, owner: str, service: str) -> None:
    row = _integration(conn, owner, service)
    if row is None:
        return
    others = conn.execute(sa.select(sa.func.count()).select_from(oauth_integrations).where(
        oauth_integrations.c.owner == owner, oauth_integrations.c.provider == "google",
        oauth_integrations.c.service != service, oauth_integrations.c.status == "connected",
    )).scalar_one()
    # Revoking at Google revokes the whole app grant; only do it when no
    # other connected service would be collateral damage.
    if not others:
        try:
            token = decrypt(row.refresh_token_enc) or decrypt(row.access_token_enc)
            if token:
                requests.post(REVOKE_URL, params={"token": token}, timeout=HTTP_TIMEOUT)
        except (IntegrationError, requests.RequestException):
            pass  # local deletion below still removes our copy of the credentials
    conn.execute(sa.delete(oauth_integrations).where(oauth_integrations.c.id == row.id))
    audit(conn, owner, "integration.disconnected", "google", service, {"revoked_at_google": not others})


def status(conn: Connection, owner: str) -> dict:
    cfg = client_config()
    out = {"configured": cfg is not None, "redirect_uri": redirect_uri(), "services": {}}
    for service in SCOPES:
        row = _integration(conn, owner, service)
        out["services"][service] = None if row is None else {
            "status": row.status, "account_email": row.account_email, "connected_at": row.connected_at,
            "last_error": row.last_error, "scopes": row.scopes.split(),
        }
    out["picker_ready"] = bool(cfg and cfg.get("api_key") and cfg.get("app_id"))
    return out


# ------------------------------------------------------------------- gmail


def _mime(to: str | None, subject: str, body: str, sender: str | None) -> str:
    msg = EmailMessage()
    if to:
        msg["To"] = to
    if sender:
        msg["From"] = sender
    msg["Subject"] = subject
    msg.set_content(body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def save_gmail_draft(conn: Connection, owner: str, subject: str, body: str, to: str | None, existing_draft_id: str | None) -> dict:
    """Creates a draft, or updates the job's existing one so repeated saves
    never pile up duplicates. Returns {"draft_id", "message_id"}."""
    token = access_token(conn, owner, "gmail")
    row = _integration(conn, owner, "gmail")
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"message": {"raw": _mime(to, subject, body, row.account_email if row else None)}}
    base = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"
    if existing_draft_id:
        resp = requests.put(f"{base}/{existing_draft_id}", headers=headers, json={"id": existing_draft_id, **payload}, timeout=HTTP_TIMEOUT)
        if resp.status_code == 404:  # sent or deleted in Gmail since; create a fresh one
            resp = requests.post(base, headers=headers, json=payload, timeout=HTTP_TIMEOUT)
    else:
        resp = requests.post(base, headers=headers, json=payload, timeout=HTTP_TIMEOUT)
    if resp.status_code in (401, 403):
        raise IntegrationError("Gmail refused the request. Reconnect Gmail and try again.", "reconnect")
    if resp.status_code >= 400:
        raise IntegrationError(f"Gmail couldn't save the draft (HTTP {resp.status_code}).", "transient")
    data = resp.json()
    audit(conn, owner, "gmail.draft_saved", "gmail_draft", data.get("id"), {"updated": bool(existing_draft_id)})
    return {"draft_id": data.get("id"), "message_id": (data.get("message") or {}).get("id")}


def gmail_draft_exists(conn: Connection, owner: str, draft_id: str) -> bool | None:
    """True/False, or None if Gmail couldn't be asked. A missing draft means
    it was sent *or* deleted -- the caller must not assume which."""
    try:
        token = access_token(conn, owner, "gmail")
        resp = requests.get(f"https://gmail.googleapis.com/gmail/v1/users/me/drafts/{draft_id}",
                            headers={"Authorization": f"Bearer {token}"}, params={"format": "minimal"}, timeout=HTTP_TIMEOUT)
    except (IntegrationError, requests.RequestException):
        return None
    if resp.status_code == 404:
        return False
    return True if resp.status_code == 200 else None


def gmail_open_url(message_id: str | None) -> str:
    return f"https://mail.google.com/mail/u/0/#drafts?compose={message_id}" if message_id else "https://mail.google.com/mail/u/0/#drafts"


# ------------------------------------------------------------------- drive

GOOGLE_DOC = "application/vnd.google-apps.document"
IMPORTABLE = {GOOGLE_DOC, "application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "text/plain"}


def drive_file(conn: Connection, owner: str, file_id: str) -> tuple[dict, bytes, str]:
    """(metadata, bytes, content_type). Google Docs are exported as DOCX."""
    if not file_id.replace("-", "").replace("_", "").isalnum():
        raise IntegrationError("Invalid Drive file id.")
    token = access_token(conn, owner, "drive")
    headers = {"Authorization": f"Bearer {token}"}
    meta = requests.get(f"https://www.googleapis.com/drive/v3/files/{file_id}", headers=headers,
                        params={"fields": "id,name,mimeType,modifiedTime,size,trashed"}, timeout=HTTP_TIMEOUT)
    if meta.status_code == 404:
        raise IntegrationError("JobLookup doesn't have access to that file (pick it again in the Drive picker).", "not_found")
    if meta.status_code >= 400:
        raise IntegrationError(f"Drive returned HTTP {meta.status_code}.", "transient")
    info = meta.json()
    if info.get("mimeType") not in IMPORTABLE:
        raise IntegrationError(f"“{info.get('name')}” isn't a Google Doc, PDF, DOCX or text file.", "unsupported")
    if info.get("mimeType") == GOOGLE_DOC:
        content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        content = requests.get(f"https://www.googleapis.com/drive/v3/files/{file_id}/export", headers=headers,
                               params={"mimeType": content_type}, timeout=60)
    else:
        content_type = info["mimeType"]
        content = requests.get(f"https://www.googleapis.com/drive/v3/files/{file_id}", headers=headers,
                               params={"alt": "media"}, timeout=60)
    if content.status_code >= 400:
        raise IntegrationError(f"Couldn't download “{info.get('name')}” (HTTP {content.status_code}).", "transient")
    audit(conn, owner, "drive.file_imported", "drive_file", file_id, {"name": info.get("name")})
    return info, content.content, content_type
