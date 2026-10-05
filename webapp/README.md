# webapp/ — dashboard

A small FastAPI backend + a vanilla-JS single-page frontend it serves
directly, for browsing discovered jobs, marking status, and editing
outreach drafts without waiting for the Saturday email. Read-only
`jobseeker.runs` data also shows here so you can tell at a glance whether
the weekday/weekend pipeline is actually running.

The Supabase service-role key lives only in this server's environment,
never in the browser — the frontend only ever talks to this API, never to
Supabase directly.

## Local run

```bash
cd webapp
pip install -r requirements.txt
cp .env.example .env   # fill in SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY,
                        # DASHBOARD_USERNAME, DASHBOARD_PASSWORD
export $(cat .env | xargs)
python -m uvicorn main:app --reload
```

Open http://localhost:8000 — your browser will prompt for the Basic Auth
credentials you set.

## Deploy (Render)

`render.yaml` at the repo root already describes this service. From the
Render dashboard: New -> Blueprint -> point it at this GitHub repo and
branch. Render reads `render.yaml`, builds `webapp/Dockerfile` with the
repo root as build context (needed since it also copies in `src/` and
`config/`), and prompts you for the four env vars marked `sync: false`:

- `SUPABASE_URL` — `https://tssyakhdwewofhzcmxdj.supabase.co`
- `SUPABASE_SERVICE_ROLE_KEY` — same value used by the GitHub Actions
  pipeline (Supabase dashboard -> this project -> Project Settings -> API
  -> service_role secret)
- `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD` — whatever you want; this
  gates the whole dashboard behind HTTP Basic Auth

No Blueprint UI available, or you'd rather do it by hand: New -> Web
Service -> Docker -> point it at this repo, set "Dockerfile Path" to
`webapp/Dockerfile` and leave "Root Directory" empty (so the build context
is the repo root), set the same four env vars, set health check path to
`/api/health`.

Free-tier Render services spin down after 15 minutes idle and take a few
seconds to wake on the next request — fine for a personal dashboard you
check a few times a day, not fine if you wanted sub-second loads at 2am.

## API

All endpoints except `/api/health` require HTTP Basic Auth.

- `GET /api/jobs?status=&source=&min_score=&q=&limit=&offset=` — list/filter
- `GET /api/jobs/{id}` — one job
- `PATCH /api/jobs/{id}` — body `{"status": "...", "outreach_draft": "..."}`,
  either field optional
- `GET /api/stats` — counts by status
- `GET /api/runs?limit=` — recent pipeline runs
- `GET /api/health` — unauthenticated liveness check
