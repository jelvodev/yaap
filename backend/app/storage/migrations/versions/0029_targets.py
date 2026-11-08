"""Machines over SSH as targets, such as this computer.

Revision ID: 0029_targets
Revises: 0028_backup_runs
Create Date: 2026-11-07
"""

import sqlalchemy as sa
from alembic import op

revision = "0029_targets"
down_revision = "0028_backup_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "targets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("environment_id", sa.Integer, sa.ForeignKey("environments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("port", sa.Integer, nullable=False, server_default="22"),
        sa.Column("user", sa.String(80), nullable=False),
        sa.Column("system", sa.String(20), nullable=False, server_default="unknown"),
        sa.Column("shell", sa.String(20), nullable=False, server_default="bash"),
        sa.Column("rights", sa.String(20), nullable=False, server_default="unknown"),
        sa.Column("folders", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("key_entry_id", sa.Integer, sa.ForeignKey("vault_entries.id", ondelete="SET NULL"), nullable=True),
        sa.Column("host_key", sa.Text, nullable=True),
        sa.Column("pending_host_key", sa.Text, nullable=True),
        sa.Column("allow_full_access", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("whole_computer", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_result", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_targets_environment_id", "targets", ["environment_id"])
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("target_id", sa.Integer, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("target_id")
    op.drop_table("targets")
