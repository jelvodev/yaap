"""The events of one reply: the browser's server-sent events, the reply's content blocks and the merged event stream."""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import anyio

from app.core.background import BackgroundWork
from app.providers.base import (
    AgentEvent,
    CallRestarted,
    Completed,
    ReasoningDelta,
    TextDelta,
    ToolCallFinished,
    ToolCallStarted,
)


def server_sent_event(name: str, payload: dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(payload)}\n\n"


@dataclass
class ReplyOutcome:
    status: str
    stop_reason: str | None
    error: str | None


def outcome_from_completion(completed: Completed) -> ReplyOutcome:
    if completed.stop_reason == "refusal":
        category = f" (category: {completed.refusal_category})" if completed.refusal_category else ""
        return ReplyOutcome("refused", completed.stop_reason, f"The model declined this request{category}.")
    if completed.stop_reason == "max_tokens":
        return ReplyOutcome("incomplete", completed.stop_reason, "The reply reached the maximum length.")
    if completed.stop_reason == "max_turns":
        return ReplyOutcome("incomplete", completed.stop_reason, "The reply reached the limit of model calls in one run.")
    return ReplyOutcome("complete", completed.stop_reason, None)


MAX_STORED_TOOL_OUTPUT = 20_000


class ReplyContent:
    """The reply in the order it happened: reasoning, text and tool calls, as neutral content blocks."""

    def __init__(self) -> None:
        self.blocks: list[dict[str, Any]] = []

    def add_text(self, kind: str, text: str) -> None:
        if self.blocks and self.blocks[-1]["type"] == kind:
            self.blocks[-1]["text"] += text
        else:
            self.blocks.append({"type": kind, "text": text})

    def start_tool(self, event: ToolCallStarted) -> None:
        self.blocks.append(
            {"type": "tool_call", "id": event.id, "name": event.name, "input": event.input, "output": None, "is_error": False}
        )

    def drop_unfinished_call(self) -> None:
        """Remove text and reasoning after the last tool call: a model call that broke off and starts again."""
        while self.blocks and self.blocks[-1]["type"] in ("text", "reasoning"):
            self.blocks.pop()

    def finish_tool(self, event: ToolCallFinished) -> None:
        for block in reversed(self.blocks):
            if block["type"] == "tool_call" and block["id"] == event.id:
                output = event.output
                if len(output) > MAX_STORED_TOOL_OUTPUT:
                    output = output[:MAX_STORED_TOOL_OUTPUT] + "\n[Output shortened for storage.]"
                block.update(output=output, is_error=event.is_error)
                return

    def content(self) -> list[dict[str, Any]]:
        blocks = [
            {**block, "text": block["text"].strip()} if block["type"] == "reasoning" else block
            for block in self.blocks
            if block["type"] != "reasoning" or block["text"].strip()
        ]
        if not any(block["type"] == "text" for block in blocks):
            blocks.append({"type": "text", "text": ""})
        return blocks


def reply_event(event: AgentEvent | dict[str, Any], reply: ReplyContent) -> tuple[str, dict[str, Any]] | None:
    """Apply a model or run event to the reply and return the event sent to the browser."""
    if isinstance(event, TextDelta):
        reply.add_text("text", event.text)
        return "text_delta", {"text": event.text}
    if isinstance(event, ReasoningDelta):
        reply.add_text("reasoning", event.text)
        return "reasoning_delta", {"text": event.text}
    if isinstance(event, ToolCallStarted):
        reply.start_tool(event)
        return "tool_call", {"id": event.id, "name": event.name, "input": event.input}
    if isinstance(event, ToolCallFinished):
        reply.finish_tool(event)
        return "tool_result", {
            "id": event.id,
            "output": event.output[:MAX_STORED_TOOL_OUTPUT],
            "is_error": event.is_error,
        }
    if isinstance(event, CallRestarted):
        reply.drop_unfinished_call()
        return "call_restarted", {"attempt": event.attempt, "reason": event.reason}
    if isinstance(event, dict):
        name = event.pop("event")
        if name == "steering":
            reply.blocks.append({"type": "steering", "text": event["text"]})
        return name, event
    return None

async def merge_events(
    provider_events: AsyncIterator[AgentEvent], run_events: "asyncio.Queue[dict[str, Any]] | None"
) -> AsyncIterator[AgentEvent | dict[str, Any]]:
    """The model's events, interleaved with the run's own events (approval requests) as they happen."""
    if run_events is None:
        async for event in provider_events:
            yield event
        return
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    async def pump_provider() -> None:
        try:
            async for event in provider_events:
                await queue.put(("event", event))
            await queue.put(("done", None))
        except Exception as error:  # noqa: BLE001 - handed to the consumer, which re-raises it
            await queue.put(("error", error))

    async def pump_run() -> None:
        while True:
            await queue.put(("event", await run_events.get()))

    # The pumps run in cancel scopes of their own: stopping them waits for a worker thread that a tool of the
    # provider's tool loop (inside pump_provider) is in.
    pumps: BackgroundWork[None] = BackgroundWork()
    pumps.start(pump_provider())
    pumps.start(pump_run())
    try:
        while True:
            kind, value = await queue.get()
            if kind == "done":
                # Run events that came with the provider's last steps (a steering marker, live output) still go out.
                while not queue.empty():
                    kind, value = queue.get_nowait()
                    if kind == "event":
                        yield value
                while not run_events.empty():
                    yield run_events.get_nowait()
                return
            if kind == "error":
                raise value
            yield value
    finally:
        # Shielded: a stopped reply's scope keeps cancelling every await until the reply has ended.
        with anyio.CancelScope(shield=True):
            await pumps.stop()
