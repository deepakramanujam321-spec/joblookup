# db/ — schema migrations (Alembic)

The **only** way the `jobseeker` schema changes. One linear chain in
`versions/`, no branches or merge revisions (`tests/test_migrations.py`
enforces this, round-trips upgrade → downgrade → upgrade with data, and
fails if `src/jobseeker/database.py` drifts from the migrated schema).

| Revision | What |
|---|---|
| `0001_initial_schema` | `jobs`, `runs`; RLS on; expose schema to PostgREST |
| `0002_grant_jobseeker_privileges` | grants a non-`public` schema doesn't inherit |
| `0003_posted_at_to_text` | heterogeneous source date formats stored raw |
| `0004_joblookup_v2` | job intelligence + provenance/verification, profiles, resumes, applications (+events/tasks), versioned drafts, feedback, learned preferences, Google integrations, audit log; legacy data carried forward. Purely additive. |

History note: 0001–0003 were first applied by hand while the database was
stamped `0001`. 0002 and 0003 are idempotent, so the stamp was brought back
in line by applying Alembic's own offline output for `0001:head` (the
`alembic upgrade --sql` script, rehearsed on a copy first), which ran
0002 → 0003 → 0004 and stamped `0004_joblookup_v2`.

## Running

Preferred: GitHub → Actions → **Migrate database** (uses the `DATABASE_URL`
secret, prints pending revisions, upgrades, prints the new head).

Locally:

```bash
cd db
pip install -r requirements.txt
export DATABASE_URL="postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres"
alembic current
alembic history --indicate-current
alembic upgrade head
```

To review SQL before applying (or hand it to someone with DB access):
`alembic upgrade <current>:head --sql > migration.sql`.

## Making a change

```bash
alembic revision -m "short description"   # then rename to the next NNNN_ prefix
```

Set `down_revision` to the current head (keeps the chain linear), write both
`upgrade()` and `downgrade()`, and update `src/jobseeker/database.py` to
match (the drift test will remind you). Then run the test suite.

Rules of thumb:
* Additive first: new nullable / defaulted columns, new tables. The running
  code must keep working against the new schema before it's redeployed.
* Removing something is two releases: stop using it, then drop it.
* New tables: `enable row level security` and grant to the Supabase roles
  in the same migration (see 0004); never add anon/authenticated policies.
* Never touch other schemas, `public` included. Other apps live there.

## Schema reference

* `jobs` — one row per vacancy. `status` is the pipeline/digest state
  (`new → scored → queued_for_digest → sent_in_digest`); the user's own
  workflow lives in `applications`. Posting dates: `posted_at` raw source
  value, `posted_at_ts` parsed **only with** `posted_at_evidence`.
* `job_sources` (provenance per URL), `job_verifications` (every freshness check).
* `job_assessments` — cached, versioned fit breakdowns (`input_hash`).
* `candidate_profiles`, `resumes`, `knowledge_documents`.
* `applications`, `application_events`, `application_tasks`, `application_drafts`.
* `job_feedback`, `learned_preferences`.
* `oauth_integrations` (tokens Fernet-encrypted), `oauth_states`, `audit_log`.
* `runs` — every pipeline run with status, duration, per-source stats.

User-owned tables carry `owner`, set server-side from the authenticated
account (`DASHBOARD_ACCOUNT_ID`, default `default`), never from requests.
