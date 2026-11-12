"""The project board's index: the board's state per project, its cards and versions as read from the board's files,
and the conversations that belong to a card.

Revision ID: 0062_board
Revises: 0061_plans
Create Date: 2026-11-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0062_board"
down_revision = "0061_plans"
branch_labels = None
depends_on = None


def project_column() -> sa.Column:
    return sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "board_state",
        sa.Column(
            "project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True, nullable=False
        ),
        sa.Column("storage", sa.String(12), nullable=False),
        sa.Column("head_commit", sa.String(64), nullable=True),
        sa.Column("last_pushed_commit", sa.String(64), nullable=True),
        sa.Column("next_card_id", sa.Integer, nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "board_cards",
        sa.Column("id", sa.Integer, primary_key=True),
        project_column(),
        sa.Column("card_id", sa.Integer, nullable=False),
        sa.Column("path", sa.String(200), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(10), nullable=True),
        sa.Column("board_column", sa.String(10), nullable=True),
        sa.Column("version", sa.String(40), nullable=True),
        sa.Column("origin_type", sa.String(12), nullable=True),
        sa.Column("origin_ref", sa.String(300), nullable=True),
        sa.Column("waits_for", sa.JSON, nullable=False),
        sa.Column("open_questions", sa.Integer, nullable=False),
        sa.Column("has_specification", sa.Boolean, nullable=False),
        sa.Column("cost_usd", sa.Float, nullable=False),
        sa.Column("plan_id", sa.Integer, nullable=True),
        sa.Column("review_id", sa.Integer, nullable=True),
        sa.Column("blob_sha", sa.String(64), nullable=False),
        sa.Column("broken", sa.Boolean, nullable=False),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("project_id", "card_id"),
    )
    op.create_index("ix_board_cards_project_id_board_column", "board_cards", ["project_id", "board_column"])
    op.create_index("ix_board_cards_project_id_version", "board_cards", ["project_id", "version"])
    op.create_table(
        "board_versions",
        sa.Column("id", sa.Integer, primary_key=True),
        project_column(),
        sa.Column("name", sa.String(40), nullable=False),
        sa.Column("state", sa.String(10), nullable=True),
        sa.Column("budget_usd", sa.Float, nullable=True),
        sa.Column("blob_sha", sa.String(64), nullable=False),
        sa.UniqueConstraint("project_id", "name"),
    )
    op.create_index("ix_board_versions_project_id", "board_versions", ["project_id"])
    op.create_table(
        "board_card_conversations",
        sa.Column("id", sa.Integer, primary_key=True),
        project_column(),
        sa.Column("card_id", sa.Integer, nullable=False),
        sa.Column(
            "conversation_id",
            sa.Integer,
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_board_card_conversations_project_id_card_id", "board_card_conversations", ["project_id", "card_id"]
    )


def downgrade() -> None:
    op.drop_table("board_card_conversations")
    op.drop_table("board_versions")
    op.drop_table("board_cards")
    op.drop_table("board_state")
