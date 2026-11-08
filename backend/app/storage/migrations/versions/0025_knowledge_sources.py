"""Knowledge from web pages and from a project folder.

Revision ID: 0025_knowledge_sources
Revises: 0024_scheduled_tasks
Create Date: 2026-11-06
"""

import sqlalchemy as sa
from alembic import op

revision = "0025_knowledge_sources"
down_revision = "0024_scheduled_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("knowledge_documents") as batch:
        batch.add_column(sa.Column("source_url", sa.Text, nullable=True))
        batch.add_column(sa.Column("source_path", sa.Text, nullable=True))
        batch.add_column(sa.Column("content_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("knowledge_documents") as batch:
        batch.drop_column("fetched_at")
        batch.drop_column("content_hash")
        batch.drop_column("source_path")
        batch.drop_column("source_url")
