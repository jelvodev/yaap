"""The search index: chunks of messages, project memory and Blueprint files, with their embeddings.

`search_chunks` holds the text of each chunk and its embedding as a float32 blob; sqlite-vec compares the blobs at
search time. `chunk_search` is a full-text index over the memory and Blueprint chunks (messages have their own,
`message_search`). Rows go when their message, conversation, project or environment goes.

Revision ID: 0018_search_chunks
Revises: 0017_goals
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0018_search_chunks"
down_revision = "0017_goals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "search_chunks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("source_key", sa.String(400), nullable=False, index=True),
        sa.Column("chunk_no", sa.Integer, nullable=False),
        sa.Column("environment_id", sa.Integer, sa.ForeignKey("environments.id", ondelete="CASCADE"), index=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), index=True),
        sa.Column("conversation_id", sa.Integer, sa.ForeignKey("conversations.id", ondelete="CASCADE")),
        sa.Column("message_id", sa.Integer, sa.ForeignKey("messages.id", ondelete="CASCADE"), index=True),
        sa.Column("path", sa.String(200), nullable=True),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("model", sa.String(80), nullable=True),
        sa.Column("embedding", sa.LargeBinary, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute("CREATE VIRTUAL TABLE chunk_search USING fts5(text, tokenize = 'unicode61 remove_diacritics 2')")
    op.execute(
        """CREATE TRIGGER chunk_search_insert AFTER INSERT ON search_chunks WHEN new.kind != 'message' BEGIN
            INSERT INTO chunk_search(rowid, text) VALUES (new.id, new.text);
        END"""
    )
    op.execute(
        """CREATE TRIGGER chunk_search_delete AFTER DELETE ON search_chunks BEGIN
            DELETE FROM chunk_search WHERE rowid = old.id;
        END"""
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS chunk_search_insert")
    op.execute("DROP TRIGGER IF EXISTS chunk_search_delete")
    op.execute("DROP TABLE IF EXISTS chunk_search")
    op.drop_table("search_chunks")
