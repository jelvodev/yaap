"""A sandbox container per project as a target.

Revision ID: 0035_sandbox
Revises: 0034_cli_workers
Create Date: 2026-11-07
"""

import sqlalchemy as sa
from alembic import op

revision = "0035_sandbox"
down_revision = "0034_cli_workers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("targets") as batch:
        batch.add_column(sa.Column("kind", sa.String(20), nullable=False, server_default="ssh"))
        batch.add_column(sa.Column("project_id", sa.Integer, nullable=True))
        batch.add_column(sa.Column("network", sa.Boolean, nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key("fk_targets_project_id", "projects", ["project_id"], ["id"], ondelete="CASCADE")
        batch.create_unique_constraint("uq_targets_project_id", ["project_id"])


def downgrade() -> None:
    op.execute("DELETE FROM targets WHERE kind = 'sandbox'")
    with op.batch_alter_table("targets") as batch:
        batch.drop_constraint("uq_targets_project_id", type_="unique")
        batch.drop_constraint("fk_targets_project_id", type_="foreignkey")
        batch.drop_column("last_used_at")
        batch.drop_column("network")
        batch.drop_column("project_id")
        batch.drop_column("kind")
