"""YAAP's tools for outside MCP clients: `/mcp/outside`, behind an `mcp` API token.

The transport is the one of `/mcp` (app.agent.mcp_server). Each token gets one agent run at a time, bound to the
token's environment and project like a conversation's run: the same tools (without delegation, goals and the shared
document, which need a reply), the permission settings and the injection guard. A tool that asks waits inside the
MCP call until the user decides in YAAP: the approval shows in the header's count and in the token's conversation.

That conversation, one per token, logs every call (name, input, output, error) as tool-call blocks, and the run is
stored in agent_runs like any other. A run ends after IDLE_SECONDS without calls; the next call starts a new one in
the same conversation.
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

import anyio
from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker
from starlette.datastructures import State

from app.agent.guard import utility_injection_check
from app.agent.mcp_server import serve_message
from app.agent.outputs import OutputStore
from app.agent.runs import AgentRun, InjectionCheck, RunConfig, RunRegistry, RunServices, SecretUseRecorder
from app.agent.tools import tools_for
from app.agents.records import finish_run_record, start_run_record
from app.auth.tokens import TokenGrant, require_api_token
from app.blueprint.levels import layers_for
from app.checkpoints.service import CheckpointStore
from app.core.api_conversations import Scope, ScopeError, in_scope, new_conversation, token_scope
from app.core.chat_history import add_user_message
from app.core.github_token import read_github_token
from app.core.model_policy_store import resolve_model_policy
from app.core.search import RunSearch
from app.core.settings import SETTING_DEFINITIONS, resolve_setting, resolve_text, resolve_tool_policy
from app.core.utility import UtilityContext
from app.projects.service import project_path
from app.secrets.service import record_secret_uses, resolve_secrets
from app.storage.models import Conversation, McpClientConversation, Message
from app.targets.base import TargetError
from app.targets.service import RunTarget, conversation_run_target

logger = logging.getLogger(__name__)

router = APIRouter(include_in_schema=False)

IDLE_SECONDS = 30 * 60
MAX_LOGGED_OUTPUT = 20_000
# Tools that belong to a reply: sub-runs, a goal and the shared document beside the chat.
LEFT_OUT = frozenset({"delegate", "finish_goal", "open_document"})
INSTRUCTIONS = (
    "YAAP's tools, confined to one environment and, when the token names one, one project folder. Project paths are "
    "relative to the project. Some tools wait until the user approves them in YAAP; when the user declines, do not "
    "retry the same action. Every call is logged in a conversation in YAAP."
)


class OutsideError(Exception):
    """The token's scope cannot serve tools; the message is safe to show to the client."""


@dataclass
class OutsideSession:
    run: AgentRun
    token_id: int
    # The assistant message in the token's conversation that logs this run's calls.
    message_id: int
    last_used: float = field(default_factory=time.monotonic)
    calls: int = 0
    log_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class OutsideClients:
    """The runs of outside MCP clients, one per API token."""

    def __init__(self) -> None:
        self.sessions: dict[int, OutsideSession] = {}
        self._opening: asyncio.Lock | None = None

    def runs_of(self, conversation_id: int) -> list[AgentRun]:
        return [item.run for item in self.sessions.values() if item.run.conversation_id == conversation_id]

    async def session_for(self, app: Any, grant: TokenGrant) -> OutsideSession:
        if self._opening is None:
            self._opening = asyncio.Lock()
        async with self._opening:
            self.close_idle(app)
            current = self.sessions.get(grant.token_id)
            if current is not None and self.usable(app, current, grant):
                current.last_used = time.monotonic()
                return current
            if current is not None:
                self.close(app, current)
            opened = await anyio.to_thread.run_sync(open_session, app, grant)
            self.sessions[grant.token_id] = opened
            return opened

    def usable(self, app: Any, current: OutsideSession, grant: TokenGrant) -> bool:
        run = current.run
        if app.state.runs.find(run.token) is None:
            return False
        if run.environment_id != grant.environment_id or run.project_id != grant.project_id:
            return False
        with app.state.session_factory() as session:
            return session.get(Message, current.message_id) is not None

    def close(self, app: Any, current: OutsideSession) -> None:
        registry: RunRegistry = app.state.runs
        registry.finish(current.run)
        self.sessions.pop(current.token_id, None)
        finish_run_record(app.state.session_factory, current.run, current.message_id, "complete", current.calls)

    def close_idle(self, app: Any, idle: float = IDLE_SECONDS) -> None:
        now = time.monotonic()
        for current in list(self.sessions.values()):
            if now - current.last_used > idle and not current.run.state.approvals:
                self.close(app, current)

    def close_all(self, app: Any) -> None:
        for current in list(self.sessions.values()):
            self.close(app, current)


def outside_clients(app: Any) -> OutsideClients:
    clients = getattr(app.state, "outside_clients", None)
    if clients is None:
        clients = app.state.outside_clients = OutsideClients()
    return clients


def client_conversation(session: Session, app: Any, grant: TokenGrant) -> Conversation:
    """The token's conversation, made on first use (and again when the user deleted it)."""
    link = session.get(McpClientConversation, grant.token_id)
    conversation = session.get(Conversation, link.conversation_id) if link is not None else None
    if in_scope(conversation, grant.environment_id, grant.project_id):
        return conversation  # type: ignore[return-value]
    scope = token_scope(session, grant.environment_id, grant.project_id)
    conversation = new_conversation(session, app.state.providers, scope, f"MCP client · token {grant.token_id}")
    if link is None:
        session.add(McpClientConversation(token_id=grant.token_id, conversation_id=conversation.id))
    else:
        link.conversation_id = conversation.id
    session.commit()
    return conversation


def open_session(app: Any, grant: TokenGrant) -> OutsideSession:
    """Start the token's run, as the reply service starts a conversation's run. Runs in a worker thread."""
    state = app.state
    with state.session_factory() as session:
        try:
            conversation = client_conversation(session, app, grant)
            scope = token_scope(session, grant.environment_id, grant.project_id)
        except ScopeError as error:
            raise OutsideError(str(error)) from error
        try:
            on_target = conversation_run_target(session, state.cipher, conversation)
        except TargetError as error:
            raise OutsideError(str(error)) from error
        run = state.runs.start(
            outside_config(state, session, conversation, scope, on_target),
            outside_services(state, session, conversation, scope, on_target is not None),
        )
        run.state.record_id = start_run_record(session, run, "outside MCP client")
        user_message = add_user_message(
            session, conversation, f"Tool calls of an outside MCP client with API token {grant.token_id}."
        )
        log = Message(
            conversation_id=conversation.id,
            parent_id=user_message.id,
            role="assistant",
            content=[{"type": "text", "text": ""}],
            status="complete",
            created_at=datetime.now(UTC),
        )
        session.add(log)
        session.flush()
        conversation.head_message_id = log.id
        session.commit()
        return OutsideSession(run=run, token_id=grant.token_id, message_id=log.id)


def outside_config(
    state: State, session: Session, conversation: Conversation, scope: Scope, on_target: RunTarget | None
) -> RunConfig:
    environment, project = scope.environment, scope.project
    project_id = project.id if project is not None else None
    author_name = resolve_text(session, "git_author_name", environment.id)
    author_email = resolve_text(session, "git_author_email", environment.id)
    on_computer = on_target is not None and not on_target.has_container_tools
    tool_names = tools_for(project is not None, False, None, on_computer)
    return RunConfig(
        conversation_id=conversation.id,
        environment_id=environment.id,
        blueprint_layers=layers_for(state.blueprint_dir, state.workspace_dir, environment, project),
        tool_names=frozenset(tool_names - LEFT_OUT),
        project_id=project_id,
        project_folder=project_path(state.workspace_dir, project) if project is not None else None,
        git_author=(author_name, author_email) if author_name and author_email else None,
        github_token=read_github_token(session, state.cipher, environment.id, project_id, conversation.id),
        # The settings as they are: an outside client never gets full access.
        policy=replace(resolve_tool_policy(session, environment.id, project_id), access="default"),
        target=on_target.runtime if on_target is not None else None,
        target_root=on_target.file_root if on_target is not None else None,
        model_policy=resolve_model_policy(session, environment.id, project_id),
    )


def outside_services(
    state: State, session: Session, conversation: Conversation, scope: Scope, on_target: bool
) -> RunServices:
    """A conversation's services without delegation, the goal and the shared document; secrets as placeholders in
    a project and on a computer."""
    environment_id, project = scope.environment.id, scope.project
    project_id = project.id if project is not None else None
    with_project, with_secrets = project is not None, project is not None or on_target
    factory = state.session_factory
    return RunServices(
        web_search=state.web_search,
        searcher=RunSearch(factory, getattr(state, "indexer", None), state.data_dir) if with_project else None,
        checkpoints=CheckpointStore(factory, state.data_dir) if with_project else None,
        secrets=resolve_secrets(session, state.cipher, environment_id, project_id) if with_secrets else {},
        on_secret_use=secret_recorder(factory, environment_id, project_id, conversation.id) if with_secrets else None,
        processes=state.processes,
        shells=state.shells,
        outputs=OutputStore(state.data_dir, conversation.id),
        injection_check=injection_check(state, session, environment_id, project_id, conversation.id),
    )


def injection_check(
    state: State, session: Session, environment_id: int, project_id: int | None, conversation_id: int
) -> InjectionCheck | None:
    if resolve_setting(session, SETTING_DEFINITIONS["injection_check"], environment_id, project_id).value != "on":
        return None
    context = UtilityContext(state.session_factory, state.cipher, state.credential_types, state.providers)
    return utility_injection_check(context, environment_id, conversation_id)


def secret_recorder(
    session_factory: sessionmaker[Session], environment_id: int, project_id: int | None, conversation_id: int
) -> SecretUseRecorder:
    def record(names: list[str], used_by: str) -> None:
        with session_factory() as session:
            record_secret_uses(session, environment_id, project_id, names, used_by, conversation_id)

    return record


def log_call(app: Any, current: OutsideSession, name: str, arguments: dict[str, Any], output: str, is_error: bool) -> None:
    """Add the call to the run's message in the token's conversation. Runs in a worker thread."""
    with app.state.session_factory() as session:
        message = session.get(Message, current.message_id)
        if message is None:
            return
        if len(output) > MAX_LOGGED_OUTPUT:
            output = output[:MAX_LOGGED_OUTPUT] + "\n[Output shortened for storage.]"
        block = {
            "type": "tool_call",
            "id": uuid.uuid4().hex[:16],
            "name": name,
            "input": arguments,
            "output": output,
            "is_error": is_error,
        }
        calls = [item for item in message.content if item.get("type") == "tool_call"]
        message.content = [*calls, block, {"type": "text", "text": ""}]
        conversation = session.get(Conversation, message.conversation_id)
        if conversation is not None:
            conversation.updated_at = datetime.now(UTC)
            conversation.unread = True
        session.commit()


def drain(run: AgentRun) -> None:
    """Nobody follows an outside run's events (approval cards are read from the registry), so they are dropped."""
    while not run.events.empty():
        run.events.get_nowait()


@router.post("/mcp/outside")
async def outside_mcp_endpoint(request: Request, grant: TokenGrant = Depends(require_api_token("mcp"))) -> Response:
    app = request.app
    clients = outside_clients(app)
    try:
        current = await clients.session_for(app, grant)
    except OutsideError as error:
        return JSONResponse({"detail": str(error)}, status.HTTP_409_CONFLICT)

    async def on_call(name: str, arguments: dict[str, Any], output: str, is_error: bool) -> None:
        current.calls += 1
        current.last_used = time.monotonic()
        drain(current.run)
        async with current.log_lock:
            try:
                await anyio.to_thread.run_sync(log_call, app, current, name, arguments, output, is_error)
            except Exception:  # noqa: BLE001 - the call happened; a failed log line must not hide its result
                logger.warning("Logging an outside MCP call failed.", exc_info=True)

    try:
        return await serve_message(request, app.state.runs, current.run, INSTRUCTIONS, on_call)
    finally:
        current.last_used = time.monotonic()
        drain(current.run)


@router.get("/mcp/outside")
@router.delete("/mcp/outside")
async def outside_not_streaming(_grant: TokenGrant = Depends(require_api_token("mcp"))) -> Response:
    """No server-initiated stream and no explicit session end, as on /mcp."""
    return Response(status_code=status.HTTP_405_METHOD_NOT_ALLOWED)
