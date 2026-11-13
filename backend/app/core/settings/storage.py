"""Reading and writing stored setting values and resolving them across levels."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings.setting_types import ResolvedSetting, SettingDefinition, Source
from app.storage.models import Setting


def find_setting_row(
    session: Session, key: str, environment_id: int | None, project_id: int | None = None
) -> Setting | None:
    environment_filter = Setting.environment_id.is_(None) if environment_id is None else Setting.environment_id == environment_id
    project_filter = Setting.project_id.is_(None) if project_id is None else Setting.project_id == project_id
    return session.scalar(select(Setting).where(Setting.key == key, environment_filter, project_filter))


def resolve_setting(
    session: Session, definition: SettingDefinition, environment_id: int | None, project_id: int | None = None
) -> ResolvedSetting:
    """The value at a level and where it comes from. The nearest level wins; merged settings combine all levels."""
    rows: list[tuple[Source, Any]] = []
    if "global" in definition.levels and (row := find_setting_row(session, definition.key, None)) is not None:
        rows.append(("global", row.value))
    if environment_id is not None and "environment" in definition.levels:
        if (row := find_setting_row(session, definition.key, environment_id)) is not None:
            rows.append(("environment", row.value))
    if environment_id is not None and project_id is not None and "project" in definition.levels:
        if (row := find_setting_row(session, definition.key, environment_id, project_id)) is not None:
            rows.append(("project", row.value))

    requested: Source = "project" if project_id is not None else "environment" if environment_id is not None else "global"
    above = [(source, value) for source, value in rows if source != requested]
    own = next((value for source, value in rows if source == requested), None)

    def combine(levels: list[tuple[Source, Any]]) -> tuple[Any, Source]:
        if not levels:
            return definition.default, "default"
        if definition.merge:
            merged = dict(definition.default or {})
            for _source, value in levels:
                merged.update(value or {})
            return merged, levels[-1][0]
        return levels[-1][1], levels[-1][0]

    inherited_value, inherited_source = combine(above)
    value, source = combine(rows)
    return ResolvedSetting(definition, value, source, inherited_value, inherited_source, own)


def store_setting(
    session: Session, definition: SettingDefinition, environment_id: int | None, value: Any, project_id: int | None = None
) -> None:
    row = find_setting_row(session, definition.key, environment_id, project_id)
    if row is None:
        row = Setting(key=definition.key, environment_id=environment_id, project_id=project_id)
        session.add(row)
    row.value = value
    row.updated_at = datetime.now(UTC)
    session.commit()


def clear_setting(
    session: Session, definition: SettingDefinition, environment_id: int | None, project_id: int | None = None
) -> None:
    row = find_setting_row(session, definition.key, environment_id, project_id)
    if row is not None:
        session.delete(row)
        session.commit()
