"""Reviews of a build: the commit and branch they compare, the card they belong to and the evidence per criterion.

Revision ID: 0063_review_branches
Revises: 0062_board
Create Date: 2026-11-13
"""

import sqlalchemy as sa
from alembic import op

revision = "0063_review_branches"
down_revision = "0062_board"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("reviews") as batch:
        batch.add_column(sa.Column("base_commit", sa.String(length=40), nullable=True))
        batch.add_column(sa.Column("branch", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("card_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("evidence", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("reviews") as batch:
        batch.drop_column("evidence")
        batch.drop_column("card_id")
        batch.drop_column("branch")
        batch.drop_column("base_commit")
