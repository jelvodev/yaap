"""Scheduled tasks and their runs.

Revision ID: 0024_scheduled_tasks
Revises: 0023_memory_suggestions
Create Date: 2026-11-06
"""

import sqlalchemy as sa
from alembic import op

revision = "0024_scheduled_tasks"
down_revision = "0023_memory_suggestions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scheduled_tasks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("prompt", sa.Text, nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("effort", sa.String(20), nullable=False),
        sa.Column("agent", sa.String(80), nullable=True),
        sa.Column("preset", sa.String(40), nullable=True),
        sa.Column("schedule_kind", sa.String(20), nullable=False),
        sa.Column("cron", sa.String(120), nullable=False, server_default=""),
        sa.Column("timezone", sa.String(60), nullable=False, server_default="UTC"),
        sa.Column("moments", sa.JSON, nullable=False),
        sa.Column("max_turns", sa.Integer, nullable=False),
        sa.Column("max_cost", sa.Float, nullable=False),
        sa.Column("allowed_commands", sa.JSON, nullable=False),
        sa.Column("allowed_domains", sa.JSON, nullable=False),
        sa.Column("conversation_mode", sa.String(10), nullable=False),
        sa.Column("conversation_id", sa.Integer, nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Column("running", sa.Boolean, nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True, index=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "scheduled_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("task_id", sa.Integer, sa.ForeignKey("scheduled_tasks.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("conversation_id", sa.Integer, nullable=True, index=True),
        sa.Column("goal_id", sa.Integer, nullable=True),
        sa.Column("trigger", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("cost", sa.Float, nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("scheduled_task_id", sa.Integer, nullable=True))
        batch.add_column(sa.Column("unread", sa.Boolean, nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("unread")
        batch.drop_column("scheduled_task_id")
    op.drop_table("scheduled_runs")
    op.drop_table("scheduled_tasks")
