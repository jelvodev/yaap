"""A run started from a queued /eval remembers the queue item, so one item never starts two runs.

Revision ID: 0057_eval_queue_items
Revises: 0056_second_opinions
Create Date: 2026-11-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0057_eval_queue_items"
down_revision = "0056_second_opinions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("eval_runs") as batch:
        batch.add_column(sa.Column("queue_item_id", sa.Integer(), nullable=True))
        batch.create_unique_constraint("uq_eval_runs_queue_item_id", ["queue_item_id"])


    # Queue ids must not repeat once a row is deleted: a run points at the id of the item it came from.
    with op.batch_alter_table("queued_messages", recreate="always", table_kwargs={"sqlite_autoincrement": True}):
        pass


def downgrade() -> None:
    with op.batch_alter_table("queued_messages", recreate="always"):
        pass
    with op.batch_alter_table("eval_runs") as batch:
        batch.drop_constraint("uq_eval_runs_queue_item_id", type_="unique")
        batch.drop_column("queue_item_id")
