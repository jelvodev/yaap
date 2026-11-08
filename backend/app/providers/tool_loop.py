"""Parts of the tool loop shared by the adapters that run it themselves (Anthropic and OpenAI over the API).

- Retry and repair: a model call that breaks off with a transient error (the connection dropped, the service was
  overloaded) is tried again, at most twice. When it had already streamed text, a CallRestarted event tells the chat
  to drop the text and reasoning of that broken call, since the new attempt writes it again from the start.
- Parallel tool calls: tools that only read run at the same time when the model asks for several in one turn;
  results keep the order of the calls.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from app.providers.base import (
    AgentEvent,
    CallRestarted,
    ProviderError,
    ToolBox,
    ToolCallFinished,
    ToolCallStarted,
    ToolResult,
)

RETRY_DELAYS_SECONDS = (2.0, 6.0)
INVALID_ARGUMENTS = "The arguments were not a valid JSON object."


async def with_retries(
    open_call: Callable[[], AsyncIterator[Any]], delays: tuple[float, ...] | None = None
) -> AsyncIterator[Any]:
    """The items of one model call, trying the call again after a transient failure."""
    delays = RETRY_DELAYS_SECONDS if delays is None else delays
    for attempt in range(len(delays) + 1):
        streamed = False
        try:
            async for item in open_call():
                streamed = True
                yield item
            return
        except ProviderError as error:
            if not error.transient or attempt == len(delays):
                raise
            if streamed:
                yield CallRestarted(attempt + 1, error.message)
            await asyncio.sleep(delays[attempt])


@dataclass(frozen=True)
class PlannedCall:
    id: str
    name: str
    # None: the model sent arguments that are not a JSON object.
    arguments: dict[str, Any] | None


async def run_tool_calls(
    tool_box: ToolBox, calls: list[PlannedCall], results: dict[str, ToolResult]
) -> AsyncIterator[AgentEvent]:
    """Run one turn's tool calls; consecutive read-only calls run together. Results land in `results` by call id."""
    read_only = {definition.name for definition in tool_box.definitions if definition.read_only}

    def parallel(call: PlannedCall) -> bool:
        return call.arguments is not None and call.name in read_only

    index = 0
    while index < len(calls):
        group = [calls[index]]
        if parallel(calls[index]):
            while index + len(group) < len(calls) and parallel(calls[index + len(group)]):
                group.append(calls[index + len(group)])
        for call in group:
            yield ToolCallStarted(call.id, call.name, call.arguments or {})

        async def execute(call: PlannedCall) -> ToolResult:
            if call.arguments is None:
                return ToolResult(INVALID_ARGUMENTS, is_error=True)
            return await tool_box.call(call.name, call.arguments)

        outcomes = await asyncio.gather(*(execute(call) for call in group))
        for call, result in zip(group, outcomes, strict=True):
            results[call.id] = result
            yield ToolCallFinished(call.id, result.text, result.is_error)
        index += len(group)
