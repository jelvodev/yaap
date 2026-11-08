"""Memory consolidation and imported sessions.

Revision ID: 0033_memory_import
Revises: 0032_documents
Create Date: 2026-11-07
"""

import sqlalchemy as sa
from alembic import op

revision = "0033_memory_import"
down_revision = "0032_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_merges",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("entries", sa.JSON, nullable=False),
        sa.Column("section", sa.String(80), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "memory_tidy_state",
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("entries_at_check", sa.Integer, nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("imported_from", sa.JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("imported_from")
    op.drop_table("memory_tidy_state")
    op.drop_table("memory_merges")
