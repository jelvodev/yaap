"""The Anthropic adapter, tested against a stand-in client so no real API call is made."""

import asyncio
from types import SimpleNamespace

import anthropic

from app.providers.anthropic_provider import SERVER_SIDE_FALLBACK_BETA, AnthropicProvider, to_provider_error
from app.providers.base import (
    AgentRequest,
    Completed,
    HistoryMessage,
    ModelCallFinished,
    ReasoningDelta,
    SystemPart,
    TextDelta,
    TokenUsage,
)


class Block:
    def __init__(self, data: dict) -> None:
        self.data = data

    def model_dump(self, **_kwargs) -> dict:
        return self.data


class StandInStream:
    def __init__(self) -> None:
        self.events = [
            SimpleNamespace(type="message_start"),
            SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="thinking_delta", thinking="Hmm")),
            SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text="Hoi")),
        ]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for event in self.events:
            yield event

    async def get_final_message(self):
        return SimpleNamespace(
            content=[Block({"type": "thinking", "thinking": "Hmm", "signature": "sig"}), Block({"type": "text", "text": "Hoi"})],
            stop_reason="end_turn",
            stop_details=None,
            model="claude-opus-5-5",
            usage=SimpleNamespace(
                input_tokens=12, output_tokens=3, cache_read_input_tokens=100, cache_creation_input_tokens=None
            ),
        )


class StandInMessages:
    def __init__(self, calls: list) -> None:
        self.calls = calls

    def stream(self, **params):
        self.calls.append(params)
        return StandInStream()


def make_provider(calls: list) -> AnthropicProvider:
    provider = AnthropicProvider("sk-test", frozenset({"claude-opus-5-5"}))
    messages = StandInMessages(calls)
    provider._client = SimpleNamespace(messages=messages, beta=SimpleNamespace(messages=messages))
    return provider


def collect(provider: AnthropicProvider, request: AgentRequest) -> list:
    async def run():
        return [event async for event in provider.stream(request)]

    return asyncio.run(run())


def make_request(model: str) -> AgentRequest:
    return AgentRequest(
        model=model,
        effort="low",
        system=[SystemPart("Base prompt", cacheable=True)],
        messages=[
            HistoryMessage("user", [{"type": "text", "text": "Hallo"}]),
            HistoryMessage(
                "assistant",
                [{"type": "reasoning", "text": "x"}, {"type": "text", "text": "Hoi"}],
                provider_raw=[{"type": "thinking", "thinking": "x", "signature": "s"}, {"type": "text", "text": "Hoi"}],
            ),
            HistoryMessage("user", [{"type": "text", "text": "Nog een vraag"}]),
        ],
    )


def test_request_mapping_and_events_with_fallback():
    calls: list = []
    events = collect(make_provider(calls), make_request("claude-opus-5-5"))

    params = calls[0]
    assert params["betas"] == [SERVER_SIDE_FALLBACK_BETA]
    assert params["fallbacks"] == "default"
    assert params["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert params["output_config"] == {"effort": "low"}
    assert params["system"] == [{"type": "text", "text": "Base prompt", "cache_control": {"type": "ephemeral"}}]
    assert params["messages"][1]["content"][0]["signature"] == "s"
    assert params["messages"][2] == {"role": "user", "content": [{"type": "text", "text": "Nog een vraag"}]}

    assert events[0] == ReasoningDelta("Hmm")
    assert events[1] == TextDelta("Hoi")
    assert isinstance(events[2], ModelCallFinished) and events[2].usage.cache_read_tokens == 100
    completed = events[3]
    assert isinstance(completed, Completed)
    assert completed.usage.cache_read_tokens == 100
    assert completed.usage.cache_write_tokens == 0
    assert completed.provider_raw[0]["signature"] == "sig"


def make_status_error(error_class, status: int, vendor_message: str):
    import httpx2

    response = httpx2.Response(
        status,
        headers={"request-id": "req_test123"},
        request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"),
    )
    body = {"type": "error", "error": {"type": "rate_limit_error", "message": vendor_message}}
    return error_class(f"Error code: {status}", response=response, body=body)


def test_rate_limit_error_explains_where_to_look_and_keeps_the_request_id():
    error = to_provider_error(make_status_error(anthropic.RateLimitError, 429, "Error"))
    assert "credit balance" in error.message
    assert "Request id: req_test123" in error.message
    assert "Anthropic says" not in error.message


def test_vendor_message_is_passed_on():
    error = to_provider_error(make_status_error(anthropic.BadRequestError, 400, "max_tokens is too large"))
    assert error.message == "Anthropic rejected the request. Anthropic says: max_tokens is too large. Request id: req_test123."


def test_models_without_fallback_use_the_standard_endpoint():
    calls: list = []
    collect(make_provider(calls), make_request("claude-haiku-5-5"))
    assert "fallbacks" not in calls[0]
    assert "betas" not in calls[0]


class ScriptedStream(StandInStream):
    def __init__(self, content: list[dict], stop_reason: str) -> None:
        super().__init__()
        self.events = [SimpleNamespace(type="message_start")]
        self.content, self.stop_reason = content, stop_reason

    async def get_final_message(self):
        return SimpleNamespace(
            content=[Block(block) for block in self.content],
            stop_reason=self.stop_reason,
            stop_details=None,
            model="claude-sonnet-5-5",
            usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0),
        )


class ScriptedMessages:
    def __init__(self, calls: list, script: list[ScriptedStream]) -> None:
        self.calls, self.script = calls, script

    def stream(self, **params):
        self.calls.append(params)
        return self.script.pop(0)


class RecordingToolBox:
    def __init__(self) -> None:
        self.calls: list = []

    @property
    def definitions(self):
        from app.providers.base import ToolDefinition

        return [ToolDefinition("read_file", "Read a file.", {"type": "object", "properties": {"path": {"type": "string"}}})]

    async def call(self, name, arguments):
        from app.providers.base import ToolResult

        self.calls.append((name, arguments))
        return ToolResult(f"contents of {arguments['path']}", images=(("image/png", "AAAA"),))


def scripted_provider(calls: list, script: list[ScriptedStream]) -> AnthropicProvider:
    provider = AnthropicProvider("sk-test", frozenset())
    provider._client = SimpleNamespace(messages=ScriptedMessages(calls, script))
    return provider


TOOL_USE = {"type": "tool_use", "id": "call-1", "name": "read_file", "input": {"path": "a.py"}}


def test_the_tool_loop_runs_tools_and_stores_every_turn():
    from dataclasses import replace

    from app.providers.anthropic_provider import TURNS_BLOCK_TYPE, to_anthropic_conversation
    from app.providers.base import ToolCallFinished, ToolCallStarted

    calls: list = []
    tools = RecordingToolBox()
    script = [
        ScriptedStream([{"type": "thinking", "thinking": "Look", "signature": "s1"}, TOOL_USE], "tool_use"),
        ScriptedStream([{"type": "text", "text": "It prints 1."}], "end_turn"),
    ]
    request = replace(make_request("claude-sonnet-5-5"), tool_box=tools)
    events = collect(scripted_provider(calls, script), request)

    assert calls[0]["tools"][0]["name"] == "read_file"
    assert tools.calls == [("read_file", {"path": "a.py"})]
    assert events[0] == ModelCallFinished("claude-sonnet-5-5", TokenUsage(input_tokens=10, output_tokens=5))
    assert events[1] == ToolCallStarted("call-1", "read_file", {"path": "a.py"})
    assert events[2] == ToolCallFinished("call-1", "contents of a.py", False)
    # Each model call is reported with its own usage; together they make the reply's usage.
    reports = [event for event in events if isinstance(event, ModelCallFinished)]
    assert sum(report.usage.input_tokens for report in reports) == 20
    second = calls[1]["messages"]
    assert second[-2]["content"][1] == TOOL_USE and second[-2]["content"][0]["signature"] == "s1"
    result = second[-1]["content"][0]
    assert result["tool_use_id"] == "call-1" and result["content"][1]["type"] == "image"

    completed = events[-1]
    assert completed.stop_reason == "end_turn"
    assert completed.usage.input_tokens == 20 and completed.context_tokens == 15
    assert completed.provider_raw[0]["type"] == TURNS_BLOCK_TYPE
    stored = completed.provider_raw[0]["messages"]
    assert [turn["role"] for turn in stored] == ["assistant", "user", "assistant"]
    # The stored tool result keeps the text, not the image.
    assert all(block["type"] == "text" for block in stored[1]["content"][0]["content"])

    # Replayed later: all turns come back, followed by the next question.
    history = [
        HistoryMessage("user", [{"type": "text", "text": "What does a.py print?"}]),
        HistoryMessage("assistant", [{"type": "text", "text": "It prints 1."}], provider_raw=completed.provider_raw),
        HistoryMessage("user", [{"type": "text", "text": "Thanks"}]),
    ]
    assert [message["role"] for message in to_anthropic_conversation(history)] == ["user", "assistant", "user", "assistant", "user"]


def test_tool_calls_left_at_the_turn_limit_get_a_result(monkeypatch):
    from dataclasses import replace

    import app.providers.anthropic_provider as adapter

    monkeypatch.setattr(adapter, "MAX_TOOL_TURNS", 1)
    calls: list = []
    request = replace(make_request("claude-sonnet-5-5"), tool_box=RecordingToolBox())
    events = collect(scripted_provider(calls, [ScriptedStream([TOOL_USE], "tool_use")]), request)

    completed = events[-1]
    assert completed.stop_reason == "max_turns"
    stored = completed.provider_raw[0]["messages"]
    assert stored[-1]["role"] == "user" and stored[-1]["content"][0]["tool_use_id"] == "call-1"
    history = [
        HistoryMessage("assistant", [], provider_raw=completed.provider_raw),
        HistoryMessage("user", [{"type": "text", "text": "Go on"}]),
    ]
    messages = adapter.to_anthropic_conversation(history)
    # The tool result and the new question are joined into one user turn.
    assert [message["role"] for message in messages] == ["assistant", "user"]
    assert [block["type"] for block in messages[1]["content"]] == ["tool_result", "text"]


def test_replies_from_claude_code_are_replayed_as_text():
    from app.providers.anthropic_provider import to_anthropic_conversation

    history = [
        HistoryMessage("user", [{"type": "text", "text": "Hi"}]),
        HistoryMessage(
            "assistant", [{"type": "text", "text": "Hello"}], provider_raw=[{"type": "claude_code_session", "session_id": "x"}]
        ),
    ]
    assert to_anthropic_conversation(history)[1] == {"role": "assistant", "content": [{"type": "text", "text": "Hello"}]}
