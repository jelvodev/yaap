"""OpenTelemetry downloads: a reply's trace, or the replies of a period, as OTLP
JSON. A run is found only while its conversation exists, as the conversation APIs show it; the period export holds
only such runs. Prompt content follows the `otlp_include_content` setting of each reply's environment."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import DataDirDependency, ProviderRegistryDependency, SessionDependency
from app.providers.registry import ProviderRegistry
from app.storage.models import Environment
from app.tracing.otlp import ReplyTrace, TooManyTraces, period_traces, reply_trace
from app.tracing.otlp_export import export_document
from app.tracing.snapshots import SnapshotStore, trace_dir

router = APIRouter(tags=["telemetry"])

OTLP_RESPONSE = {200: {"content": {"application/json": {}}, "description": "An OTLP/JSON trace export."}}


def utc(moment: datetime) -> datetime:
    """A time without zone is UTC."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def otlp_download(
    session: Session, traces: list[ReplyTrace], providers: ProviderRegistry, data_dir: Path, filename: str
) -> Response:
    document = export_document(session, traces, providers, SnapshotStore(trace_dir(data_dir)))
    return Response(
        json.dumps(document, ensure_ascii=False, separators=(",", ":")),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "X-Content-Type-Options": "nosniff"},
    )


@router.get("/telemetry/runs/{run_id}/otlp", responses=OTLP_RESPONSE)
def export_run_otlp(
    run_id: int, session: SessionDependency, providers: ProviderRegistryDependency, data_dir: DataDirDependency
) -> Response:
    """The trace of the reply a run belongs to (a sub-run gives its whole reply) as OTLP JSON."""
    trace = reply_trace(session, run_id)
    if trace is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found.")
    return otlp_download(session, [trace], providers, data_dir, f"yaap-trace-run-{trace.root.record.id}.json")


@router.get("/telemetry/otlp", responses=OTLP_RESPONSE)
def export_period_otlp(
    session: SessionDependency,
    providers: ProviderRegistryDependency,
    data_dir: DataDirDependency,
    since: Annotated[datetime, Query(description="The start of the period (inclusive); without zone it is UTC.")],
    until: Annotated[datetime, Query(description="The end of the period (exclusive); without zone it is UTC.")],
    environment_id: Annotated[int | None, Query(description="Only this environment's replies.")] = None,
) -> Response:
    """The replies that started in the period, one trace each, as OTLP JSON; at most 1,000 replies."""
    start, end = utc(since), utc(until)
    if end <= start:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The end of the period must come after its start.")
    if environment_id is not None and session.get(Environment, environment_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Environment not found.")
    try:
        traces = period_traces(session, start, end, environment_id)
    except TooManyTraces as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    filename = f"yaap-traces-{start:%Y-%m-%d}-{end:%Y-%m-%d}.json"
    return otlp_download(session, traces, providers, data_dir, filename)
