"""Second opinions: a review can be about one reply, and the check of an answer needs no project.

Revision ID: 0056_second_opinions
Revises: 0055_blueprint_versions
Create Date: 2026-11-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0056_second_opinions"
down_revision = "0055_blueprint_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("reviews") as batch:
        batch.add_column(sa.Column("message_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("same_vendor", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("agrees", sa.Boolean(), nullable=True))
        batch.add_column(sa.Column("alternative", sa.Text(), nullable=True))
        batch.alter_column("project_id", existing_type=sa.Integer(), nullable=True)
        batch.create_foreign_key("fk_reviews_message_id", "messages", ["message_id"], ["id"], ondelete="CASCADE")
        batch.create_index("ix_reviews_message_id", ["message_id"])


def downgrade() -> None:
    # A check of an answer without a project has nowhere to go back to.
    op.execute("DELETE FROM reviews WHERE project_id IS NULL")
    with op.batch_alter_table("reviews") as batch:
        batch.drop_index("ix_reviews_message_id")
        batch.drop_constraint("fk_reviews_message_id", type_="foreignkey")
        batch.alter_column("project_id", existing_type=sa.Integer(), nullable=False)
        batch.drop_column("alternative")
        batch.drop_column("agrees")
        batch.drop_column("same_vendor")
        batch.drop_column("message_id")
