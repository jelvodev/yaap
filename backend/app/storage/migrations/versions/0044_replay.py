"""Replay: a replay of one model call of a run, and pins that keep a checkpoint from being pruned.

Revision ID: 0044_replay
Revises: 0043_eval_runs
Create Date: 2026-11-08
"""

import sqlalchemy as sa
from alembic import op

revision = "0044_replay"
down_revision = "0043_eval_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "checkpoint_pins",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "checkpoint_id", sa.Integer, sa.ForeignKey("checkpoints.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("owner", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("checkpoint_id", "owner"),
    )
    op.create_index("ix_checkpoint_pins_checkpoint_id", "checkpoint_pins", ["checkpoint_id"])
    op.create_table(
        "replays",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("environment_id", sa.Integer, sa.ForeignKey("environments.id"), nullable=False),
        sa.Column(
            "source_conversation_id",
            sa.Integer,
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_run_id", sa.Integer, sa.ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_step_id", sa.Integer, nullable=False),
        sa.Column("step_position", sa.Integer, nullable=False),
        sa.Column("snapshot", sa.String(64), nullable=False),
        sa.Column("checkpoint_id", sa.Integer, sa.ForeignKey("checkpoints.id", ondelete="SET NULL"), nullable=True),
        sa.Column("variant", sa.JSON, nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("detail", sa.Text, nullable=True),
        sa.Column(
            "conversation_id", sa.Integer, sa.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("agent_run_id", sa.Integer, sa.ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("asked", sa.Integer, nullable=False),
        sa.Column("eval_case", sa.String(60), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_replays_project_id", "replays", ["project_id"])
    op.create_index("ix_replays_source_conversation_id", "replays", ["source_conversation_id"])


def downgrade() -> None:
    op.drop_table("replays")
    op.drop_table("checkpoint_pins")
