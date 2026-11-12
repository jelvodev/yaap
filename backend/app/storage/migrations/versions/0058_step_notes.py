"""A run step can carry a detail: a note's text and reason, or the reason a tool call asked or was refused.

Revision ID: 0058_step_notes
Revises: 0057_eval_queue_items
Create Date: 2026-11-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0058_step_notes"
down_revision = "0057_eval_queue_items"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("run_steps") as batch:
        batch.add_column(sa.Column("detail", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("run_steps") as batch:
        batch.drop_column("detail")
