"""Presets get a delegation switch.

Revision ID: 0060_preset_delegation
Revises: 0059_continued_conversations
Create Date: 2026-11-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0060_preset_delegation"
down_revision = "0059_continued_conversations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("model_presets") as batch:
        batch.add_column(sa.Column("delegation", sa.String(length=10), nullable=False, server_default="free"))


def downgrade() -> None:
    with op.batch_alter_table("model_presets") as batch:
        batch.drop_column("delegation")
