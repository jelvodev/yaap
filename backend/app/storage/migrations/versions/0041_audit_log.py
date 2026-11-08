"""The audit log: one row per model request that leaves YAAP.

Revision ID: 0041_audit_log
Revises: 0039_hardening
Create Date: 2026-11-08
"""

import sqlalchemy as sa
from alembic import op

revision = "0041_audit_log"
down_revision = "0039_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("environment_id", sa.Integer, sa.ForeignKey("environments.id"), nullable=False),
        sa.Column("project_id", sa.Integer, nullable=True),
        sa.Column("conversation_id", sa.Integer, nullable=True),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("task", sa.String(40), nullable=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("connection", sa.String(40), nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("data_classes", sa.JSON, nullable=False),
        sa.Column("secret_names", sa.JSON, nullable=False),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(500), nullable=True),
    )
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])
    op.create_index("ix_audit_log_environment_id", "audit_log", ["environment_id"])


def downgrade() -> None:
    op.drop_table("audit_log")
