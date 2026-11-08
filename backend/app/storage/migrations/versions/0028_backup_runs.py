"""Backups on a schedule and their destinations.

Revision ID: 0028_backup_runs
Revises: 0027_vault
Create Date: 2026-11-07
"""

import sqlalchemy as sa
from alembic import op

revision = "0028_backup_runs"
down_revision = "0027_vault"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backup_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("trigger", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("name", sa.String(60), nullable=True),
        sa.Column("size", sa.Integer, nullable=True),
        sa.Column("results", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("error", sa.Text, nullable=True),
    )
    op.create_index("ix_backup_runs_started_at", "backup_runs", ["started_at"])


def downgrade() -> None:
    op.drop_table("backup_runs")
