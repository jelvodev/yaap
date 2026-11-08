"""The worker a delegated sub-run was given: a preset, Claude Code or Codex.

Revision ID: 0034_cli_workers
Revises: 0033_memory_import
Create Date: 2026-11-07
"""

import sqlalchemy as sa
from alembic import op

revision = "0034_cli_workers"
down_revision = "0033_memory_import"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch:
        batch.add_column(sa.Column("worker", sa.String(40), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_column("worker")
