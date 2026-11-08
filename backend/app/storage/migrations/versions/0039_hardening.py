"""The foreign key of conversations.target_id, which the model has and migration 0029 left out.

Found by the test that compares the migrated schema with the models. A conversation whose target was deleted kept the
target's id; it is cleared first, as the key's ON DELETE SET NULL would have done.

Revision ID: 0039_hardening
Revises: 0038_plugins
Create Date: 2026-11-08
"""

from alembic import op

revision = "0039_hardening"
down_revision = "0038_plugins"
branch_labels = None
depends_on = None

CONSTRAINT = "fk_conversations_target_id_targets"


def upgrade() -> None:
    op.execute(
        "UPDATE conversations SET target_id = NULL "
        "WHERE target_id IS NOT NULL AND target_id NOT IN (SELECT id FROM targets)"
    )
    with op.batch_alter_table("conversations") as batch:
        batch.create_foreign_key(CONSTRAINT, "targets", ["target_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.drop_constraint(CONSTRAINT, type_="foreignkey")
