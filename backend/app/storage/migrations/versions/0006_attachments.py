"""Attachments: files added to a conversation, stored under /data/files.

Revision ID: 0006_attachments
Revises: 0005_usage_context
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_attachments"
down_revision = "0005_usage_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attachments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("environment_id", sa.Integer, sa.ForeignKey("environments.id"), nullable=False),
        sa.Column("conversation_id", sa.Integer, sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("message_id", sa.Integer, sa.ForeignKey("messages.id")),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("size", sa.Integer, nullable=False),
        sa.Column("folder", sa.String(200), nullable=False, unique=True),
        sa.Column("details", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_attachments_environment_id", "attachments", ["environment_id"])
    op.create_index("ix_attachments_conversation_id", "attachments", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_attachments_conversation_id", "attachments")
    op.drop_index("ix_attachments_environment_id", "attachments")
    op.drop_table("attachments")
