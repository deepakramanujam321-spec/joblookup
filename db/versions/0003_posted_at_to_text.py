"""jobs.posted_at: timestamptz -> text

Sources hand back wildly different date formats: WeWorkRemotely's RSS
feed gives RFC-822 ("Tue, 07 Oct 2026 10:15:00 GMT"), RemoteOK's API
gives ISO-8601, ATS boards and LinkedIn/Indeed never set it at all
(empty string). Nothing in this app does date arithmetic on this column
-- it's purely informational, shown as-is in the digest -- so fighting
every source's format just to satisfy a typed column buys nothing and
breaks real inserts (a non-ISO string, or even "" instead of NULL, is
invalid input for timestamptz and fails the whole batch insert it's in).
Storing the raw string sidesteps the entire problem.

Discovered live: the pipeline's third run got past both prior bugs
(stdout corruption, missing schema grants) and failed on this exact
issue on the first real insert.

Revision ID: 0003_posted_at_to_text
Revises: 0002_grant_jobseeker_privileges
Create Date: 2026-10-09
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003_posted_at_to_text"
down_revision = "0002_grant_jobseeker_privileges"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("jobs", "posted_at", type_=sa.Text(), schema="jobseeker")


def downgrade() -> None:
    # Not reversible in general (arbitrary text may not parse as a
    # timestamp); this intentionally fails loudly rather than silently
    # dropping data that doesn't cast cleanly.
    op.alter_column("jobs", "posted_at", type_=sa.DateTime(timezone=True), schema="jobseeker")
