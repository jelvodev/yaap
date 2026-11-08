"""Shell sessions that live as long as their conversation.

Revision ID: 0031_shell_sessions
Revises: 0030_context_control
Create Date: 2026-11-07
"""

import sqlalchemy as sa
from alembic import op

revision = "0031_shell_sessions"
down_revision = "0030_context_control"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shell_sessions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "conversation_id", sa.Integer, sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(40), nullable=False),
        sa.Column("place", sa.String(40), nullable=False),
        sa.Column("token", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("conversation_id", "name"),
    )
    op.create_index("ix_shell_sessions_conversation_id", "shell_sessions", ["conversation_id"])


def downgrade() -> None:
    op.drop_table("shell_sessions")
