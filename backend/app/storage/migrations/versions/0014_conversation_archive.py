"""Archived conversations: hidden from the sidebar until restored.

Revision ID: 0014_conversation_archive
Revises: 0013_processes
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0014_conversation_archive"
down_revision = "0013_processes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("archived_at")
