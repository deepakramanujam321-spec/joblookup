"""grant schema/table privileges on jobseeker to anon/authenticated/service_role

Creating a schema outside `public` does NOT inherit Supabase's default
grants -- a new schema's tables start with zero privileges for anon,
authenticated, or service_role, even though `public` gets these
automatically. BYPASSRLS (what lets service_role skip RLS policies) does
not bypass this -- GRANT/REVOKE is a separate permission layer underneath
RLS. Without this migration, every REST write from
src/jobseeker/storage.py fails with a 403, even with RLS correctly
configured and the service-role key correctly used.

This was discovered live: the first production pipeline run got past
collection cleanly and failed with exactly this 403 on the first insert.

Revision ID: 0002_grant_jobseeker_privileges
Revises: 0001_initial_schema
Create Date: 2026-10-08
"""

from __future__ import annotations

from alembic import op

revision = "0002_grant_jobseeker_privileges"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None

ROLES = "anon, authenticated, service_role"


def upgrade() -> None:
    op.execute(f"grant usage on schema jobseeker to {ROLES}")
    op.execute(f"grant all on all tables in schema jobseeker to {ROLES}")
    op.execute(f"grant all on all sequences in schema jobseeker to {ROLES}")
    # So a future `alembic revision` that adds a table doesn't repeat this
    # exact bug: anything created in this schema from now on automatically
    # gets the same grants. RLS (enabled, zero policies, per migration
    # 0001) is what actually keeps anon/authenticated from touching real
    # data -- this grant alone does not expose rows.
    op.execute(f"alter default privileges in schema jobseeker grant all on tables to {ROLES}")
    op.execute(f"alter default privileges in schema jobseeker grant all on sequences to {ROLES}")


def downgrade() -> None:
    op.execute(f"alter default privileges in schema jobseeker revoke all on tables from {ROLES}")
    op.execute(f"alter default privileges in schema jobseeker revoke all on sequences from {ROLES}")
    op.execute(f"revoke all on all tables in schema jobseeker from {ROLES}")
    op.execute(f"revoke all on all sequences in schema jobseeker from {ROLES}")
    op.execute(f"revoke usage on schema jobseeker from {ROLES}")
