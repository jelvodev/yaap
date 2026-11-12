"""The Blueprint version of each run and of each replay.

Revision ID: 0055_blueprint_versions
Revises: 0054_queue_handoffs
Create Date: 2026-11-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0055_blueprint_versions"
down_revision = "0054_queue_handoffs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch:
        batch.add_column(sa.Column("blueprint_versions", sa.JSON(), nullable=True))
    with op.batch_alter_table("replays") as batch:
        batch.add_column(sa.Column("blueprint", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("replays") as batch:
        batch.drop_column("blueprint")
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_column("blueprint_versions")
