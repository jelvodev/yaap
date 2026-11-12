"""Plans: what the agent proposes to build in a project, step by step, and where its build runs.

Revision ID: 0061_plans
Revises: 0060_preset_delegation
Create Date: 2026-11-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0061_plans"
down_revision = "0060_preset_delegation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plans",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "conversation_id", sa.Integer, sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("card_id", sa.Integer, nullable=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.JSON, nullable=False),
        sa.Column("base_branch", sa.String(120), nullable=True),
        sa.Column("base_commit", sa.String(40), nullable=True),
        sa.Column("build_branch", sa.String(120), nullable=True),
        sa.Column("build_folder", sa.String(300), nullable=True),
        sa.Column("build_run_id", sa.Integer, nullable=True),
        sa.Column("failure", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_plans_project_id_status", "plans", ["project_id", "status"])
    op.create_index("ix_plans_conversation_id", "plans", ["conversation_id"])


def downgrade() -> None:
    op.drop_table("plans")
