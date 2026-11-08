"""Checkpoints: a snapshot of a project folder before each action that can change files.

Revision ID: 0020_checkpoints
Revises: 0019_blueprint_search_kind
Create Date: 2026-11-06
"""

import sqlalchemy as sa
from alembic import op

revision = "0020_checkpoints"
down_revision = "0019_blueprint_search_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "checkpoints",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("conversation_id", sa.Integer, sa.ForeignKey("conversations.id", ondelete="SET NULL"), index=True),
        sa.Column("run_record_id", sa.Integer, nullable=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("tool", sa.String(60), nullable=False),
        sa.Column("summary", sa.String(300), nullable=False),
        sa.Column("call_key", sa.String(16), nullable=True),
        sa.Column("commit", sa.String(64), nullable=False),
        sa.Column("tree", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("checkpoints")
