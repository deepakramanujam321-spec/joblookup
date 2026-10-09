"""Dashboard backend: JSON API under /api (v2 + legacy v1) and the built
React app for everything else. Secrets (database URL, service keys, OAuth
tokens) live only here, server-side.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import sqlalchemy as sa  # noqa: E402
from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from deps import engine  # noqa: E402
from routers import applications, drafts, feedback, integrations, jobs, legacy, pipeline, profile  # noqa: E402

log = logging.getLogger("joblookup")
app = FastAPI(title="JobLookup", version="2.0")

for module in (jobs, applications, drafts, feedback, profile, integrations, pipeline, legacy):
    app.include_router(module.router)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    # Log the detail server-side; never echo internals (connection strings,
    # tokens, stack traces) back to the browser.
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"detail": "Internal server error"}, status_code=500)


@app.get("/api/health")
def health():
    """Unauthenticated liveness + database reachability. Render gates
    deploys on this, so a misconfigured database never replaces a working
    version -- and the reason is logged (once per change) so the deploy log
    says what to fix."""
    try:
        with engine().connect() as conn:
            conn.execute(sa.text("select 1"))
        _log_health("ok", None)
        return {"ok": True, "database": "ok"}
    except Exception as exc:
        reason = classify_db_error(exc)
        _log_health(reason, exc)
        return JSONResponse({"ok": False, "database": reason}, status_code=503)


_last_health_state: str | None = None


def _log_health(state: str, exc: Exception | None) -> None:
    global _last_health_state
    if state == _last_health_state:
        return  # health is polled every few seconds; log transitions only
    _last_health_state = state
    if exc is None:
        log.warning("database health: ok")
    else:
        # Driver messages name host/user but never the password.
        detail = str(getattr(exc, "orig", None) or exc).strip().splitlines()[0][:300]
        log.error("database health: %s -- %s: %s. %s", state, type(exc).__name__, detail, DB_HINTS.get(state, ""))


DB_HINTS = {
    "not_configured": "Set DATABASE_URL (Supabase -> Connect -> Session pooler URI) in this service's environment.",
    "auth_failed": "Wrong username or password in DATABASE_URL (special characters in the password must be URL-encoded).",
    "host_not_found": "The host in DATABASE_URL doesn't resolve; copy the pooler URI again.",
    "unreachable": "The database didn't accept the connection (wrong port/host, or Supabase project paused).",
}


def classify_db_error(exc: Exception) -> str:
    text = f"{type(exc).__name__} {exc}".lower()
    if "database_url is not set" in text:
        return "not_configured"
    if "password authentication failed" in text or "tenant or user not found" in text or "authentication" in text:
        return "auth_failed"
    if "resolve host" in text or "name or service not known" in text or "nodename nor servname" in text:
        return "host_not_found"
    return "unreachable"


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    else:
        response.headers.setdefault("Content-Security-Policy", CSP)
    return response


# Scripts only from this origin, plus Google's Drive Picker; job
# descriptions are sanitized server-side and this blocks anything that
# slipped through from executing. Inline *styles* are allowed (React style
# props); inline *scripts* are not.
CSP = (
    "default-src 'self'; script-src 'self' https://apis.google.com; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; "
    "frame-src https://docs.google.com https://drive.google.com; "
    "connect-src 'self' https://content.googleapis.com; object-src 'none'; base-uri 'self'; form-action 'self'; "
    "frame-ancestors 'none'"
)


STATIC_DIR = Path(__file__).resolve().parent / "frontend" / "dist"
if (STATIC_DIR / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    """Client-side routes (/jobs/123, /profile, ...) all serve index.html
    so deep links and refreshes work."""
    if path.startswith("api/"):
        raise HTTPException(404, "Not found")
    candidate = (STATIC_DIR / path).resolve()
    if path and STATIC_DIR in candidate.parents and candidate.is_file():
        return FileResponse(candidate)
    index = STATIC_DIR / "index.html"
    if not index.is_file():
        return JSONResponse({"detail": "Frontend not built. Run `npm run build` in webapp/frontend."}, status_code=503)
    return FileResponse(index, headers={"Cache-Control": "no-cache"})
