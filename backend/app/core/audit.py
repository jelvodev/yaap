"""The audit log: one row per model request that leaves YAAP.

A row says when, in which environment, project and conversation, for what (the purpose), through which connection
to which provider and model a request went, and which kinds of data it carried. It never holds content: no prompt,
no reply, no title, and secrets only by their placeholder names. A check before the request leaves can refuse it
(the outgoing check, or the model policy where the request is known when the model is chosen); its row then says
so and why (`with_refusal`).

Every path on which a model request leaves YAAP records it here, right before the request is sent: a reply
(`ReplyService.launch`), a delegated sub-run or CLI worker (`Delegator.run_sub_run`), the utility model
(`run_utility`) and a call to a remote A2A agent (the `call_agent` tool). Embeddings are made by a local model and
never leave YAAP, so they have no row today; the purpose exists for an embedding model behind a connection. A
reply's trace sent to an OpenTelemetry collector is recorded as `telemetry` (`app/tracing/otlp_export.py`).

Rows are kept for a year: the minute loop removes older ones (`prune_audit`).
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Literal, get_args

from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

from app.core.model_choice import ModelChoice
from app.providers.base import AgentRequest
from app.providers.registry import ProviderSpec
from app.secrets.service import placeholder_names
from app.storage.models import AuditRecord, Conversation

AuditPurpose = Literal["reply", "sub_run", "cli_worker", "utility", "a2a", "embedding", "telemetry"]
DataClass = Literal["conversation", "files", "knowledge", "memory", "attachments", "secrets"]
AuditOutcome = Literal["sent", "refused"]

AUDIT_PURPOSES: tuple[AuditPurpose, ...] = get_args(AuditPurpose)
# Also the order in which a row lists its data classes.
DATA_CLASSES: tuple[DataClass, ...] = get_args(DataClass)
KEPT = timedelta(days=365)

# What each utility task puts in its prompt. A task not listed is recorded with what its prompt shows (placeholders).
UTILITY_TASK_DATA: dict[str, tuple[DataClass, ...]] = {
    "title": ("conversation",),
    "rule": ("conversation",),
    "summary": ("conversation", "files"),
    "memory_suggestion": ("conversation", "memory"),
    "title_and_memory": ("conversation", "memory"),
    "memory_tidy": ("memory",),
    "injection_check": ("files",),
    "commit_message": ("files",),
    "pull_request": ("files",),
    "import": ("files",),
    "eval_rubric": ("files",),
    "review": ("conversation", "files"),
}


@dataclass(frozen=True)
class ProjectData:
    """What of a project goes along with an agent request besides the conversation: its memory (whole or the
    entries recalled for the message), whether it has knowledge documents the model is told about and can read,
    and the names of the secrets offered as placeholders."""

    memory: bool = False
    knowledge: bool = False
    secret_names: tuple[str, ...] = ()


NO_PROJECT_DATA = ProjectData()


@dataclass(frozen=True)
class AuditEntry:
    """One row of the audit log, before it is written."""

    environment_id: int
    purpose: AuditPurpose
    provider: str
    connection: str
    model: str
    data_classes: tuple[DataClass, ...] = ()
    secret_names: tuple[str, ...] = ()
    project_id: int | None = None
    conversation_id: int | None = None
    task: str | None = None
    outcome: AuditOutcome = "sent"
    reason: str | None = None


def carried_data(
    request: AgentRequest,
    conversation: bool,
    project: ProjectData = NO_PROJECT_DATA,
    attachments: bool = False,
    known: Iterable[DataClass] = (),
) -> tuple[tuple[DataClass, ...], tuple[str, ...]]:
    """The data classes a request carries and the secret placeholder names among them.

    files covers files and tool output: a request with YAAP's tools sends their results back to the model, and a
    history with tool calls carries earlier results. Secrets are the names offered to the model plus placeholders
    written in the messages; the system prompt is not scanned, as it names the placeholder syntax itself."""
    classes: set[DataClass] = set(known)
    if conversation:
        classes.add("conversation")
    if attachments:
        classes.add("attachments")
    if project.memory:
        classes.add("memory")
    if project.knowledge:
        classes.add("knowledge")
    if request.tool_box is not None or request.tool_server is not None or has_tool_output(request):
        classes.add("files")
    names = sorted(set(project.secret_names) | set(placeholder_names(messages_text(request))))
    if names:
        classes.add("secrets")
    return tuple(name for name in DATA_CLASSES if name in classes), tuple(names)


def has_tool_output(request: AgentRequest) -> bool:
    return any(block.get("type") == "tool_call" for message in request.messages for block in message.content)


def messages_text(request: AgentRequest) -> str:
    """The messages as text, tool calls included, for finding placeholders; images and documents add no text."""
    return json.dumps([message.content for message in request.messages], ensure_ascii=False, default=str)


def reply_entry(
    conversation: Conversation,
    spec: ProviderSpec,
    request: AgentRequest,
    project: ProjectData,
    attachments: bool,
) -> AuditEntry:
    classes, names = carried_data(request, True, project, attachments)
    return AuditEntry(
        environment_id=conversation.environment_id,
        purpose="reply",
        provider=spec.name,
        connection=spec.connection,
        model=request.model,
        data_classes=classes,
        secret_names=names,
        project_id=conversation.project_id,
        conversation_id=conversation.id,
    )


def sub_run_entry(
    choice: ModelChoice,
    request: AgentRequest,
    project: ProjectData,
    project_id: int | None,
    conversation_id: int | None,
) -> AuditEntry:
    """A delegated sub-run sees its task, not the conversation; a CLI worker is a sub-run on a coding agent. It
    always works with YAAP's tools, also when the policy refused it before its tools existed."""
    classes, names = carried_data(request, False, project, known=("files",))
    return AuditEntry(
        environment_id=choice.environment_id,
        purpose="cli_worker" if choice.cli is not None else "sub_run",
        provider=choice.spec.name,
        connection=choice.connection,
        model=choice.model,
        data_classes=classes,
        secret_names=names,
        project_id=project_id,
        conversation_id=conversation_id,
    )


def utility_entry(
    choice: ModelChoice,
    request: AgentRequest,
    task: str | None,
    project_id: int | None,
    conversation_id: int | None,
) -> AuditEntry:
    classes, names = carried_data(request, False, known=UTILITY_TASK_DATA.get(task or "", ()))
    return AuditEntry(
        environment_id=choice.environment_id,
        purpose="utility",
        provider=choice.spec.name,
        connection=choice.connection,
        model=choice.model,
        data_classes=classes,
        secret_names=names,
        project_id=project_id,
        conversation_id=conversation_id,
        task=task,
    )


def a2a_entry(
    environment_id: int,
    project_id: int | None,
    conversation_id: int | None,
    agent: str,
    message: str,
    token: str | None,
) -> AuditEntry:
    """A message to a remote A2A agent: the model wrote it from the conversation. The agent's token, a placeholder,
    goes along as a secret."""
    names = sorted(set(placeholder_names(message)) | set(placeholder_names(token or "")))
    classes: tuple[DataClass, ...] = ("conversation", "secrets") if names else ("conversation",)
    return AuditEntry(
        environment_id=environment_id,
        purpose="a2a",
        provider="a2a",
        connection="a2a",
        model=agent,
        data_classes=classes,
        secret_names=tuple(names),
        project_id=project_id,
        conversation_id=conversation_id,
    )


def telemetry_entry(
    environment_id: int,
    project_id: int | None,
    conversation_id: int,
    host: str,
    content: bool,
    secret_names: tuple[str, ...],
) -> AuditEntry:
    """A reply's trace sent to an OpenTelemetry collector (host as the model). It carries the conversation and tool
    calls only when prompt content is included; the headers' secrets go along by placeholder name."""
    classes: tuple[DataClass, ...] = ("conversation", "files") if content else ()
    if secret_names:
        classes = (*classes, "secrets")
    return AuditEntry(
        environment_id=environment_id,
        purpose="telemetry",
        provider="otlp",
        connection="otlp",
        model=host,
        data_classes=classes,
        secret_names=secret_names,
        project_id=project_id,
        conversation_id=conversation_id,
    )


def with_refusal(entry: AuditEntry, reason: str | None) -> AuditEntry:
    """The entry as refused for reason, or unchanged when nothing refused it. The reasons of the outgoing check and
    the model policy name a secret, a pattern or a model, never a secret's value."""
    return entry if reason is None else replace(entry, outcome="refused", reason=reason)


def record_request(session_factory: sessionmaker[Session], entry: AuditEntry) -> None:
    """Write the row for a request about to leave YAAP (or refused before it left)."""
    with session_factory() as session:
        session.add(
            AuditRecord(
                created_at=datetime.now(UTC),
                environment_id=entry.environment_id,
                project_id=entry.project_id,
                conversation_id=entry.conversation_id,
                purpose=entry.purpose,
                task=entry.task,
                provider=entry.provider,
                connection=entry.connection,
                model=entry.model,
                data_classes=list(entry.data_classes),
                secret_names=list(entry.secret_names),
                outcome=entry.outcome,
                reason=entry.reason[:500] if entry.reason else None,
            )
        )
        session.commit()


def prune_audit(session: Session, now: datetime | None = None) -> int:
    """Called from the minute loop: removes rows older than a year. Returns how many went."""
    cutoff = (now or datetime.now(UTC)) - KEPT
    result = session.execute(delete(AuditRecord).where(AuditRecord.created_at < cutoff))
    session.commit()
    return int(result.rowcount or 0)  # type: ignore[attr-defined]  # rowcount exists on a DML result
