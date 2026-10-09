# JobLookup

A personal job-search workspace. On weekdays it collects backend / AI-platform
roles from employers' own job boards and remote boards, works out how well
each one fits you (and why), checks whether listings are still open, and
drafts applications grounded in your real experience. A dashboard lets you
review, track and give feedback; a Saturday email digest summarises the week.

Nothing is ever sent to an employer automatically. Drafts can be saved to
Gmail, but you send them. Applications are only marked "applied" when you
say so.

## What's in it

| Area | What it does |
|---|---|
| **Collection** | Brave Search discovers Greenhouse / Lever / Ashby postings, which are then read from those ATS's **official public APIs** (full structured description, real publication date, employment type, pay when published). Discovered boards are expanded with other relevant openings. RemoteOK API, WeWorkRemotely RSS, LinkedIn/Indeed best-effort (individual postings only, JSON-LD when present). |
| **Data quality** | Canonical URLs, strict cross-source dedupe with provenance, search-result pages rejected, posting dates stored **only with source evidence** (otherwise "Posting date unavailable"), salaries/skills/seniority/experience extracted deterministically. |
| **Freshness** | Listings are re-checked through the official APIs or page status. Only an authoritative signal (API 404, removed from a board, explicit "position filled") closes a job; network failures never do. |
| **Matching** | Explainable fit breakdown: skills, role family, seniority, location priority, compensation, domain, plus one budgeted LLM read. Unknowns are skipped, not penalised. **Fit, recency, freshness and priority are separate scores.** Claims the LLM makes about you are kept only if your resume/profile supports them. |
| **Feedback learning** | Structured feedback ("wrong seniority", "excellent match", …) adjusts ranking per attribute, after ≥2 consistent signals; explicit profile preferences always win; every learned preference is visible, switchable and resettable; improvement is measured (leave-one-out AUC). |
| **Applications** | Lifecycle (needs review → shortlisted → draft ready → applied → recruiter response → interview → offer / rejected / withdrawn), notes, recruiter details, interviews & reminders, full audit trail. Contradictory moves are refused. |
| **Drafts** | Email + optional cover letter + answers to application questions, from the selected resume. Every generation and every edit is a new immutable version. Missing information is flagged instead of invented. |
| **Profile & resumes** | Structured profile (experience, skills with provenance, projects, preferences, exclusions, free-text notes for the agent), multiple resumes with versions (PDF/DOCX), stored privately in the same database. Extraction proposes; you choose what to apply. **Resume hub:** point the profile at a Drive folder shared "anyone with the link" and new/edited files sync in every weekday (and on demand). |
| **Google (optional)** | Gmail: save drafts (`gmail.compose` only, so no inbox reading and no sending). Drive: import files you pick in Google's Picker (`drive.file` only). |

## Architecture

```
GitHub Actions (cron)                         Render (free web service)
───────────────────────                       ─────────────────────────
collect.yml  Mon–Fri 09:00 IST                webapp/  FastAPI  /api/v2 (+ legacy /api)
  run_collect.py      fetch + filter            ├─ routers/   jobs, applications, drafts,
  score_and_draft.py  ingest → enrich →         │             feedback, profile, integrations
                      score (LLM budgeted)      └─ frontend/  React + TypeScript (Vite)
  run_verify.py       still open?
digest.yml   Sat 09:00 IST                    src/jobseeker/  shared domain package
  run_verify.py → run_digest.py (SMTP)          normalize, ats, ingest, verification,
migrate.yml  manual: alembic upgrade            matching, priority, learning, lifecycle,
backfill.yml manual: re-enrich / re-score       assessment, drafting, documents, google
tests.yml    every push: pytest + vitest
                     │                                 │
                     └──────── Supabase Postgres ──────┘
                               schema `jobseeker` (Alembic: db/)
                               (resume files too: document_blobs)
```

* One data layer (SQLAlchemy Core over `DATABASE_URL`) for the pipeline and
  the API; table definitions mirror the migrations and a test fails on drift.
* LLM access goes through [LiteLLM](https://docs.litellm.ai/docs/providers),
  so it's provider-agnostic. Set one key (OpenAI, Anthropic, Gemini, Groq…).
  Cost is bounded per run (`MAX_LLM_CALLS_PER_RUN`) and cached by input hash.
  On `gpt-4o-mini` a full run costs a few cents.

## Setup

### 1. Database (Supabase)

Schema `jobseeker` in the existing project. **Never** `public`, which holds other
apps' data. Migrations are Alembic only (one linear chain, see `db/README.md`).

`DATABASE_URL`: Supabase → **Connect** → *Session pooler* URI (IPv4-friendly),
e.g. `postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres`.
URL-encode special characters in the password.

### 2. GitHub Actions secrets

Settings → Secrets and variables → Actions:

| Secret | Needed for |
|---|---|
| `DATABASE_URL` | everything |
| `BRAVE_API_KEY` | collection (free tier: 2,000 queries/month) |
| `OPENAI_API_KEY` (or another provider key, optionally `LLM_MODEL`) | semantic scoring + digest drafts |
| `GMAIL_ADDRESS` + `GMAIL_APP_PASSWORD` (or `SMTP_*`) | Saturday digest |
| `DASHBOARD_URL` *(optional)* | "Open in JobLookup" links in the digest |

`SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` are no longer used anywhere. Delete them.

### 3. Render (dashboard)

`render.yaml` describes the service (free plan, Docker). Environment:

| Variable | |
|---|---|
| `DATABASE_URL` | same as above |
| `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD` | HTTP Basic Auth for the whole app |
| `OPENAI_API_KEY` (or other provider) | draft generation + resume extraction in the dashboard |
| `TOKEN_ENCRYPTION_KEY` | encrypts Google tokens at rest (any long random string; `generateValue` in render.yaml) |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_API_KEY`, `GOOGLE_APP_ID` | optional Google integrations (below) |

Render injects `RENDER_EXTERNAL_URL`, which is used to build the OAuth redirect
URI (elsewhere, set `DASHBOARD_URL`). The free plan sleeps when idle; the first
request after that takes ~30s.

### 4. Google integrations (optional)

1. [console.cloud.google.com](https://console.cloud.google.com) → new project → *APIs & Services*:
   enable **Gmail API**, **Google Drive API**, **Google Picker API**.
2. *OAuth consent screen*: External; add your Google account as a test user;
   scopes `gmail.compose`, `drive.file`, `openid`, `email`. **Publish the app
   ("In production")**: in *Testing* mode Google expires refresh tokens after
   7 days. For a personal app you can skip verification; Google shows an
   "unverified app" warning you click through once.
3. *Credentials* → **OAuth client ID** (Web application). Authorized redirect URI:
   `https://<your-render-host>/api/v2/integrations/google/callback`
   (Settings page in the app shows the exact value). → `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`.
4. *Credentials* → **API key**, restricted to the Picker API and your Render
   host as HTTP referrer → `GOOGLE_API_KEY`. Project number (Dashboard) → `GOOGLE_APP_ID`.
5. In the app: **Settings → Connect** Gmail / Drive. Disconnect any time; tokens are
   deleted (and revoked at Google when no other service uses the grant).

### 5. First run after upgrading

1. Actions → **Migrate database** → Run (`head`). Already applied to the live
   project for 0004; this confirms and becomes the path for future migrations.
2. Actions → **Backfill / re-score** → Run. Re-reads existing ATS postings from
   the official APIs (full descriptions, real dates), flags non-postings, and
   re-scores everything with the new breakdown (~$0.10 on gpt-4o-mini).

## Local development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export DATABASE_URL=postgresql+psycopg://postgres@localhost:5432/joblookup
(cd db && alembic upgrade head)

# API (http://localhost:8000) + frontend dev server (http://localhost:5173, proxies /api)
(cd webapp && DASHBOARD_USERNAME=me DASHBOARD_PASSWORD=pw uvicorn main:app --reload) &
(cd webapp/frontend && npm install && npm run dev)

# Tests: a disposable Postgres admin URL; each run creates and drops its own database
TEST_DATABASE_URL=postgresql+psycopg://postgres@localhost:5432/postgres pytest
(cd webapp/frontend && npm test)
```

Collection only (no DB writes): `BRAVE_API_KEY=... python scripts/run_collect.py --pretty`.

## Reusing this for someone else

Nothing in `src/`, `scripts/` or `webapp/` references a specific person. Fork,
replace `config/profile.yaml` + `config/resume.txt` (they seed the profile on
first run, after which the in-app Profile page is the source of truth), create
your own Supabase project, run `alembic upgrade head` from `db/`, set the
secrets above. If reusing it requires editing Python, that's a bug.

## Data sources & terms

| Source | Method | Notes |
|---|---|---|
| Greenhouse / Lever / Ashby | Official public job-board APIs | Published for exactly this use |
| RemoteOK | Public JSON API | Requires a real User-Agent |
| WeWorkRemotely | RSS | Description may be partial; labelled as such |
| LinkedIn / Indeed | Brave discovery + single public page fetch, no login | Best-effort; their ToS restrict automation, so these are never re-fetched for verification |
