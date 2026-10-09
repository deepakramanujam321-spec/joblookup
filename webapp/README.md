# webapp/ — dashboard

FastAPI (`main.py`, `routers/`) serving a JSON API under `/api/v2` (the v1
`/api/*` endpoints still work) plus the React app in `frontend/`. Secrets
(database URL, storage key, OAuth tokens) never reach the browser.

```
routers/jobs.py           overview, list (server-side filter/sort/page), detail, live re-check
routers/applications.py   status lifecycle, saved, notes, interviews/reminders, board
routers/drafts.py         generate / edit (versioned) / save to Gmail (never send)
routers/feedback.py       structured feedback, learned preferences, evaluation
routers/profile.py        profile (optimistic concurrency), resumes (upload/versions/extract/apply)
routers/integrations.py   Google connect/callback/disconnect, Drive Picker + import
routers/pipeline.py       health + run history
routers/legacy.py         v1 contract on the new data layer
frontend/src/             pages/, components/, api/ (typed client + React Query hooks), lib/
```

## Local

```bash
cd webapp
DATABASE_URL=... DASHBOARD_USERNAME=me DASHBOARD_PASSWORD=pw uvicorn main:app --reload
cd frontend && npm install && npm run dev      # http://localhost:5173, proxies /api to :8000
```

`npm run build` writes `frontend/dist/`, which `main.py` serves (with SPA
fallback so `/jobs/123` deep links work). The Docker image builds it in a
separate Node stage.

## Security notes

* HTTP Basic Auth on every `/api` route except `/api/health`.
* Job descriptions are untrusted HTML: sanitised server-side (nh3 allow-list),
  and the app sends a CSP that forbids inline/third-party scripts (except
  Google's Picker loader).
* Uploads: type by magic bytes, 5 MB cap, DOCX zip-bomb/macro checks;
  stored in Postgres (`document_blobs`) and downloaded only through the API.
* LLM prompts wrap postings/documents as untrusted data; claims about the
  candidate are checked against their own evidence before being kept.
* Rate limits on paid/third-party endpoints (LLM, Gmail, Drive, live checks).
