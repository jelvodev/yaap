"""Utility calls record which task they served and a short result, so the chat can show them in place.

Revision ID: 0011_utility_tasks
Revises: 0010_message_search
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_utility_tasks"
down_revision = "0010_message_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("usage_records") as batch:
        batch.add_column(sa.Column("task", sa.String(40)))
        batch.add_column(sa.Column("summary", sa.String(300)))


def downgrade() -> None:
    with op.batch_alter_table("usage_records") as batch:
        batch.drop_column("summary")
        batch.drop_column("task")
