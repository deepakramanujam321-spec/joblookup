"""multi-owner jobs: each person's job list is their own

Until now `jobs` was one shared catalog and its per-person columns (fit
score, status, match highlights, ...) silently meant "Deepak's". Two
people with different searches would see each other's listings. Jobs now
belong to an owner; the same posting found for two owners is two rows, so
scores, statuses and digest state never mix.

* jobs.owner (default 'default': every existing row is the original user's)
* uniqueness moves from (url) to (owner, url); job_sources from (url) to
  (job_id, url) -- a posting URL may now exist once per owner
* runs.owner: which person a collect/enrich/digest run was for (NULL for
  runs that cover everyone, e.g. verification)

Additive, so the running code keeps working until it is redeployed.
Downgrade is refused while another owner has jobs (it would have to
delete their data to restore the single-owner uniqueness).

Revision ID: 0006_multi_owner_jobs
Revises: 0005_document_blobs
Create Date: 2026-11-24
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0006_multi_owner_jobs"
down_revision = "0005_document_blobs"
branch_labels = None
depends_on = None

S = "jobseeker"


def upgrade() -> None:
    op.add_column("jobs", sa.Column("owner", sa.Text(), nullable=False, server_default="default"), schema=S)
    op.drop_constraint("jobs_url_key", "jobs", schema=S, type_="unique")
    op.create_unique_constraint("jobs_owner_url_key", "jobs", ["owner", "url"], schema=S)
    op.create_index("jobs_owner_status_idx", "jobs", ["owner", "status"], schema=S)
    op.create_index("jobs_owner_canonical_url_idx", "jobs", ["owner", "canonical_url"], schema=S)

    op.drop_constraint("job_sources_url_key", "job_sources", schema=S, type_="unique")
    op.create_unique_constraint("job_sources_job_url_key", "job_sources", ["job_id", "url"], schema=S)

    op.add_column("runs", sa.Column("owner", sa.Text()), schema=S)
    op.execute(f"update {S}.runs set owner = 'default' where run_type in ('collect', 'enrich', 'digest')")


def downgrade() -> None:
    others = op.get_bind().execute(sa.text(f"select count(*) from {S}.jobs where owner <> 'default'")).scalar()
    if others:
        raise RuntimeError(
            f"{others} job(s) belong to other owners; downgrading would have to delete them. "
            "Remove or export that data first."
        )
    op.drop_column("runs", "owner", schema=S)
    op.drop_constraint("job_sources_job_url_key", "job_sources", schema=S, type_="unique")
    op.create_unique_constraint("job_sources_url_key", "job_sources", ["url"], schema=S)
    op.drop_index("jobs_owner_canonical_url_idx", table_name="jobs", schema=S)
    op.drop_index("jobs_owner_status_idx", table_name="jobs", schema=S)
    op.drop_constraint("jobs_owner_url_key", "jobs", schema=S, type_="unique")
    op.create_unique_constraint("jobs_url_key", "jobs", ["url"], schema=S)
    op.drop_column("jobs", "owner", schema=S)
