"""Pinned skills per conversation.

Revision ID: 0009_pinned_skills
Revises: 0008_agents
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_pinned_skills"
down_revision = "0008_agents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("pinned_skills", sa.JSON, nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("pinned_skills")
