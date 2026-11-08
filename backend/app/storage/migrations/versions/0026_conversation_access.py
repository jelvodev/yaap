"""The access level of a conversation, chosen in the chat.

Revision ID: 0026_conversation_access
Revises: 0025_knowledge_sources
Create Date: 2026-11-07
"""

import sqlalchemy as sa
from alembic import op

revision = "0026_conversation_access"
down_revision = "0025_knowledge_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("access", sa.String(20), nullable=False, server_default="default"))


def downgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("access")
