"""Triggers on the board: a scheduled task's settings for the board_trigger action.

Revision ID: 0064_board_triggers
Revises: 0063_review_branches
Create Date: 2026-11-13
"""

import sqlalchemy as sa
from alembic import op

revision = "0064_board_triggers"
down_revision = "0063_review_branches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("scheduled_tasks") as batch:
        batch.add_column(sa.Column("config", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("scheduled_tasks") as batch:
        batch.drop_column("config")
