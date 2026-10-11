# JobLookup — How we got here (conversation summary)

Summary of the original Claude Code build session (Oct 5–9, 2026). The raw chat isn't included
because it contains passwords and API keys; this keeps the decisions and lessons.

## Phase 1 — v1 pipeline
- Goal: a background "job seeker partner": weekdays it finds and scores jobs, Saturday it emails one
  ranked digest with drafted outreach notes; I apply manually on weekends.
- Sources: Greenhouse/Lever/Ashby (via Brave Search), RemoteOK API, WeWorkRemotely RSS,
  LinkedIn/Indeed best-effort. Discovery = Brave Search API; fetching = Scrapling.
- First tried Claude Code Routines → failed (no connectors, locked-down network) → moved to **GitHub Actions**.
- Storage: Supabase — used the existing *shifu* project with a dedicated `jobseeker` schema and Alembic,
  so my other apps' data in `public` is never touched.
- LLM made provider-agnostic with LiteLLM; I use OpenAI gpt-4o-mini (about $0.03 per 50 jobs).
- Bugs fixed from live runs: stdout corrupting the JSON handoff; Scrapling timeout in ms not s;
  missing grants on a non-public schema (403); `posted_at` type mismatch (400).
- First digest sent successfully; first dashboard (vanilla JS) deployed on Render free tier.

## Phase 2 — JobLookup 2.0 (big spec)
Built end to end: React/TypeScript dashboard (overview, master-detail job workspace, job detail with
honest dates, match breakdown, application workspace, profile/resumes, insights, pipeline health,
settings), official ATS APIs for full descriptions + real posting dates + closed detection,
cross-source dedupe with provenance, explainable fit (unknowns not penalised) separate from
priority/recency/freshness, budgeted + cached LLM reads with grounding checks, application lifecycle
with events/tasks, versioned drafts, Gmail draft saving (no sending), Drive import, structured feedback
learning (min evidence, explicit preferences win, resettable, leave-one-out evaluation), Alembic 0004.
Also fixed two real Alembic bugs (schema creation before version table; migrations silently rolled back).

## Phase 3 — after launch (lessons)
- **Env-var sprawl**: I pointed out duplicated settings. Now only `DATABASE_URL` for the DB (resume
  files moved into Postgres, migration 0005), `PUBLIC_BASE_URL` folded into `DASHBOARD_URL`, an
  `ENV_VARS` registry + test, and a rule in CLAUDE.md.
- `SUPABASE_URL` (REST URL) + service key ≠ Postgres connection; the DB password is only in `DATABASE_URL`.
- Render deploy failed on a `postgresql+asyncpg://` URL → app now accepts any driver prefix; health
  check now logs *why* the DB is unreachable.
- **Salary**: below-minimum stated pay now caps fit at 40; pay status badge on every job
  ("Salary not stated" / "meets your minimum" / "below your minimum"); fixed parsing of Indian
  digit grouping (₹12,00,000).
- **Resume hub**: my Drive folder syncs into resumes (new → resume, edited → new version, deleted
  stays deleted). First live sync failed because the pipeline's requirements lacked pypdf/python-docx →
  fixed + guard test. Then found scores weren't refreshed when the resume changed → each score now
  records the resume/profile it used and stale ones are re-scored.

## State at the end (2026-10-09)
- 250 jobs: 214 with a source-backed posting date, 200 full descriptions, 202 confirmed open,
  11 closed, 14 search-result pages flagged. 162 jobs have a current AI read against the Master Draft.
- Salary: 34 meet the 18 LPA minimum, 202 don't state pay, 0 below.
- 58 jobs queued for the Saturday digest. Total OpenAI spend that day ≈ $0.25.
- Tests: 123 backend (real Postgres) + 18 frontend, all passing; CI runs on every push.
