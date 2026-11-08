"""An empty revision, kept so that the revision chain of existing databases stays unbroken.

Revision ID: 0019_blueprint_search_kind
Revises: 0018_search_chunks
Create Date: 2026-11-05
"""

revision = "0019_blueprint_search_kind"
down_revision = "0018_search_chunks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
