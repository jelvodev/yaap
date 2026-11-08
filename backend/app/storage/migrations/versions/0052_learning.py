"""Learning from the work: signals on run steps, learning proposals, and when a conversation's signals
were last reviewed.

Revision ID: 0052_learning
Revises: 0051_preset_routing
Create Date: 2026-11-08
"""

import sqlalchemy as sa
from alembic import op

revision = "0052_learning"
down_revision = "0051_preset_routing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("run_steps") as batch:
        batch.add_column(sa.Column("signal", sa.String(20), nullable=True))
        batch.add_column(sa.Column("signal_note", sa.String(500), nullable=True))
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("learning_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "learning_proposals",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", sa.Integer, nullable=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("level", sa.String(20), nullable=False),
        sa.Column("target", sa.String(200), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("diff", sa.Text, nullable=False),
        sa.Column("rationale", sa.Text, nullable=False),
        sa.Column("signals", sa.JSON, nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("branch", sa.String(120), nullable=True),
        sa.Column("measurement", sa.String(20), nullable=False),
        sa.Column("measurement_note", sa.Text, nullable=True),
        sa.Column("baseline_run_id", sa.Integer, nullable=True),
        sa.Column("variant_run_id", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_learning_proposals_project_id", "learning_proposals", ["project_id"])


def downgrade() -> None:
    op.drop_table("learning_proposals")
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("learning_checked_at")
    with op.batch_alter_table("run_steps") as batch:
        batch.drop_column("signal_note")
        batch.drop_column("signal")
