"""Authentication: the local account and the browser sessions.

Revision ID: 0036_authentication
Revises: 0035_sandbox
Create Date: 2026-11-07
"""

import sqlalchemy as sa
from alembic import op

revision = "0036_authentication"
down_revision = "0035_sandbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("password_hash", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("csrf_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_agent", sa.String(200), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_table("auth_sessions")
    op.drop_table("accounts")
