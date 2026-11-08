"""Record the context size of a reply's last model call; an agent run with tool calls makes several calls.

Revision ID: 0005_usage_context
Revises: 0004_projects
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_usage_context"
down_revision = "0004_projects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("usage_records") as batch:
        batch.add_column(sa.Column("context_tokens", sa.Integer))


def downgrade() -> None:
    with op.batch_alter_table("usage_records") as batch:
        batch.drop_column("context_tokens")
