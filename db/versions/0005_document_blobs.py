"""document_blobs: store uploaded resume files in Postgres

Resumes are small (<=5 MB, a handful per person), so keeping the bytes in
the same database removes the only reason the app needed Supabase's
Storage REST credentials (SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY) next
to DATABASE_URL. One database, one connection setting.

Revision ID: 0005_document_blobs
Revises: 0004_joblookup_v2
Create Date: 2026-10-09
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0005_document_blobs"
down_revision = "0004_joblookup_v2"
branch_labels = None
depends_on = None

S = "jobseeker"
ROLES = "anon, authenticated, service_role"


def upgrade() -> None:
    op.create_table(
        "document_blobs",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("owner", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema=S,
    )
    op.execute(f"alter table {S}.document_blobs enable row level security")
    op.execute(f"grant all on {S}.document_blobs to {ROLES}")


def downgrade() -> None:
    op.drop_table("document_blobs", schema=S)
