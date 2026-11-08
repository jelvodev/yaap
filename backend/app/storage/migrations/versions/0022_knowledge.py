"""Project knowledge: documents imported into a project as searchable reference material.

Revision ID: 0022_knowledge
Revises: 0021_secrets
Create Date: 2026-11-06
"""

import sqlalchemy as sa
from alembic import op

revision = "0022_knowledge"
down_revision = "0021_secrets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("size", sa.Integer, nullable=False),
        sa.Column("chars", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("knowledge_documents")
