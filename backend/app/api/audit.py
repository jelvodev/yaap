"""The audit log of an environment: which model received which data for what."""

from dataclasses import asdict
from datetime import UTC, date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel

from app.api.dependencies import EnvironmentDependency, SessionDependency
from app.core.audit import AUDIT_PURPOSES, DATA_CLASSES, AuditOutcome, AuditPurpose, DataClass
from app.core.audit_report import AuditFilter, audit_models, list_audit, to_csv, to_json

router = APIRouter(tags=["audit"])


class AuditEntryOut(BaseModel):
    id: int
    time: datetime
    purpose: AuditPurpose
    # Utility calls: the task, such as title or commit_message.
    task: str | None
    project_id: int | None
    # The project's current name; None outside a project or when it was removed.
    project: str | None
    conversation_id: int | None
    provider: str
    connection: str
    model: str
    data_classes: list[DataClass]
    # Secrets by placeholder name only; values are never recorded.
    secret_names: list[str]
    outcome: AuditOutcome
    reason: str | None


class AuditLogOut(BaseModel):
    entries: list[AuditEntryOut]
    # Rows matching the filter, of which entries is one page.
    total: int
    # The choices for the filters: the models this environment's log names, every purpose and data class.
    models: list[str]
    purposes: list[AuditPurpose]
    data_classes: list[DataClass]


def audit_filter(
    start: date | None = None,
    end: date | None = None,
    project_id: int | None = None,
    model: str | None = Query(default=None, max_length=80),
    purpose: AuditPurpose | None = None,
) -> AuditFilter:
    """The filter from the query; start and end are days in UTC, both included."""
    if start is not None and end is not None and end < start:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The period ends before it starts.")
    return AuditFilter(start, end, project_id, model or None, purpose)


AuditFilterDependency = Annotated[AuditFilter, Depends(audit_filter)]


@router.get("/environments/{environment_id}/audit", response_model=AuditLogOut)
def environment_audit_log(
    environment: EnvironmentDependency,
    session: SessionDependency,
    where: AuditFilterDependency,
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> AuditLogOut:
    page = list_audit(session, environment.id, where, limit, offset)
    return AuditLogOut(
        entries=[AuditEntryOut.model_validate(asdict(row)) for row in page.rows],
        total=page.total,
        models=audit_models(session, environment.id),
        purposes=list(AUDIT_PURPOSES),
        data_classes=list(DATA_CLASSES),
    )


@router.get(
    "/environments/{environment_id}/audit/export",
    responses={200: {"content": {"text/csv": {}, "application/json": {}}}},
)
def export_environment_audit_log(
    environment: EnvironmentDependency,
    session: SessionDependency,
    where: AuditFilterDependency,
    format: Literal["csv", "json"] = "csv",
) -> Response:
    """Every row matching the filter, as CSV or JSON; like the log itself, it holds no content and no secret values."""
    rows = list_audit(session, environment.id, where).rows
    if format == "json":
        body, media_type = to_json(rows), "application/json"
    else:
        body, media_type = to_csv(rows), "text/csv; charset=utf-8"
    filename = f"yaap-audit-{environment.slug}-{datetime.now(UTC):%Y-%m-%d}.{format}"
    return Response(
        body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "X-Content-Type-Options": "nosniff"},
    )
