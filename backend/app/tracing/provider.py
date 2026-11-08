"""Model calls as steps: the wrapper at the common entry of the provider adapters (tracing).

A reply's provider is wrapped where its requests leave YAAP (next to the outgoing check and the audit log), so a model
request is counted once. Adapters that run the tool loop themselves report each model call (`ModelCallFinished`);
Claude Code and Codex report their turn as a whole with the totals their CLI gives. A provider that reports nothing
but `Completed` is recorded as one step with the reply's usage. Either way the usage of a run's model steps adds up
to its `Completed` usage, which is the reply's `UsageRecord`.

Per model call the wrapper stores what the model saw: the request's system parts and tools, and its messages followed
by what the reply did before the call (its text and its tool calls with their results), in YAAP's neutral form.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.providers.base import (
    AgentEvent,
    AgentRequest,
    CallRestarted,
    Completed,
    ContentBlock,
    HistoryMessage,
    ModelCallFinished,
    ModelProvider,
    ReasoningDelta,
    TextDelta,
    TokenUsage,
    ToolCallFinished,
    ToolCallStarted,
)
from app.providers.catalog import calculate_cost
from app.providers.registry import ProviderRegistry
from app.tracing.recorder import ModelStep, TraceRecorder
from app.tracing.snapshots import ContextSnapshot

# The cost of a model call from its model and usage; None when it is not paid per token or the model is unknown.
Pricing = Callable[[str, TokenUsage], float | None]


def catalog_pricing(providers: ProviderRegistry, metered: bool) -> Pricing:
    """Prices from the model catalog, as the usage records use them; a subscription costs nothing per call."""

    def price(model: str, usage: TokenUsage) -> float | None:
        info = providers.find_model(model)
        return calculate_cost(info, usage) if metered and info is not None else None

    return price


@dataclass(frozen=True)
class ModelTrace:
    """Where a reply's model calls go: the recorder, the run record, how a call is priced, and the secret values a
    snapshot must never hold."""

    recorder: TraceRecorder
    run_id: int
    pricing: Pricing
    secrets: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FinishedCall:
    position: int | None
    started_at: datetime
    duration_ms: int
    messages: list[HistoryMessage]


class CallTracker:
    """Follows one reply's events: when the current model call started and what the model had seen by then."""

    def __init__(self, request: AgentRequest, first_position: int) -> None:
        self.request = request
        self.blocks: list[ContentBlock] = []
        self.seen = 0
        self.open = False
        self.first_position: int | None = first_position
        self.restart_clock()

    def restart_clock(self) -> None:
        self.started_at = datetime.now(UTC)
        self.started = time.monotonic()

    def observe(self, event: AgentEvent) -> None:
        if isinstance(event, (TextDelta, ReasoningDelta)):
            if not self.open:
                self.open, self.seen = True, len(self.blocks)
            if isinstance(event, TextDelta):
                self.add_text(event.text)
        elif isinstance(event, ToolCallStarted):
            self.blocks.append(
                {"type": "tool_call", "id": event.id, "name": event.name, "input": event.input, "output": None}
            )
        elif isinstance(event, ToolCallFinished):
            for block in reversed(self.blocks):
                if block["type"] == "tool_call" and block["id"] == event.id:
                    block.update(output=event.output, is_error=event.is_error)
                    break
            # The next model call starts when the last tool is done.
            self.restart_clock()
        elif isinstance(event, CallRestarted):
            while len(self.blocks) > self.seen and self.blocks[-1]["type"] == "text":
                self.blocks.pop()

    def add_text(self, text: str) -> None:
        if self.blocks and self.blocks[-1]["type"] == "text":
            self.blocks[-1]["text"] += text
        else:
            self.blocks.append({"type": "text", "text": text})

    def finish(self) -> FinishedCall:
        """The model call that just ended: its position (taken at the start for the first call), timing and input."""
        seen = self.seen if self.open else len(self.blocks)
        done = [dict(block) for block in self.blocks[:seen]]
        before = [HistoryMessage(role="assistant", content=done)] if done else []
        call = FinishedCall(
            self.first_position,
            self.started_at,
            int((time.monotonic() - self.started) * 1000),
            [*self.request.messages, *before],
        )
        self.first_position, self.open = None, False
        self.restart_clock()
        return call


class TracedProvider:
    def __init__(self, provider: ModelProvider, trace: ModelTrace) -> None:
        self.provider = provider
        self.trace = trace

    async def stream(self, request: AgentRequest) -> AsyncIterator[AgentEvent]:
        # The first model call is the run's first step of this reply: its position is taken before any tool call.
        tracker = CallTracker(request, self.trace.recorder.reserve(self.trace.run_id))
        reported = False
        async for event in self.provider.stream(request):
            if isinstance(event, ModelCallFinished):
                reported = True
                await self.record(request, tracker.finish(), event.model, event.usage, event.model_calls)
            elif isinstance(event, Completed) and not reported:
                await self.record(request, tracker.finish(), event.served_by_model, event.usage, None)
            else:
                tracker.observe(event)
            yield event
            if isinstance(event, ModelCallFinished):
                # A step listener may have stopped the reply just now (a budget was reached): give that stop the
                # chance to land before the adapter goes on to its next tool or model call.
                await asyncio.sleep(0)

    async def record(
        self, request: AgentRequest, call: FinishedCall, model: str, usage: TokenUsage, model_calls: int | None
    ) -> None:
        trace = self.trace
        position = call.position if call.position is not None else trace.recorder.reserve(trace.run_id)
        tools = request.tool_box.definitions if request.tool_box is not None else []
        snapshot = await trace.recorder.snapshot(ContextSnapshot(request.system, tools, call.messages), trace.secrets)
        step = ModelStep(
            position=position,
            started_at=call.started_at,
            duration_ms=call.duration_ms,
            model=model,
            usage=usage,
            cost=trace.pricing(model, usage),
            model_calls=model_calls,
            snapshot=snapshot,
            cache_ttl=request.cache_ttl if any(part.cacheable for part in request.system) else "5m",
        )
        await trace.recorder.record(trace.run_id, step)

    async def close(self) -> None:
        await self.provider.close()


def traced_provider(provider: ModelProvider, trace: ModelTrace | None) -> ModelProvider:
    """The provider with its model calls recorded as steps of the run; unchanged without a trace."""
    return provider if trace is None else TracedProvider(provider, trace)
