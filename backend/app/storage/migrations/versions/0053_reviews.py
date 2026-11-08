"""Reviews by another vendor: a report on a project's changes by a model of another vendor.

Revision ID: 0053_reviews
Revises: 0052_learning
Create Date: 2026-11-08
"""

import sqlalchemy as sa
from alembic import op

revision = "0053_reviews"
down_revision = "0052_learning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reviews",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("environment_id", sa.Integer, sa.ForeignKey("environments.id"), nullable=False),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "conversation_id", sa.Integer, sa.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("goal_id", sa.Integer, nullable=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("objective", sa.Text, nullable=True),
        sa.Column("writer_vendors", sa.JSON, nullable=False),
        sa.Column("reviewer_provider", sa.String(40), nullable=True),
        sa.Column("reviewer_model", sa.String(80), nullable=True),
        sa.Column("diff", sa.Text, nullable=False),
        sa.Column("notes", sa.JSON, nullable=False),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("findings", sa.JSON, nullable=False),
        sa.Column("cost", sa.Float, nullable=True),
        sa.Column("input_tokens", sa.Integer, nullable=False),
        sa.Column("output_tokens", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_reviews_project_id", "reviews", ["project_id"])
    op.create_index("ix_reviews_conversation_id", "reviews", ["conversation_id"])
    op.create_index("ix_reviews_goal_id", "reviews", ["goal_id"])


def downgrade() -> None:
    op.drop_table("reviews")
