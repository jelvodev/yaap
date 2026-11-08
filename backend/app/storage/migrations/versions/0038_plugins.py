"""Plugins switched on or off in Settings.

Revision ID: 0038_plugins
Revises: 0037_a2a
Create Date: 2026-11-07
"""

import sqlalchemy as sa
from alembic import op

revision = "0038_plugins"
down_revision = "0037_a2a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plugins",
        sa.Column("name", sa.String(40), primary_key=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("loading_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("plugins")
