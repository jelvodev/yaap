"""Vendor-neutral provider interface. Vendor-specific code lives only in the adapters."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

ContentBlock = dict[str, Any]
# Where a part of the system prompt comes from, for the context snapshot of a model call (tracing).
ContextSource = Literal[
    "base",
    "rules",
    "standards",
    "skills",
    "agent",
    "project",
    "repo_map",
    "target",
    "document",
    "delegation",
    "utility",
    "other",
]
# How long the cacheable system parts stay in the provider's prompt cache: the usual five minutes, or an hour where
# the connection offers it (an Anthropic API key) and a pause is expected.
CacheTtl = Literal["5m", "1h"]


@dataclass(frozen=True)
class SystemPart:
    text: str
    cacheable: bool = False
    source: ContextSource = "other"


@dataclass(frozen=True)
class HistoryMessage:
    role: Literal["user", "assistant"]
    content: list[ContentBlock]
    provider_raw: list[ContentBlock] | None = None


@dataclass(frozen=True)
class ToolServer:
    """YAAP's tools for one agent run, served over MCP."""

    url: str
    token: str


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    # Only reads: several such calls in one turn may run at the same time.
    read_only: bool = False


@dataclass(frozen=True)
class ToolResult:
    text: str
    is_error: bool = False
    # Images as (media type, base64 data), for models that can see them.
    images: tuple[tuple[str, str], ...] = ()


class ToolBox(Protocol):
    """YAAP's tools for one agent run, called in-process by adapters that run the tool loop themselves."""

    @property
    def definitions(self) -> list[ToolDefinition]: ...

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResult: ...


@dataclass(frozen=True)
class AgentRequest:
    model: str
    effort: str
    system: list[SystemPart]
    messages: list[HistoryMessage]
    max_tokens: int = 64_000
    # Agent runs only: the project folder to work in and the tools to use there.
    working_folder: Path | None = None
    # The same tools twice: over MCP for Claude Code, in-process for adapters with their own tool loop.
    tool_server: ToolServer | None = None
    tool_box: ToolBox | None = None
    # At most this many model calls in the tool loop (a delegated sub-run); None is the adapter's own limit.
    max_model_calls: int | None = None
    # How long the cacheable system parts are cached; an adapter that cannot choose ignores it.
    cache_ttl: CacheTtl = "5m"


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    # Of the cache writes, those kept for an hour, which cost more than five-minute writes.
    cache_write_1h_tokens: int = 0


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ReasoningDelta:
    text: str


@dataclass(frozen=True)
class ToolCallStarted:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class ToolCallFinished:
    id: str
    output: str
    is_error: bool


@dataclass(frozen=True)
class Completed:
    stop_reason: str | None
    served_by_model: str
    usage: TokenUsage
    provider_raw: list[ContentBlock] = field(default_factory=list)
    refusal_category: str | None = None
    # "metered": paid per token (API key). "subscription": covered by a subscription, so no per-reply cost.
    billing: str = "metered"
    # Tokens in the context of the last model call. A run with tool calls makes several calls, so its usage total
    # is larger than the context; None means the usage total is the context.
    context_tokens: int | None = None


@dataclass(frozen=True)
class ModelCallFinished:
    """One model call of a reply ended, with what it used. An adapter that runs the tool loop itself reports each
    call; a coding agent CLI reports its whole turn once, with the totals the CLI gives and, when it says,
    the number of model calls it made. The usage of all of a reply's reports adds up to its Completed usage."""

    model: str
    usage: TokenUsage
    model_calls: int | None = 1


@dataclass(frozen=True)
class CallRestarted:
    """A model call broke off and starts again: its text and reasoning so far (since the last tool result) are void."""

    attempt: int
    reason: str


AgentEvent = (
    TextDelta | ReasoningDelta | ToolCallStarted | ToolCallFinished | CallRestarted | ModelCallFinished | Completed
)


class ProviderError(Exception):
    """A provider failure with a message that is safe to show to the user.

    transient: the failure may pass by itself (a dropped connection, an overloaded service), so trying the same call
    again makes sense.
    """

    def __init__(self, message: str, transient: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.transient = transient


class ModelProvider(Protocol):
    def stream(self, request: AgentRequest) -> AsyncIterator[AgentEvent]: ...

    async def close(self) -> None: ...
