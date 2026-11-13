"""CHECK constraints on goal statuses and on message roles and statuses, added only where the data is clean.

The upgrade never fails because of stored data: obvious values (stray spaces or capitals) are corrected, and a table
that still holds values outside the allowed set gets no constraint; those values are checked when stored instead.

Revision ID: 0065_status_checks
Revises: 0064_board_triggers
Create Date: 2026-11-13
"""

import logging
from collections.abc import Callable

import sqlalchemy as sa
from alembic import op
from alembic.operations import BatchOperations

revision = "0065_status_checks"
down_revision = "0064_board_triggers"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

# A snapshot of the allowed values when this migration was written. Migrations are history: they must not import
# application code, so a later change of the lists in the application never changes what this migration does.
CHECKS: dict[str, dict[str, tuple[str, ...]]] = {
    "goals": {"status": ("active", "paused", "done", "blocked", "limit", "stopped")},
    "messages": {
        "status": ("complete", "incomplete", "refused", "error"),
        "role": ("user", "assistant"),
    },
}
WHITESPACE = "' ' || char(9) || char(10) || char(13)"


def constraint_name(table: str, column: str) -> str:
    return f"ck_{table}_{column}"


def value_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def out_of_set(column: str, values: tuple[str, ...]) -> str:
    return f"({column} IS NULL OR {column} NOT IN ({value_list(values)}))"


def clean_column(table: str, column: str, values: tuple[str, ...]) -> int:
    """Correct obvious values in place; returns how many rows still hold a value outside the set."""
    bind = op.get_bind()
    tidy = f"lower(trim({column}, {WHITESPACE}))"
    mapped = bind.execute(
        sa.text(
            f"UPDATE {table} SET {column} = {tidy} "
            f"WHERE {column} IS NOT NULL AND {column} != {tidy} AND {tidy} IN ({value_list(values)})"
        )
    ).rowcount
    if mapped:
        logger.info("%s.%s: %d value(s) with stray spaces or capitals corrected.", table, column, mapped)
    bad = bind.execute(
        sa.text(f"SELECT {column}, COUNT(*) FROM {table} WHERE {out_of_set(column, values)} GROUP BY {column}")
    ).all()
    if bad:
        shown = ", ".join(f"{str(value)[:40]!r} x{count}" for value, count in bad)
        total = sum(count for _, count in bad)
        logger.warning("%s.%s: %d row(s) hold values outside the allowed set: %s", table, column, total, shown)
        return total
    return 0


def table_triggers(table: str) -> dict[str, str]:
    """The table's triggers by name with their CREATE statements: SQLite drops them when a batch change rebuilds the
    table (the message search index depends on the ones on messages)."""
    rows = op.get_bind().execute(
        sa.text("SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND tbl_name = :table AND sql IS NOT NULL"),
        {"table": table},
    )
    return {row[0]: row[1] for row in rows}


def rebuild_with(table: str, change: Callable[[BatchOperations], None]) -> None:
    triggers = table_triggers(table)
    with op.batch_alter_table(table) as batch:
        change(batch)
    existing = table_triggers(table)
    for name, statement in triggers.items():
        if name not in existing:
            op.execute(statement)


def upgrade() -> None:
    for table, columns in CHECKS.items():
        remaining = sum(clean_column(table, column, values) for column, values in columns.items())
        if remaining:
            logger.warning("%s: no CHECK constraints added; the values are checked when stored instead.", table)
            continue

        def add(batch: BatchOperations, table: str = table, columns: dict[str, tuple[str, ...]] = columns) -> None:
            for column, values in columns.items():
                batch.create_check_constraint(constraint_name(table, column), f"{column} IN ({value_list(values)})")

        rebuild_with(table, add)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table, columns in CHECKS.items():
        present = {check["name"] for check in inspector.get_check_constraints(table)}
        names = [constraint_name(table, column) for column in columns if constraint_name(table, column) in present]
        if not names:
            continue

        def drop(batch: BatchOperations, names: list[str] = names) -> None:
            for name in names:
                batch.drop_constraint(name, type_="check")

        rebuild_with(table, drop)
