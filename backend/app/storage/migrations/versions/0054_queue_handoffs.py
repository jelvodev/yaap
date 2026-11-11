"""Durable queue handoffs for send-now and steering.

Revision ID: 0054_queue_handoffs
Revises: 0053_reviews
Create Date: 2026-11-11
"""

import sqlalchemy as sa
from alembic import op

revision = "0054_queue_handoffs"
down_revision = "0053_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("queued_messages") as batch:
        batch.add_column(sa.Column("send_now_job_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("steering_run_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("steering_claimed", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.create_index("ix_queued_messages_send_now_job_id", ["send_now_job_id"])
        batch.create_index("ix_queued_messages_steering_run_id", ["steering_run_id"])


def downgrade() -> None:
    with op.batch_alter_table("queued_messages") as batch:
        batch.drop_index("ix_queued_messages_steering_run_id")
        batch.drop_index("ix_queued_messages_send_now_job_id")
        batch.drop_column("steering_run_id")
        batch.drop_column("steering_claimed")
        batch.drop_column("send_now_job_id")
