# joblookup

An autonomous job-search partner for Deepak Vaduganambi. It searches for
backend/AI-platform roles in the background on weekdays, scores and drafts
outreach notes for the good ones, and on Saturday morning emails a single
ranked digest — ready to review and send over the weekend.

## How it works

```
Weekdays (Mon-Fri), GitHub Actions          Saturday morning, GitHub Actions
───────────────────────────────             ───────────────────────────────
run_collect.py                               run_digest.py
  Brave Search discovery                       queries jobseeker.jobs
  + Scrapling fetch across                     where status=
  Greenhouse/Lever/Ashby,                      'queued_for_digest'
  RemoteOK, WeWorkRemotely,                        │
  LinkedIn/Indeed (best-effort)                    ▼
  → deterministic filters                    renders one ranked HTML
  → JSON on stdout                           email (pure templating,
       │                                     no LLM call needed here --
       ▼                                     scoring already happened)
score_and_draft.py                                │
  dedupes against Supabase,                        ▼
  calls Claude (one structured                send_digest.py
  tool-use call per job) to                    Gmail SMTP → your inbox
  score fit + draft an                             │
  outreach note for strong                          ▼
  matches, writes results                     marks those rows
  back to Supabase                            sent_in_digest
```

Scraping, filtering, storage, and sending are all plain deterministic
Python — reliable, debuggable, cheap to re-run. **Only one step calls an
LLM**: scoring each new posting against the resume and drafting an outreach
note for the strong matches (`scripts/score_and_draft.py`, via
[LiteLLM](https://docs.litellm.ai/docs/providers) — any provider works, see
`LLM_MODEL` in Setup below). The Saturday digest is pure templating over
already-scored data, no LLM call needed.

Nothing is ever sent to an employer automatically. The weekday runs only
populate a queue; you read and send the Saturday digest yourself.

### Why GitHub Actions, not a Claude Code Routine

The first version of this ran on a Claude Code Routine. That turned out not
to work: Routine-fired sessions in this account get a fresh container with
(a) no MCP connectors and (b) a locked-down network policy that blocks
Supabase, Brave, Gmail, and every job board. GitHub Actions runners have
ordinary internet access and no such restriction, so that's what this runs
on now. See `.github/workflows/`.

## Project layout

```
config/
  profile.yaml         target roles, locations, salary floor, exclusions
  resume.txt            resume text used for fit-scoring and drafting notes
src/jobseeker/
  brave_search.py        Brave Search API client (discovery)
  scrapling_fetch.py     Scrapling wrapper (fast fetch + stealth fetch)
  parsing.py              turns a fetched page into a JobListing
  sources.py              one function per source: ATS boards, RemoteOK,
                          WeWorkRemotely, LinkedIn/Indeed (best-effort)
  filters.py               deterministic pre-filters (excluded companies,
                          staffing agencies, keyword match, salary floor)
  models.py                 JobListing dataclass
  storage.py                 Supabase REST client (jobseeker schema)
  config.py                   loads profile.yaml / resume.txt / env vars
scripts/
  run_collect.py          mechanical: fetch candidates, filter, print JSON
  score_and_draft.py        the one LLM step: score fit, draft outreach notes
  run_digest.py              mechanical: render + send the weekly digest
  send_digest.py              Gmail SMTP sender (also used standalone)
  db.py                        CLI over storage.py, handy for manual debugging
db/
  (a separate Alembic project for schema migrations — see db/README.md)
.github/workflows/
  collect.yml              weekday cron -> run_collect.py + score_and_draft.py
  digest.yml                 Saturday cron -> run_digest.py
```

## Data sources

| Source | Method | Notes |
|---|---|---|
| Greenhouse / Lever / Ashby boards | Brave Search discovery + direct page fetch | Legit public ATS pages, no ToS risk |
| RemoteOK | Public JSON API (`remoteok.com/api`) | Requires a real User-Agent header |
| WeWorkRemotely | RSS feed | No auth needed |
| LinkedIn / Indeed | Brave Search discovery + Scrapling stealth fetch, **public search results only, no login** | Best-effort, lower priority. Scraping these sites is against their ToS even with anti-bot tooling — this is read-only against public pages, rate-limited, and never uses your credentials. Treat its results as a bonus, not the primary feed. |

## Storage: Supabase

Project `deepakramanujam321-spec's Project` (`tssyakhdwewofhzcmxdj`) in the
`shifu` org, schema **`jobseeker`** — deliberately not `public`, so this
project's tables never collide with the other apps sharing this Supabase
project (gym tracking, learning log, etc). Tables: `jobseeker.jobs`,
`jobseeker.runs`. RLS is enabled with **no policies** — this schema is only
ever written to via `scripts/db.py` / `src/jobseeker/storage.py` using the
service-role key (bypasses RLS), never from a public/anon client. Schema
changes are tracked with Alembic — see `db/README.md` before changing
anything by hand.

There are two small leftover tables, `public.jobseeker_jobs` and
`public.jobseeker_runs`, from before the move to a dedicated schema —
empty, unused, harmless. Dropping them kept timing out (a Supabase-side
issue at the time, not a lock on real data); safe to drop manually via the
Supabase SQL editor whenever convenient, or leave them.

## Setup

### 1. Secrets

Add these as **GitHub Actions secrets** on this repo — Settings -> Secrets
and variables -> Actions -> New repository secret — not committed to git,
not pasted into chat:

- `BRAVE_API_KEY` — https://api.search.brave.com/app/keys (free tier:
  2,000 queries/month; this pipeline uses roughly 20-25/day on a weekday
  run, well inside that).
- An LLM provider key for `score_and_draft.py`'s per-job scoring call — at
  a few dozen jobs/day this is pennies a month on any provider. Scoring
  goes through [LiteLLM](https://docs.litellm.ai/docs/providers), so it's
  provider-agnostic. Just add ONE of these key secrets and it's picked up
  automatically (checked in this order; `LLM_MODEL` only needs setting if
  you want to override the model that provider defaults to, or use a
  provider not in this list):

  | add this key secret | get a key at | and you get |
  |---|---|---|
  | `ANTHROPIC_API_KEY` | console.anthropic.com | `anthropic/claude-sonnet-5` |
  | `OPENAI_API_KEY` | platform.openai.com | `openai/gpt-4o-mini` |
  | `GEMINI_API_KEY` | aistudio.google.com/apikey | `gemini/gemini-2.0-flash` |
  | `GROQ_API_KEY` | console.groq.com (fast, generous free tier) | `groq/llama-3.3-70b-versatile` |

  Any other [LiteLLM-supported model string](https://docs.litellm.ai/docs/providers)
  works too — `.github/workflows/collect.yml` just needs that provider's
  env var name added alongside the four already wired through.
- `SUPABASE_URL` — `https://tssyakhdwewofhzcmxdj.supabase.co`
- `SUPABASE_SERVICE_ROLE_KEY` — Supabase dashboard -> this project ->
  Project Settings -> API -> "service_role" secret (click reveal). This key
  bypasses Row-Level Security, so treat it like a database password.
- `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` — an
  [app password](https://myaccount.google.com/apppasswords) for your Gmail
  account (requires 2-Step Verification). Do not use your real Gmail
  password here.

### 2. The two workflows

Already committed in `.github/workflows/` — they start running on their
cron schedule as soon as the secrets above are set:

- **collect.yml** — Mon-Fri, 9:00 AM IST. Fetches candidates, scores them,
  drafts outreach notes for strong matches.
- **digest.yml** — Saturday, 9:00 AM IST. Sends the week's queued matches
  as one email.

Both also support manual triggering from the repo's Actions tab
(`workflow_dispatch`) if you want to run one on demand.

Note: GitHub disables a scheduled workflow automatically after 60 days
with no commits to the repo. A commit (even a small one, like a
`profile.yaml` tweak) resets that clock.

### 3. Tuning the search

Edit `config/profile.yaml` any time — role titles, location priorities,
salary floor, excluded companies — the next scheduled run picks it up
automatically, no redeploy needed. Keep `config/resume.txt` in sync with
your actual resume; it's what the agent scores every posting against.

### Reusing this for someone else

Nothing under `src/`, `scripts/`, or `webapp/` references a specific
person — every personal detail (name, resume, role targets, location
priorities, salary floor, exclusions, digest recipient) lives in
`config/profile.yaml` and `config/resume.txt`, and every credential is an
env var, never a literal in code. For someone else to run their own
instance: fork the repo, replace those two config files with their own,
set up their own Supabase project (`db/` has the schema migration) and
their own secrets, deploy their own dashboard. No Python changes needed —
if a change to get someone else running *does* require editing `.py`
files, that's a bug in this repo, not an expected step.

## Local testing

```bash
pip install -r requirements.txt
scrapling install

export BRAVE_API_KEY=...
python scripts/run_collect.py --pretty | head -100
```

This only exercises the mechanical scraping step — no Supabase writes, no
emails sent, safe to run as often as you like while iterating on
`profile.yaml` or the source parsers. To test scoring too, also set
`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, and one provider key from the
table above, then run `python scripts/score_and_draft.py --jobs-file <file>`.
