"""Initial schema: environments, credentials, conversations, messages, usage records.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "environments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(80), nullable=False, unique=True),
        sa.Column("colour", sa.String(7), nullable=False),
        sa.Column("uses_base_layer", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "credentials",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("environment_id", sa.Integer, sa.ForeignKey("environments.id"), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("encrypted_value", sa.LargeBinary, nullable=False),
        sa.Column("last_four", sa.String(4), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_tested_at", sa.DateTime(timezone=True)),
        sa.Column("last_test_result", sa.Text),
        sa.UniqueConstraint("environment_id", "kind"),
    )
    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("environment_id", sa.Integer, sa.ForeignKey("environments.id"), nullable=False, index=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("effort", sa.String(20), nullable=False),
        sa.Column("head_message_id", sa.Integer),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("conversation_id", sa.Integer, sa.ForeignKey("conversations.id"), nullable=False, index=True),
        sa.Column("parent_id", sa.Integer, sa.ForeignKey("messages.id")),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.JSON, nullable=False),
        sa.Column("provider_raw", sa.JSON),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("stop_reason", sa.String(40)),
        sa.Column("error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "usage_records",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("environment_id", sa.Integer, sa.ForeignKey("environments.id"), nullable=False, index=True),
        sa.Column("conversation_id", sa.Integer, sa.ForeignKey("conversations.id")),
        sa.Column("message_id", sa.Integer, sa.ForeignKey("messages.id")),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False),
        sa.Column("output_tokens", sa.Integer, nullable=False),
        sa.Column("cache_read_tokens", sa.Integer, nullable=False),
        sa.Column("cache_write_tokens", sa.Integer, nullable=False),
        sa.Column("cost", sa.Float),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("usage_records")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("credentials")
    op.drop_table("environments")
