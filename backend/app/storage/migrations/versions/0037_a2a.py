"""A2A tasks and the conversations of outside MCP clients.

Revision ID: 0037_a2a
Revises: 0036_authentication
Create Date: 2026-11-07
"""

import sqlalchemy as sa
from alembic import op

revision = "0037_a2a"
down_revision = "0036_authentication"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "a2a_tasks",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("context_id", sa.String(100), nullable=False, index=True),
        sa.Column("token_id", sa.Integer, nullable=False),
        sa.Column("environment_id", sa.Integer, nullable=False),
        sa.Column("project_id", sa.Integer, nullable=True),
        sa.Column(
            "conversation_id",
            sa.Integer,
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("user_message_id", sa.Integer, nullable=False),
        sa.Column("canceled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "mcp_client_conversations",
        sa.Column("token_id", sa.Integer, primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Integer,
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("mcp_client_conversations")
    op.drop_table("a2a_tasks")
