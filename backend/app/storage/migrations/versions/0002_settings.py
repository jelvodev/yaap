"""Settings with a scope (global, environment, project); drop the unused base-layer flag.

Revision ID: 0002_settings
Revises: 0001_initial
Create Date: 2026-11-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_settings"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("environment_id", sa.Integer, sa.ForeignKey("environments.id"), index=True),
        sa.Column("project_id", sa.Integer),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("value", sa.JSON, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    with op.batch_alter_table("environments") as batch:
        batch.drop_column("uses_base_layer")


def downgrade() -> None:
    with op.batch_alter_table("environments") as batch:
        batch.add_column(sa.Column("uses_base_layer", sa.Boolean, nullable=False, server_default=sa.false()))
    op.drop_table("settings")
