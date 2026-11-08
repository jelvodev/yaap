"""The shared document beside a conversation.

Revision ID: 0032_documents
Revises: 0031_shell_sessions
Create Date: 2026-11-07
"""

import sqlalchemy as sa
from alembic import op

revision = "0032_documents"
down_revision = "0031_shell_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Integer,
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("path", sa.String(1000), nullable=False),
        sa.Column("opened_by", sa.String(10), nullable=False),
        sa.Column("known_hash", sa.String(64), nullable=True),
        sa.Column("known_text", sa.Text, nullable=True),
        sa.Column("review_hash", sa.String(64), nullable=True),
        sa.Column("review_base", sa.Text, nullable=True),
        sa.Column("review_created", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("review_checkpoint_id", sa.Integer, nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("documents")
