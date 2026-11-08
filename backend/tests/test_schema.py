"""The migrated schema matches the models: every model change has its migration."""

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from app.storage.db import MIGRATIONS_DIR, run_migrations
from app.storage.models import Base

# Full-text search tables, made with raw SQL in their migrations (FTS5 virtual tables and their shadow tables);
# they have no model.
FULL_TEXT_TABLES = ("message_search", "chunk_search")


def full_text_table(name: str | None) -> bool:
    return name is not None and any(name == table or name.startswith(f"{table}_") for table in FULL_TEXT_TABLES)


def include_name(name: str | None, type_: str, _parents: dict[str, str | None]) -> bool:
    return not (type_ == "table" and full_text_table(name))


def migrated_engine(folder: Path):  # type: ignore[no-untyped-def]
    url = f"sqlite:///{(folder / 'schema.db').as_posix()}"
    run_migrations(url)
    return create_engine(url)


def test_the_migrated_schema_matches_the_models(tmp_path: Path) -> None:
    engine = migrated_engine(tmp_path)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection, opts={"compare_type": True, "include_name": include_name, "render_as_batch": True}
            )
            differences = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()
    assert differences == []


def test_a_conversation_references_its_target(tmp_path: Path) -> None:
    engine = migrated_engine(tmp_path)
    try:
        keys = inspect(engine).get_foreign_keys("conversations")
    finally:
        engine.dispose()
    target = next(key for key in keys if key["constrained_columns"] == ["target_id"])
    assert target["referred_table"] == "targets" and target["options"].get("ondelete") == "SET NULL"


def test_a_conversation_whose_target_was_deleted_loses_the_stale_id(tmp_path: Path) -> None:
    url = f"sqlite:///{(tmp_path / 'before.db').as_posix()}"
    config = Config()
    config.set_main_option("script_location", MIGRATIONS_DIR.as_posix())
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0038_plugins")
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO environments (id, name, colour, created_at, slug) "
                    "VALUES (9, 'Work', '#123456', '2026-11-08', 'work')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO conversations (id, environment_id, title, provider, model, effort, created_at, updated_at, "
                    "target_id) VALUES (1, 9, 'Old', 'anthropic', 'claude-opus-5-5', 'high', '2026-11-08', '2026-11-08', 42)"
                )
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT title, target_id FROM conversations")).one() == ("Old", None)
    finally:
        engine.dispose()
