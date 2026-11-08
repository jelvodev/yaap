"""Retry and repair of a broken model call, and read-only tool calls running together."""

import asyncio
import time
from dataclasses import replace
from types import SimpleNamespace

import httpx2
import pytest

from app.core.chat import ReplyContent
from app.providers import tool_loop
from app.providers.base import (
    CallRestarted,
    Completed,
    ProviderError,
    TextDelta,
    ToolCallFinished,
    ToolCallStarted,
    ToolDefinition,
    ToolResult,
)
from app.providers.tool_loop import PlannedCall, run_tool_calls, with_retries
from tests.test_anthropic_provider import ScriptedStream, collect, make_request, scripted_provider


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(tool_loop, "RETRY_DELAYS_SECONDS", (0.0, 0.0))


def gather(iterator) -> list:
    async def run():
        return [item async for item in iterator]

    return asyncio.run(run())


def attempts(*scripts):
    """A model call whose attempts follow the scripts: items, then an optional error."""
    remaining = list(scripts)

    async def call():
        items, error = remaining.pop(0)
        for item in items:
            yield item
        if error is not None:
            raise error

    return call


def test_a_transient_break_is_tried_again_and_reported():
    call = attempts((["half"], ProviderError("The connection broke off.", transient=True)), (["whole"], None))
    assert gather(with_retries(call)) == ["half", CallRestarted(1, "The connection broke off."), "whole"]


def test_a_break_before_any_output_retries_silently():
    call = attempts(([], ProviderError("Overloaded.", transient=True)), (["whole"], None))
    assert gather(with_retries(call)) == ["whole"]


def test_permanent_errors_and_exhausted_retries_end_the_reply():
    with pytest.raises(ProviderError, match="Rejected"):
        gather(with_retries(attempts(([], ProviderError("Rejected.")), (["never"], None))))
    broken = ([], ProviderError("Down.", transient=True))
    with pytest.raises(ProviderError, match="Down"):
        gather(with_retries(attempts(broken, broken, broken, (["never"], None))))


class BreakingStream(ScriptedStream):
    """Streams some text, then the connection drops."""

    def __init__(self) -> None:
        super().__init__([], "end_turn")
        self.events = [
            SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text="Half a sen")),
        ]

    async def _iterate(self):
        for event in self.events:
            yield event
        raise httpx2.ReadError("connection reset")


def test_the_anthropic_loop_repairs_a_broken_stream():
    calls: list = []
    retry = ScriptedStream([{"type": "text", "text": "A whole sentence."}], "end_turn")
    retry.events.append(
        SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text="A whole sentence."))
    )
    events = collect(scripted_provider(calls, [BreakingStream(), retry]), make_request("claude-sonnet-5-5"))
    assert events[0] == TextDelta("Half a sen")
    assert events[1] == CallRestarted(1, "The connection to Anthropic broke off.")
    assert events[2] == TextDelta("A whole sentence.")
    assert isinstance(events[-1], Completed) and events[-1].stop_reason == "end_turn"
    assert len(calls) == 2 and calls[0]["messages"] == calls[1]["messages"]


def test_the_reply_drops_the_text_of_a_broken_call_only():
    reply = ReplyContent()
    reply.add_text("text", "Before the tool.")
    reply.start_tool(ToolCallStarted("t1", "read_file", {}))
    reply.finish_tool(ToolCallFinished("t1", "ok", False))
    reply.add_text("reasoning", "Thinking about")
    reply.add_text("text", "Half a sen")
    reply.drop_unfinished_call()
    reply.add_text("text", "A whole sentence.")
    assert [block.get("text") for block in reply.content()] == ["Before the tool.", None, "A whole sentence."]


class SlowToolBox:
    def __init__(self) -> None:
        self.running = 0
        self.most_at_once = 0

    @property
    def definitions(self):
        schema = {"type": "object"}
        return [
            ToolDefinition("read_file", "Read.", schema, read_only=True),
            ToolDefinition("search", "Search.", schema, read_only=True),
            ToolDefinition("write_file", "Write.", schema),
        ]

    async def call(self, name, arguments):
        self.running += 1
        self.most_at_once = max(self.most_at_once, self.running)
        await asyncio.sleep(0.2)
        self.running -= 1
        return ToolResult(f"{name} {arguments.get('path', '')}".strip())


def test_read_only_calls_run_together_and_keep_their_order():
    box = SlowToolBox()
    calls = [
        PlannedCall("1", "read_file", {"path": "a"}),
        PlannedCall("2", "search", {}),
        PlannedCall("3", "read_file", {"path": "b"}),
        PlannedCall("4", "write_file", {"path": "c"}),
        PlannedCall("5", "read_file", None),
    ]
    results: dict[str, ToolResult] = {}
    started = time.monotonic()
    events = gather(run_tool_calls(box, calls, results))
    elapsed = time.monotonic() - started

    assert box.most_at_once == 3
    assert elapsed < 0.75  # three reads together (0.2 s), then the write (0.2 s); not five calls after each other
    finished = [event.id for event in events if isinstance(event, ToolCallFinished)]
    assert finished == ["1", "2", "3", "4", "5"]
    assert results["3"].text == "read_file b"
    assert results["5"].is_error and "not a valid JSON object" in results["5"].text


def test_the_anthropic_loop_runs_parallel_calls_with_results_in_order():
    calls: list = []
    first = {"type": "tool_use", "id": "a", "name": "read_file", "input": {"path": "a"}}
    second = {"type": "tool_use", "id": "b", "name": "read_file", "input": {"path": "b"}}
    script = [ScriptedStream([first, second], "tool_use"), ScriptedStream([{"type": "text", "text": "Done."}], "end_turn")]
    box = SlowToolBox()
    request = replace(make_request("claude-sonnet-5-5"), tool_box=box)
    collect(scripted_provider(calls, script), request)
    results = calls[1]["messages"][-1]["content"]
    assert [result["tool_use_id"] for result in results] == ["a", "b"]
    assert box.most_at_once == 2
