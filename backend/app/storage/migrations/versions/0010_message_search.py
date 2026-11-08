"""Full-text search over message text.

An FTS5 table keyed by message id holds the visible text of each message: its text blocks and the names of its
attachments. Triggers keep it in step with the messages table, so every write path is covered.

Revision ID: 0010_message_search
Revises: 0009_pinned_skills
Create Date: 2026-11-05
"""

from alembic import op

revision = "0010_message_search"
down_revision = "0009_pinned_skills"
branch_labels = None
depends_on = None

MESSAGE_TEXT = """(
    SELECT group_concat(coalesce(json_extract(block.value, '$.text'), json_extract(block.value, '$.name')), char(10))
    FROM json_each({row}.content) AS block
    WHERE json_extract(block.value, '$.type') IN ('text', 'attachment')
)"""


def upgrade() -> None:
    op.execute("CREATE VIRTUAL TABLE message_search USING fts5(text, tokenize = 'unicode61 remove_diacritics 2')")
    op.execute(f"INSERT INTO message_search(rowid, text) SELECT id, coalesce({MESSAGE_TEXT.format(row='messages')}, '') FROM messages")
    op.execute(
        f"""CREATE TRIGGER message_search_insert AFTER INSERT ON messages BEGIN
            INSERT INTO message_search(rowid, text) VALUES (new.id, coalesce({MESSAGE_TEXT.format(row='new')}, ''));
        END"""
    )
    op.execute(
        f"""CREATE TRIGGER message_search_update AFTER UPDATE OF content ON messages BEGIN
            DELETE FROM message_search WHERE rowid = old.id;
            INSERT INTO message_search(rowid, text) VALUES (new.id, coalesce({MESSAGE_TEXT.format(row='new')}, ''));
        END"""
    )
    op.execute(
        """CREATE TRIGGER message_search_delete AFTER DELETE ON messages BEGIN
            DELETE FROM message_search WHERE rowid = old.id;
        END"""
    )


def downgrade() -> None:
    for trigger in ("message_search_insert", "message_search_update", "message_search_delete"):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    op.execute("DROP TABLE IF EXISTS message_search")
