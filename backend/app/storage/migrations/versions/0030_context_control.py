"""Context control: a context limit per conversation and a reply length per preset.

Revision ID: 0030_context_control
Revises: 0029_targets
Create Date: 2026-11-07
"""

import sqlalchemy as sa
from alembic import op

revision = "0030_context_control"
down_revision = "0029_targets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("context_limit", sa.Integer, nullable=True))
    with op.batch_alter_table("model_presets") as batch:
        batch.add_column(sa.Column("max_output_tokens", sa.Integer, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("model_presets") as batch:
        batch.drop_column("max_output_tokens")
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("context_limit")
