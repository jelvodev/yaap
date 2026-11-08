"""Steps of agent runs: one row per model call and per tool call.

Revision ID: 0042_run_steps
Revises: 0041_audit_log
Create Date: 2026-11-08
"""

import sqlalchemy as sa
from alembic import op

revision = "0042_run_steps"
down_revision = "0041_audit_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_steps",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("run_id", sa.Integer, sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Integer, nullable=False),
        sa.Column("model", sa.String(80), nullable=True),
        sa.Column("input_tokens", sa.Integer, nullable=False),
        sa.Column("output_tokens", sa.Integer, nullable=False),
        sa.Column("cache_read_tokens", sa.Integer, nullable=False),
        sa.Column("cache_write_tokens", sa.Integer, nullable=False),
        sa.Column("cost", sa.Float, nullable=True),
        sa.Column("model_calls", sa.Integer, nullable=True),
        sa.Column("snapshot", sa.String(64), nullable=True),
        sa.Column("tool", sa.String(120), nullable=True),
        sa.Column("summary", sa.String(300), nullable=True),
        sa.Column("output_ref", sa.Integer, nullable=True),
        sa.Column("is_error", sa.Boolean, nullable=False),
        sa.Column("approval", sa.String(20), nullable=True),
    )
    op.create_index("ix_run_steps_run_id", "run_steps", ["run_id"])


def downgrade() -> None:
    op.drop_table("run_steps")
