# db/ — schema migrations

A real Alembic project for the `jobseeker` schema, kept separate from
`requirements.txt` at the repo root: the app's runtime (`scripts/*.py`) only
ever talks to Supabase over its REST API and never needs a direct Postgres
connection, so `alembic`/`psycopg2` would be dead weight in every CI run.
This is a dev/ops-only tool, used when the schema itself changes.

## Why this exists

The initial schema (`versions/0001_initial_schema.py`) was applied directly
against the database on 2026-10-05, before this Alembic project existed —
necessary at the time, but not how schema changes should happen going
forward. That migration file's DDL is written to match exactly what's live,
and the database's `jobseeker.alembic_version` table has already been
stamped at `0001_initial_schema`. That means:

- Running `alembic upgrade head` against this database today is a **no-op**
  that confirms the migration history and the live schema agree — it will
  not try to recreate anything.
- Every schema change from here on should be a new Alembic revision, not a
  one-off SQL command. That's what keeps "migrate it in the future" safe.

## Setup

```bash
cd db
pip install -r requirements.txt
```

You need `DATABASE_URL` — Supabase's **direct Postgres connection string**,
not the `SUPABASE_SERVICE_ROLE_KEY` used by the app at runtime. Get it from
the Supabase dashboard: this project -> Project Settings -> Database ->
Connection string -> "Transaction pooler" (recommended; works from
anywhere, including CI) or "Session pooler". It looks like:

```
postgresql://postgres.tssyakhdwewofhzcmxdj:[YOUR-PASSWORD]@aws-0-<region>.pooler.supabase.com:6543/postgres
```

```bash
export DATABASE_URL="postgresql://..."
alembic current      # should print 0001_initial_schema (head) once in sync
alembic upgrade head # no-op on a database already at head
```

## Making a schema change

```bash
cd db
alembic revision -m "add applied_at column to jobs"
# edit the generated file in versions/, write upgrade()/downgrade()
alembic upgrade head
```

Keep changes additive where possible (new nullable columns, new tables) so
a migration never has to choose between losing data and failing outright.
If a column really needs to go, do it as two migrations: stop writing to it
in the app first, drop it in a later migration once you're sure nothing
still depends on it.

## Schema reference

- `jobseeker.jobs` — one row per discovered posting. `status` moves
  `new -> scored -> queued_for_digest -> sent_in_digest` (or `-> excluded`
  by the deterministic filters, before it ever reaches here).
- `jobseeker.runs` — one row per pipeline run (`collect` or `digest`), for
  debugging when something goes quiet.
- RLS is enabled on both tables with **no policies** — intentional, since
  the only writer is `scripts/db.py`/`src/jobseeker/storage.py` using the
  service-role key, which bypasses RLS entirely. Never add a public/anon
  policy to these tables.
