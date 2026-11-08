"""Record how a reply was billed: metered (API key) or covered by a subscription (Claude Code).

Revision ID: 0003_usage_billing
Revises: 0002_settings
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_usage_billing"
down_revision = "0002_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("usage_records") as batch:
        batch.add_column(sa.Column("billing", sa.String(20), nullable=False, server_default="metered"))


def downgrade() -> None:
    with op.batch_alter_table("usage_records") as batch:
        batch.drop_column("billing")
