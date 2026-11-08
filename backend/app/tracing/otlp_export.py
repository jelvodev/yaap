"""Sending a reply's trace to an OpenTelemetry collector.

When the `otlp_endpoint` setting is set at the reply's environment (or at Global), each reply's trace is posted as
OTLP/HTTP JSON to `<endpoint>/v1/traces` once the reply has ended, in the background: the reply never waits for it,
and a collector that fails or cannot be reached is logged and nothing else happens. Downloads build the same document
(`export_document`).

What leaves YAAP goes through the same checks as other outgoing data:
- the headers name their secrets as `{{secret:NAME}}` placeholders, filled in from the vault right before sending;
  each use lands in the vault's use log;
- with prompt content included, the payload is checked with the outgoing check (`text_refusal`: the values of the
  environment's and project's secrets and the model policy's patterns), and a hit stops the export. Without content
  the payload holds only what YAAP made itself (ids, times, token counts, model and tool names), so it is not checked;
- every export, sent or refused, gets a row in the audit log (purpose `telemetry`, the collector's host as model).
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit

import httpx
from sqlalchemy.orm import Session, sessionmaker

from app.core.audit import record_request, telemetry_entry, with_refusal
from app.core.outgoing_checks import outgoing_rules, text_refusal
from app.core.settings import SETTING_DEFINITIONS, resolve_setting
from app.credentials.crypto import CredentialCipher
from app.providers.registry import ProviderRegistry
from app.secrets.service import SecretError, fill_in, placeholder_names, record_secret_uses
from app.tracing.otlp import ExportOptions, OtlpJson, ReplyTrace, otlp_document, reply_spans, reply_trace
from app.tracing.otlp_settings import CONTENT_KEY, ENDPOINT_KEY, HEADERS_KEY, stored_headers, traces_url
from app.tracing.snapshots import SnapshotStore

logger = logging.getLogger(__name__)

SEND_TIMEOUT = 10.0
SECRET_USER = "OpenTelemetry export"
ExportOutcome = Literal["off", "sent", "failed", "refused"]

# Exports started after replies, kept referenced until they end.
EXPORT_TASKS: set["asyncio.Task[ExportOutcome]"] = set()


@dataclass(frozen=True)
class TelemetryConfig:
    endpoint: str = ""
    # Header values as stored: with {{secret:NAME}} placeholders, never values.
    headers: dict[str, str] = field(default_factory=dict)
    include_content: bool = False


def resolve_telemetry(session: Session, environment_id: int | None) -> TelemetryConfig:
    """The export settings at an environment (inherited from Global), or at Global."""

    def value(key: str) -> object:
        return resolve_setting(session, SETTING_DEFINITIONS[key], environment_id).value

    return TelemetryConfig(
        endpoint=str(value(ENDPOINT_KEY) or "").strip(),
        headers=stored_headers(value(HEADERS_KEY)),
        include_content=value(CONTENT_KEY) == "on",
    )


def export_options(providers: ProviderRegistry, snapshots: SnapshotStore, include_content: bool) -> ExportOptions:
    def provider_of(model: str) -> str | None:
        info = providers.find_model(model)
        return info.provider if info is not None else None

    return ExportOptions(provider_of, snapshots.load if include_content else None)


def export_document(
    session: Session,
    traces: list[ReplyTrace],
    providers: ProviderRegistry,
    snapshots: SnapshotStore,
) -> OtlpJson:
    """The OTLP document of traces; prompt content per the setting of each reply's environment."""
    content: dict[int, bool] = {}
    spans: list[OtlpJson] = []
    for trace in traces:
        if trace.environment_id not in content:
            content[trace.environment_id] = resolve_telemetry(session, trace.environment_id).include_content
        spans += reply_spans(trace, export_options(providers, snapshots, content[trace.environment_id]))
    return otlp_document(spans)


@dataclass(frozen=True)
class TelemetrySender:
    """What sending a reply's trace needs; one per reply, made by the reply service."""

    session_factory: sessionmaker[Session]
    cipher: CredentialCipher
    providers: ProviderRegistry
    snapshots: SnapshotStore
    # Replaces the network in tests.
    transport: httpx.AsyncBaseTransport | None = None


@dataclass(frozen=True)
class PreparedExport:
    url: str
    body: str
    headers: dict[str, str]


def endpoint_host(endpoint: str) -> str:
    return (urlsplit(endpoint).netloc or endpoint)[:80]


def prepare_export(sender: TelemetrySender, record_id: int) -> PreparedExport | ExportOutcome:
    """Build and check the export of one reply; an outcome when nothing is to be sent. Runs in a worker thread."""
    with sender.session_factory() as session:
        trace = reply_trace(session, record_id)
        if trace is None:
            return "off"
        config = resolve_telemetry(session, trace.environment_id)
        if not config.endpoint:
            return "off"
        document = export_document(session, [trace], sender.providers, sender.snapshots)
        body = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
        rules = outgoing_rules(session, sender.cipher, trace.environment_id, trace.project_id)
        names = sorted({name for header in config.headers.values() for name in placeholder_names(header)})
        refusal = text_refusal(body, rules) if config.include_content else None
        try:
            headers = {name: fill_in(header, rules.secrets) for name, header in config.headers.items()}
        except SecretError as error:
            refusal = refusal or f"A header of the OpenTelemetry export names a secret that is not there: {error}"
            headers = {}
        entry = telemetry_entry(
            trace.environment_id,
            trace.project_id,
            trace.conversation_id,
            endpoint_host(config.endpoint),
            config.include_content,
            tuple(names),
        )
        record_request(sender.session_factory, with_refusal(entry, refusal))
        if refusal is not None:
            logger.warning("The OpenTelemetry export of run %s was not sent: %s", record_id, refusal)
            return "refused"
        if names:
            record_secret_uses(
                session, trace.environment_id, trace.project_id, names, SECRET_USER, trace.conversation_id
            )
        return PreparedExport(traces_url(config.endpoint), body, headers)


async def post_export(prepared: PreparedExport, transport: httpx.AsyncBaseTransport | None) -> ExportOutcome:
    headers = {**prepared.headers, "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(transport=transport, timeout=SEND_TIMEOUT) as client:
            response = await client.post(prepared.url, content=prepared.body.encode("utf-8"), headers=headers)
    except httpx.HTTPError as error:
        # The headers may hold secret values, so only the address and the kind of error are logged.
        logger.warning("The OpenTelemetry collector at %s could not be reached: %s", prepared.url, type(error).__name__)
        return "failed"
    if response.status_code >= 400:
        logger.warning("The OpenTelemetry collector at %s answered %s.", prepared.url, response.status_code)
        return "failed"
    return "sent"


async def export_reply(sender: TelemetrySender, record_id: int) -> ExportOutcome:
    """Send one reply's trace when an endpoint is set. Never raises: the reply is already done."""
    try:
        prepared = await asyncio.to_thread(prepare_export, sender, record_id)
        if not isinstance(prepared, PreparedExport):
            return prepared
        return await post_export(prepared, sender.transport)
    except Exception:  # A background task after the reply: a failure is logged and the user's work goes on.
        logger.exception("The OpenTelemetry export of run %s failed.", record_id)
        return "failed"


def start_export(sender: TelemetrySender, record_id: int) -> None:
    task = asyncio.get_running_loop().create_task(export_reply(sender, record_id))
    EXPORT_TASKS.add(task)
    task.add_done_callback(EXPORT_TASKS.discard)


async def with_telemetry_export(
    sender: TelemetrySender, record_id: int, events: AsyncIterator[str]
) -> AsyncIterator[str]:
    """The reply's events; when they end (also when the reply is stopped), its trace is sent in the background.
    Whether an endpoint is set is read there too, so a reply never waits for the settings or the collector."""
    try:
        async for event in events:
            yield event
    finally:
        start_export(sender, record_id)
