"""Background processes started by agents, shown on the project page.

Revision ID: 0013_processes
Revises: 0012_queued_messages
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0013_processes"
down_revision = "0012_queued_messages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "processes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("environment_id", sa.Integer, sa.ForeignKey("environments.id"), nullable=False),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=True, index=True),
        sa.Column("conversation_id", sa.Integer, sa.ForeignKey("conversations.id"), nullable=True),
        sa.Column("command", sa.Text, nullable=False),
        sa.Column("port", sa.Integer, nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("exit_code", sa.Integer, nullable=True),
        sa.Column("ended_reason", sa.String(120), nullable=True),
        sa.Column("output_tail", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("processes")
