"""A conversation can continue from a summary of an earlier one; it keeps a link back.

Revision ID: 0059_continued_conversations
Revises: 0057_eval_queue_items
Create Date: 2026-11-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0059_continued_conversations"
down_revision = "0057_eval_queue_items"
branch_labels = None
depends_on = None

CONSTRAINT = "fk_conversations_continued_from_id_conversations"
INDEX = "ix_conversations_continued_from_id"


def upgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("continued_from_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(CONSTRAINT, "conversations", ["continued_from_id"], ["id"], ondelete="SET NULL")
        batch.create_index(INDEX, ["continued_from_id"])


def downgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.drop_index(INDEX)
        batch.drop_constraint(CONSTRAINT, type_="foreignkey")
        batch.drop_column("continued_from_id")
