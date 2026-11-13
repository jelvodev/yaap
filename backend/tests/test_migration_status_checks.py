"""Migration 0065 adds CHECK constraints on statuses only where the data is clean, and never fails on dirty data."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect, text

from app.storage.db import MIGRATIONS_DIR

SEARCH_TRIGGERS = {"message_search_insert", "message_search_update", "message_search_delete"}


def alembic_config(url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", MIGRATIONS_DIR.as_posix())
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture
def database(tmp_path: Path) -> Iterator[tuple[Config, Engine]]:
    url = f"sqlite:///{(tmp_path / 'checks.db').as_posix()}"
    config = alembic_config(url)
    command.upgrade(config, "0064_board_triggers")
    engine = create_engine(url)
    yield config, engine
    engine.dispose()


def insert_conversation(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO environments (id, name, colour, created_at, slug) VALUES (1, 'W', '#123456', 'x', 'w')")
        )
        connection.execute(
            text(
                "INSERT INTO conversations (id, environment_id, title, provider, model, effort, created_at, "
                "updated_at) "
                "VALUES (1, 1, 'T', 'anthropic', 'claude-opus-5-5', 'high', '2026-11-08', '2026-11-08')"
            )
        )


def insert_message(engine: Engine, message_id: int, role: str, status: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO messages (id, conversation_id, role, content, status, created_at) "
                "VALUES (:id, 1, :role, '[]', :status, '2026-11-08')"
            ),
            {"id": message_id, "role": role, "status": status},
        )


def insert_goal(engine: Engine, goal_id: int, status: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO goals (id, conversation_id, objective, status, max_turns, max_cost, turns, created_at) "
                "VALUES (:id, 1, 'o', :status, 5, 1.0, 1, '2026-11-08')"
            ),
            {"id": goal_id, "status": status},
        )


def check_names(engine: Engine, table: str) -> set[str | None]:
    return {check["name"] for check in inspect(engine).get_check_constraints(table)}


def trigger_names(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT name FROM sqlite_master WHERE type = 'trigger'"))
        return {row[0] for row in rows}


def test_dirty_rows_do_not_fail_the_upgrade_and_get_no_constraint(database: tuple[Config, Engine]) -> None:
    config, engine = database
    insert_conversation(engine)
    insert_message(engine, 1, "user", "Complete ")
    insert_message(engine, 2, "assistant", "weird-status")
    insert_goal(engine, 1, " Done")
    insert_goal(engine, 2, "done")

    command.upgrade(config, "0065_status_checks")

    with engine.connect() as connection:
        statuses = dict(connection.execute(text("SELECT id, status FROM messages")).all())
        goal_statuses = dict(connection.execute(text("SELECT id, status FROM goals")).all())
    assert statuses == {1: "complete", 2: "weird-status"}
    assert goal_statuses == {1: "done", 2: "done"}
    # The messages table holds a value no one can map, so it gets no constraint; the clean goals table does.
    assert not {name for name in check_names(engine, "messages") if name and name.startswith("ck_messages_")}
    assert {"ck_goals_status"} <= check_names(engine, "goals")
    assert SEARCH_TRIGGERS <= trigger_names(engine)


def test_a_clean_database_gets_the_check_constraints_and_keeps_the_search_triggers(
    database: tuple[Config, Engine],
) -> None:
    config, engine = database
    insert_conversation(engine)
    insert_message(engine, 1, "user", "complete")
    insert_goal(engine, 1, "active")

    command.upgrade(config, "0065_status_checks")

    assert {"ck_messages_status", "ck_messages_role"} <= check_names(engine, "messages")
    assert "ck_goals_status" in check_names(engine, "goals")
    assert SEARCH_TRIGGERS <= trigger_names(engine)
    with pytest.raises(Exception, match="CHECK"):
        insert_goal(engine, 2, "typo")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM messages")).scalar_one() == 1
