"""Messages queued while a reply runs, kept on the server so they survive a reload.

Revision ID: 0012_queued_messages
Revises: 0011_utility_tasks
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0012_queued_messages"
down_revision = "0011_utility_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "queued_messages",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("conversation_id", sa.Integer, sa.ForeignKey("conversations.id"), nullable=False, index=True),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("attachment_ids", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("queued_messages")
