"""Reading the audit log: the rows of one environment, filtered, and their export.

The export holds what a row holds and nothing more: ids, names of projects, purposes, connections, models, data
classes and placeholder names. Conversation titles are left out, because a title is made from the first message and
could carry what the user typed.
"""

import csv
import io
import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.audit import AuditOutcome, AuditPurpose
from app.storage.models import AuditRecord, Project

EXPORT_COLUMNS = (
    "time",
    "purpose",
    "task",
    "project_id",
    "project",
    "conversation_id",
    "provider",
    "connection",
    "model",
    "data_classes",
    "secret_names",
    "outcome",
    "reason",
)


@dataclass(frozen=True)
class AuditFilter:
    # Both days included, in UTC.
    start: date | None = None
    end: date | None = None
    project_id: int | None = None
    model: str | None = None
    purpose: AuditPurpose | None = None
    # refused: the requests a check stopped, such as utility tasks the model policy skipped.
    outcome: AuditOutcome | None = None


@dataclass(frozen=True)
class AuditRow:
    id: int
    time: datetime
    purpose: str
    task: str | None
    project_id: int | None
    # The project's current name; None outside a project or when it was removed.
    project: str | None
    conversation_id: int | None
    provider: str
    connection: str
    model: str
    data_classes: list[str]
    secret_names: list[str]
    outcome: str
    reason: str | None


@dataclass(frozen=True)
class AuditPage:
    rows: list[AuditRow]
    total: int


def filtered(environment_id: int, where: AuditFilter) -> Select[tuple[AuditRecord]]:
    query = select(AuditRecord).where(AuditRecord.environment_id == environment_id)
    if where.start is not None:
        query = query.where(AuditRecord.created_at >= datetime.combine(where.start, time.min, UTC))
    if where.end is not None:
        query = query.where(AuditRecord.created_at < datetime.combine(where.end + timedelta(days=1), time.min, UTC))
    if where.project_id is not None:
        query = query.where(AuditRecord.project_id == where.project_id)
    if where.model:
        query = query.where(AuditRecord.model == where.model)
    if where.purpose is not None:
        query = query.where(AuditRecord.purpose == where.purpose)
    if where.outcome is not None:
        query = query.where(AuditRecord.outcome == where.outcome)
    return query


def list_audit(
    session: Session, environment_id: int, where: AuditFilter, limit: int | None = None, offset: int = 0
) -> AuditPage:
    """The environment's rows matching the filter, newest first; limit None returns them all (the export)."""
    query = filtered(environment_id, where)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    page = query.order_by(AuditRecord.created_at.desc(), AuditRecord.id.desc()).offset(offset)
    if limit is not None:
        page = page.limit(limit)
    records = list(session.scalars(page))
    project_ids = {record.project_id for record in records if record.project_id is not None}
    names: dict[int, str] = {}
    if project_ids:
        found = session.execute(select(Project.id, Project.name).where(Project.id.in_(project_ids)))
        names = {key: name for key, name in found}
    return AuditPage([to_row(record, names.get(record.project_id or 0)) for record in records], total)


def to_row(record: AuditRecord, project: str | None) -> AuditRow:
    return AuditRow(
        id=record.id,
        time=record.created_at,
        purpose=record.purpose,
        task=record.task,
        project_id=record.project_id,
        project=project,
        conversation_id=record.conversation_id,
        provider=record.provider,
        connection=record.connection,
        model=record.model,
        data_classes=list(record.data_classes or []),
        secret_names=list(record.secret_names or []),
        outcome=record.outcome,
        reason=record.reason,
    )


def audit_models(session: Session, environment_id: int) -> list[str]:
    """The models the environment's log names, for the filter."""
    query = select(AuditRecord.model).where(AuditRecord.environment_id == environment_id).distinct()
    return sorted(session.scalars(query))


def export_record(row: AuditRow) -> dict[str, object]:
    values = asdict(row)
    values.pop("id")
    values["time"] = row.time.isoformat()
    return {column: values[column] for column in EXPORT_COLUMNS}


def to_json(rows: list[AuditRow]) -> str:
    return json.dumps([export_record(row) for row in rows], indent=2, ensure_ascii=False)


def csv_cell(value: object) -> str:
    """A cell as text: lists joined with a space, and text a spreadsheet would read as a formula (a project named
    `=…`) quoted with an apostrophe."""
    text = " ".join(str(item) for item in value) if isinstance(value, list) else ("" if value is None else str(value))
    return f"'{text}" if text[:1] in ("=", "+", "-", "@") else text


def to_csv(rows: list[AuditRow]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(EXPORT_COLUMNS)
    for row in rows:
        writer.writerow([csv_cell(value) for value in export_record(row).values()])
    return buffer.getvalue()
