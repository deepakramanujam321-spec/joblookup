# joblookup

An autonomous job-search partner for Deepak Vaduganambi. It searches for
backend/AI-platform roles in the background on weekdays, scores and drafts
outreach notes for the good ones, and on Saturday morning emails a single
ranked digest — ready to review and send over the weekend.

## How it works

```
Weekdays (Mon-Fri)              Saturday morning
───────────────────             ─────────────────
Routine fires  ─┐                Routine fires ─┐
                 │                                │
  run_collect.py │  mechanical                    │
  (Brave Search  │  scraping,                      │
  + Scrapling)   │  no LLM calls                   │
                 ▼                                 ▼
  stdout: JSON list          Claude agent queries
  of candidate jobs          jobseeker_jobs where
                 │            status='queued_for_digest'
                 ▼                                 │
  Claude agent dedupes                              ▼
  against Supabase,          Agent composes the
  scores each new job        digest HTML, ranked
  against resume.txt,        by fit_score, each
  drafts an outreach         with its drafted note
  note for strong fits                              │
                 │                                   ▼
                 ▼            send_digest.py (mechanical
  jobseeker_jobs updated      Gmail SMTP send) → inbox
  in Supabase
```

The split is deliberate: **scraping and sending are plain, testable Python
scripts with no LLM in the loop** — reliable, debuggable, cheap to re-run.
**Scoring fit and drafting outreach notes is done by the Claude agent itself**
when a Routine fires, using the Supabase MCP connection already available in
this environment — no second API key, no redundant LLM-calling code to
maintain.

Nothing is ever sent to an employer automatically. The weekday runs only
populate a queue; you read and send the Saturday digest yourself.

## Project layout

```
config/
  profile.yaml      target roles, locations, salary floor, exclusions
  resume.txt         resume text used for fit-scoring and drafting notes
src/jobseeker/
  brave_search.py     Brave Search API client (discovery)
  scrapling_fetch.py  Scrapling wrapper (fast fetch + stealth fetch)
  parsing.py           turns a fetched page into a JobListing
  sources.py           one function per source: ATS boards, RemoteOK,
                        WeWorkRemotely, LinkedIn/Indeed (best-effort)
  filters.py            deterministic pre-filters (excluded companies,
                        staffing agencies, keyword match, salary floor)
  models.py             JobListing dataclass
  config.py             loads profile.yaml / resume.txt / env vars
scripts/
  run_collect.py        mechanical: fetch candidates, filter, print JSON
  send_digest.py         mechanical: send a pre-built HTML file via Gmail SMTP
```

## Data sources

| Source | Method | Notes |
|---|---|---|
| Greenhouse / Lever / Ashby boards | Brave Search discovery + direct page fetch | Legit public ATS pages, no ToS risk |
| RemoteOK | Public JSON API (`remoteok.com/api`) | Requires a real User-Agent header |
| WeWorkRemotely | RSS feed | No auth needed |
| LinkedIn / Indeed | Brave Search discovery + Scrapling stealth fetch, **public search results only, no login** | Best-effort, lower priority. Scraping these sites is against their ToS even with anti-bot tooling — this is read-only against public pages, rate-limited, and never uses your credentials. Treat its results as a bonus, not the primary feed. |

## Storage: Supabase

Project `deepakramanujam321-spec's Project` (`tssyakhdwewofhzcmxdj`), table
`jobseeker_jobs`. Row-Level Security is enabled with **no policies** — this
table is only ever written to via the Supabase MCP connection (service-role
equivalent, bypasses RLS) inside a Claude Code session, never from a public
client, so zero policies is the correct "nobody but the backend touches
this" configuration, not an oversight.

Columns worth knowing: `status` moves `new → scored → queued_for_digest →
sent_in_digest`, `fit_score` (0-100) and `fit_rationale` are written by the
agent during the weekday run, `outreach_draft` holds the drafted note for
jobs that scored high enough to be worth your time.

## Setup

### 1. Secrets

This repo needs two secrets, set as **environment variables on this Claude
Code environment** (Settings for this environment in the Claude Code web
UI) — not committed to git, not pasted into chat. See `.env.example` for
the full list and where to get each one:

- `BRAVE_API_KEY` — from https://api.search.brave.com/app/keys (free tier:
  2,000 queries/month; this pipeline uses roughly 20-25/day on a weekday
  run, well inside that).
- `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` — an
  [app password](https://myaccount.google.com/apppasswords) for
  deepakramanujam321@gmail.com (requires 2-Step Verification). Do not use
  your real Gmail password here.

Supabase needs no separate secret — the agent uses the Supabase MCP
connection already authorized in this environment.

### 2. Install dependencies

```bash
pip install -r requirements.txt
scrapling install   # downloads the browser Scrapling's stealth fetcher needs
```

### 3. The two Routines

Two scheduled Routines drive this (already created — see
`mcp__Claude_Code_Remote__list_triggers` to inspect or adjust them):

- **jobseeker-weekday-collect** — fires once daily, Mon-Fri, 9:00 AM IST.
  Runs `scripts/run_collect.py`, then scores and drafts outreach notes for
  the results.
- **jobseeker-weekend-digest** — fires Saturday, 9:00 AM IST. Compiles the
  week's queued matches into one email and sends it via
  `scripts/send_digest.py`.

To change the schedule, use `update_trigger` with a new `cron_expression`
(cron is evaluated in UTC — IST is UTC+5:30).

### 4. Tuning the search

Edit `config/profile.yaml` any time — role titles, location priorities,
salary floor, excluded companies — the next Routine firing picks it up
automatically, no redeploy needed. Keep `config/resume.txt` in sync with
your actual resume; it's what the agent scores every posting against.

## Local testing

```bash
export BRAVE_API_KEY=...
python scripts/run_collect.py --pretty | head -100
```

This only exercises the mechanical scraping step — no Supabase writes, no
emails sent, safe to run as often as you like while iterating on
`profile.yaml` or the source parsers.
