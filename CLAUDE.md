# Working on this repo

## Environment variables: one name per concept, no duplicates

Before adding, renaming or documenting any environment variable or secret:

1. Read `ENV_VARS` in `src/jobseeker/config.py`, the full registry.
2. If an existing variable already carries the same credential, URL or service
   (even under a different name), **reuse it**. Don't add a second name, alias
   or fallback chain for the same thing.
3. Prefer deriving or removing over adding. Example: resume files live in
   Postgres, so the app needs only `DATABASE_URL`, not Supabase REST
   credentials on top of it.
4. If a new variable is truly needed, register it in `ENV_VARS` with its
   purpose and wire it into the workflows/`render.yaml` in the same change.
   Then tell the user exactly where to set it and why no existing one fits.

`tests/test_env_registry.py` enforces this: it fails on unregistered or unused
variables across code, `.github/workflows/` and `render.yaml`.

Known debt: `GMAIL_ADDRESS`/`GMAIL_APP_PASSWORD` duplicate
`SMTP_USERNAME`/`SMTP_PASSWORD`. Collapse them to one pair once it's confirmed
which names the GitHub secrets actually use.

## Database migrations

Alembic only (`db/`), one linear chain (`NNNN_` prefixes, each `down_revision`
= previous head, no branches or merges); see `db/README.md`. Never hand-write
DDL against the live database. Never touch schemas other than `jobseeker`:
`public` holds other apps' data.

## Checks before pushing

```bash
TEST_DATABASE_URL=postgresql+psycopg://postgres@localhost:5432/postgres pytest
ruff check --select F,E9,B --ignore B008,B904,E402 src scripts webapp tests db
(cd webapp/frontend && npm test && npm run build)
```
