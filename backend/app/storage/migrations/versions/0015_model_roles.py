"""Model roles in presets and the preset a conversation started with.

Revision ID: 0015_model_roles
Revises: 0014_conversation_archive
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0015_model_roles"
down_revision = "0014_conversation_archive"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("model_presets") as batch:
        batch.add_column(sa.Column("worker_model", sa.String(80), nullable=True))
        batch.add_column(sa.Column("worker_effort", sa.String(20), nullable=True))
        batch.add_column(sa.Column("utility_model", sa.String(80), nullable=True))
        batch.add_column(sa.Column("utility_effort", sa.String(20), nullable=True))
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("preset", sa.String(40), nullable=True))
    # The seeded presets that delegate get a worker, as new environments do.
    op.execute(
        "UPDATE model_presets SET worker_model = 'claude-sonnet-5-5', worker_effort = 'low' "
        "WHERE name = 'Balanced' AND worker_model IS NULL"
    )
    op.execute(
        "UPDATE model_presets SET worker_model = 'claude-sonnet-5-5', worker_effort = 'medium' "
        "WHERE name = 'Deep' AND worker_model IS NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("preset")
    with op.batch_alter_table("model_presets") as batch:
        batch.drop_column("utility_effort")
        batch.drop_column("utility_model")
        batch.drop_column("worker_effort")
        batch.drop_column("worker_model")
