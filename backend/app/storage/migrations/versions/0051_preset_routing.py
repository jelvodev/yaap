"""Preset routing: a preset routes each reply by the kind of message; a reply keeps its route.

Revision ID: 0051_preset_routing
Revises: 0044_replay
Create Date: 2026-11-08
"""

import sqlalchemy as sa
from alembic import op

revision = "0051_preset_routing"
down_revision = "0044_replay"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("model_presets") as batch:
        batch.add_column(sa.Column("routing", sa.Boolean, nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("routes", sa.JSON, nullable=True))
    with op.batch_alter_table("messages") as batch:
        batch.add_column(sa.Column("route", sa.JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("messages") as batch:
        batch.drop_column("route")
    with op.batch_alter_table("model_presets") as batch:
        batch.drop_column("routes")
        batch.drop_column("routing")
