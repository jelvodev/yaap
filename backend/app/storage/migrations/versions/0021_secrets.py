"""Secrets for placeholders: named values the agent uses without seeing them.

Revision ID: 0021_secrets
Revises: 0020_checkpoints
Create Date: 2026-11-06
"""

import sqlalchemy as sa
from alembic import op

revision = "0021_secrets"
down_revision = "0020_checkpoints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "secrets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("environment_id", sa.Integer, sa.ForeignKey("environments.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("encrypted_value", sa.LargeBinary, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("secrets")
