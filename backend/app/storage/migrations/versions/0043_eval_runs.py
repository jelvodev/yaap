"""Eval runs: a run of a project's eval cases and one result per play of a case; a scheduled task
can run the eval cases.

Revision ID: 0043_eval_runs
Revises: 0042_run_steps
Create Date: 2026-11-08
"""

import sqlalchemy as sa
from alembic import op

revision = "0043_eval_runs"
down_revision = "0042_run_steps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("environment_id", sa.Integer, sa.ForeignKey("environments.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("trigger", sa.String(20), nullable=False),
        sa.Column("case_name", sa.String(60), nullable=True),
        sa.Column("repeats", sa.Integer, nullable=False),
        sa.Column("variant", sa.JSON, nullable=True),
        sa.Column("baseline_run_id", sa.Integer, nullable=True),
        sa.Column("scheduled_run_id", sa.Integer, nullable=True),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_eval_runs_project_id", "eval_runs", ["project_id"])
    op.create_table(
        "eval_results",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("run_id", sa.Integer, sa.ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("case_name", sa.String(60), nullable=False),
        sa.Column("repeat", sa.Integer, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("checks", sa.JSON, nullable=False),
        sa.Column("detail", sa.Text, nullable=True),
        sa.Column("model", sa.String(80), nullable=True),
        sa.Column("cost", sa.Float, nullable=False),
        sa.Column("tokens", sa.Integer, nullable=False),
        sa.Column("steps", sa.Integer, nullable=False),
        sa.Column("seconds", sa.Float, nullable=False),
        sa.Column("asked", sa.Integer, nullable=False),
        sa.Column("scorer_cost", sa.Float, nullable=False),
        sa.Column(
            "conversation_id", sa.Integer, sa.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("agent_run_id", sa.Integer, sa.ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("workspace", sa.String(300), nullable=True),
        sa.Column("workspace_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_eval_results_run_id", "eval_results", ["run_id"])
    with op.batch_alter_table("scheduled_tasks") as batch:
        batch.add_column(sa.Column("action", sa.String(20), nullable=False, server_default="prompt"))
        batch.add_column(sa.Column("eval_case", sa.String(60), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("scheduled_tasks") as batch:
        batch.drop_column("eval_case")
        batch.drop_column("action")
    op.drop_table("eval_results")
    op.drop_table("eval_runs")
