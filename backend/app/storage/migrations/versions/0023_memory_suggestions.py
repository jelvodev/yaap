"""Memory suggestions: entries the utility model proposes for the project memory.

Revision ID: 0023_memory_suggestions
Revises: 0022_knowledge
Create Date: 2026-11-06
"""

import sqlalchemy as sa
from alembic import op

revision = "0023_memory_suggestions"
down_revision = "0022_knowledge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_suggestions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("conversation_id", sa.Integer, nullable=True),
        sa.Column("section", sa.String(40), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("updates_entry", sa.Text, nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("memory_checked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("memory_checked_at")
    op.drop_table("memory_suggestions")
