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
    """Unauthenticated liveness + database reachability."""
    try:
        with engine().connect() as conn:
            conn.execute(sa.text("select 1"))
        return {"ok": True, "database": "ok"}
    except Exception:
        return JSONResponse({"ok": False, "database": "unreachable"}, status_code=503)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


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
