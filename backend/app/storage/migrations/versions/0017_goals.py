"""Goal mode: a conversation works towards an objective over several turns.

Revision ID: 0017_goals
Revises: 0016_run_tree
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0017_goals"
down_revision = "0016_run_tree"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "goals",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("conversation_id", sa.Integer, sa.ForeignKey("conversations.id"), nullable=False, index=True),
        sa.Column("objective", sa.Text, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("max_turns", sa.Integer, nullable=False),
        sa.Column("max_cost", sa.Float, nullable=False),
        sa.Column("turns", sa.Integer, nullable=False),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("goals")
