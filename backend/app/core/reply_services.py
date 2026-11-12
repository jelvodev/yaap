"""What the reply service works with: YAAP's shared services and switches, passed in as one typed object.

`from_state` reads them from the application's state when an entry point (a request, the scheduler, A2A, goal mode)
starts a reply, so a test that changes a switch on the state before that sees its change.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx
from sqlalchemy.orm import Session, sessionmaker
from starlette.datastructures import State

from app.agent.processes import ProcessManager
from app.agent.runs import RunRegistry
from app.agent.shells import ShellSessions
from app.core.replies import ReplyRegistry
from app.core.search_index import SearchIndexer
from app.core.utility import UtilityContext
from app.credentials.crypto import CredentialCipher
from app.credentials.types import CredentialType
from app.mcp_client.manager import McpManager
from app.providers.registry import ProviderRegistry
from app.storage.models import Conversation
from app.tracing.otlp_export import TelemetrySender
from app.tracing.snapshots import SnapshotStore, trace_dir
from app.websearch.service import SearchProvider


class ChatEvals(Protocol):
    """Starts an eval run for /eval in a conversation (app/evals); returns the run's id and what the chat shows.
    Raises CommandRefused (core/reply_errors.py) when the run cannot start."""

    def start_from_chat(
        self, conversation: Conversation, case_name: str | None, queue_item_id: int | None = None
    ) -> tuple[int, str]: ...


@dataclass(frozen=True)
class ReplyServices:
    session_factory: sessionmaker[Session]
    cipher: CredentialCipher
    credential_types: dict[str, CredentialType]
    providers: ProviderRegistry
    runs: RunRegistry
    replies: ReplyRegistry
    processes: ProcessManager
    shells: ShellSessions
    mcp: McpManager
    data_dir: Path
    files_dir: Path
    workspace_dir: Path
    blueprint_dir: Path
    # Where Claude Code, Codex and sub-runs reach YAAP's MCP endpoint.
    internal_url: str
    web_search: SearchProvider | None = None
    # Started with the application; absent when a test runs without its lifespan.
    indexer: SearchIndexer | None = None
    # The Playwright MCP command; None when the browser is not available.
    browser_command: list[str] | None = None
    # Replaces the network for calls to remote A2A agents in tests.
    a2a_transport: httpx.AsyncBaseTransport | None = None
    # Replaces the network for sending traces to an OpenTelemetry collector in tests.
    telemetry_transport: httpx.AsyncBaseTransport | None = None
    # Work after a reply that tests switch off or run inline.
    utility_titles: bool = True
    memory_suggestions: bool = True
    memory_tidy: bool = True
    background_summaries: bool = True
    # A review by another vendor after a goal or scheduled task that changed code.
    reviews_after_goals: bool = True
    # The eval runs behind /eval; None when the application runs without them.
    evals: ChatEvals | None = None
    # YAAP's data folder on the user's computer (app/sandbox/host_path.py); None when it is not known.
    host_data_dir: str | None = None

    @property
    def utility(self) -> UtilityContext:
        return UtilityContext(
            self.session_factory, self.cipher, self.credential_types, self.providers, self.blueprint_dir
        )

    @property
    def telemetry(self) -> TelemetrySender:
        """Sends a reply's trace to the OpenTelemetry endpoint of the settings, when one is set."""
        snapshots = SnapshotStore(trace_dir(self.data_dir))
        return TelemetrySender(
            self.session_factory, self.cipher, self.providers, snapshots, self.telemetry_transport
        )

    @classmethod
    def from_state(cls, state: State) -> "ReplyServices":
        return cls(
            session_factory=state.session_factory,
            cipher=state.cipher,
            credential_types=state.credential_types,
            providers=state.providers,
            runs=state.runs,
            replies=state.replies,
            processes=state.processes,
            shells=state.shells,
            mcp=state.mcp,
            data_dir=state.data_dir,
            files_dir=state.files_dir,
            workspace_dir=state.workspace_dir,
            blueprint_dir=state.blueprint_dir,
            internal_url=state.internal_url,
            web_search=state.web_search,
            indexer=getattr(state, "indexer", None),
            browser_command=getattr(state, "browser_command", None),
            a2a_transport=getattr(state, "a2a_transport", None),
            telemetry_transport=getattr(state, "telemetry_transport", None),
            utility_titles=state.utility_titles,
            memory_suggestions=getattr(state, "memory_suggestions", True),
            memory_tidy=getattr(state, "memory_tidy", True),
            background_summaries=getattr(state, "background_summaries", True),
            reviews_after_goals=getattr(state, "reviews_after_goals", True),
            evals=getattr(state, "evals", None),
            host_data_dir=getattr(state, "host_data_dir", None),
        )
