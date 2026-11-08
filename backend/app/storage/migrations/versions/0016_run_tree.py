"""Delegated sub-runs: the run that started them, their task and what they did.

Revision ID: 0016_run_tree
Revises: 0015_model_roles
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0016_run_tree"
down_revision = "0015_model_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch:
        batch.add_column(sa.Column("parent_run_id", sa.Integer, nullable=True))
        batch.add_column(sa.Column("task", sa.Text, nullable=True))
        batch.add_column(sa.Column("transcript", sa.JSON, nullable=True))
        batch.create_index("ix_agent_runs_parent_run_id", ["parent_run_id"])


def downgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_index("ix_agent_runs_parent_run_id")
        batch.drop_column("transcript")
        batch.drop_column("task")
        batch.drop_column("parent_run_id")
