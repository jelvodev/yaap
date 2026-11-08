"""The OpenAI adapter, tested against a stand-in client so no real API call is made."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

from app.providers.base import (
    AgentRequest,
    Completed,
    HistoryMessage,
    ReasoningDelta,
    SystemPart,
    TextDelta,
    ToolCallFinished,
    ToolCallStarted,
    ToolDefinition,
    ToolResult,
)
from app.providers.catalog import OPENAI_MODELS, calculate_cost
from app.providers.openai_provider import ITEMS_BLOCK_TYPE, OpenAIProvider, to_input_items
from app.providers.base import TokenUsage


class Item:
    def __init__(self, data: dict) -> None:
        self.data = data

    def model_dump(self, **_kwargs) -> dict:
        return dict(self.data)


def response(output: list[dict], status: str = "completed", input_tokens: int = 100, cached: int = 40) -> SimpleNamespace:
    return SimpleNamespace(
        output=[Item(item) for item in output],
        status=status,
        model="gpt-6.1-sol-2026-10-01",
        incomplete_details=None,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            input_tokens_details=SimpleNamespace(cached_tokens=cached),
            output_tokens=20,
        ),
    )


def events(final: SimpleNamespace, text: str = "", summary: str = "") -> list[SimpleNamespace]:
    result = []
    if summary:
        result.append(SimpleNamespace(type="response.reasoning_summary_text.delta", delta=summary))
    if text:
        result.append(SimpleNamespace(type="response.output_text.delta", delta=text))
    result.append(SimpleNamespace(type="response.completed", response=final))
    return result


class StandInResponses:
    def __init__(self, calls: list, script: list[list[SimpleNamespace]]) -> None:
        self.calls, self.script = calls, script

    async def create(self, **params):
        self.calls.append(params)
        stream = self.script.pop(0)

        async def iterate():
            for event in stream:
                yield event

        return iterate()


def make_provider(calls: list, script: list) -> OpenAIProvider:
    provider = OpenAIProvider("sk-test")
    provider._client = SimpleNamespace(responses=StandInResponses(calls, script))
    return provider


def collect(provider: OpenAIProvider, request: AgentRequest) -> list:
    async def run():
        return [event async for event in provider.stream(request)]

    return asyncio.run(run())


def make_request(**changes) -> AgentRequest:
    request = AgentRequest(
        model="gpt-6.1-sol",
        effort="medium",
        system=[SystemPart("Base prompt"), SystemPart("Project")],
        messages=[
            HistoryMessage(
                "user",
                [
                    {"type": "text", "text": "Look"},
                    {"type": "image", "media_type": "image/png", "data": "AAAA"},
                    {"type": "document", "media_type": "application/pdf", "data": "BBBB", "title": "spec.pdf"},
                ],
            ),
            HistoryMessage("assistant", [{"type": "text", "text": "Seen"}], provider_raw=[{"type": "claude_code_session"}]),
            HistoryMessage("user", [{"type": "text", "text": "And now?"}]),
        ],
    )
    return replace(request, **changes)


class RecordingToolBox:
    def __init__(self) -> None:
        self.calls: list = []

    @property
    def definitions(self):
        return [ToolDefinition("read_file", "Read a file.", {"type": "object", "properties": {"path": {"type": "string"}}})]

    async def call(self, name, arguments):
        self.calls.append((name, arguments))
        return ToolResult(f"contents of {arguments['path']}", images=(("image/png", "CCCC"),))


MESSAGE = {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": "Done."}]}


def test_a_plain_reply_maps_the_request_and_streams_text_and_summary():
    calls: list = []
    final = response([{"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "enc"}, MESSAGE])
    result = collect(make_provider(calls, [events(final, "Done.", "Thinking it over")]), make_request())

    params = calls[0]
    assert params["instructions"] == "Base prompt\n\nProject"
    assert params["store"] is False and params["stream"] is True
    assert params["reasoning"] == {"effort": "medium", "summary": "auto"}
    assert "tools" not in params
    first = params["input"][0]["content"]
    assert first[1] == {"type": "input_image", "image_url": "data:image/png;base64,AAAA"}
    assert first[2]["type"] == "input_file" and first[2]["filename"] == "spec.pdf"
    # A Claude Code reply goes along as text.
    assert params["input"][1] == {"role": "assistant", "content": "Seen"}

    assert result[0] == ReasoningDelta("Thinking it over") and result[1] == TextDelta("Done.")
    completed = result[-1]
    assert isinstance(completed, Completed) and completed.stop_reason == "end_turn"
    assert completed.usage == TokenUsage(input_tokens=60, output_tokens=20, cache_read_tokens=40)
    items = completed.provider_raw[0]["items"]
    assert completed.provider_raw[0]["type"] == ITEMS_BLOCK_TYPE
    # Reasoning keeps its id and encrypted content; other items lose the id and status.
    assert items[0] == {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "enc"}
    assert "id" not in items[1] and "status" not in items[1]


def test_the_tool_loop_sends_results_back_and_stores_every_item():
    calls: list = []
    tools = RecordingToolBox()
    call = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "read_file", "arguments": '{"path": "a.py"}'}
    script = [
        events(response([{"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "e1"}, call])),
        events(response([MESSAGE]), "Done."),
    ]
    result = collect(make_provider(calls, script), make_request(tool_box=tools))

    assert calls[0]["tools"][0] == {
        "type": "function",
        "name": "read_file",
        "description": "Read a file.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        "strict": False,
    }
    assert tools.calls == [("read_file", {"path": "a.py"})]
    assert ToolCallStarted("call_1", "read_file", {"path": "a.py"}) in result
    assert ToolCallFinished("call_1", "contents of a.py", False) in result
    second = calls[1]["input"]
    assert second[-2]["type"] == "function_call" and second[-3]["encrypted_content"] == "e1"
    assert second[-1]["output"][1] == {"type": "input_image", "image_url": "data:image/png;base64,CCCC"}

    completed = result[-1]
    assert completed.usage.input_tokens == 120 and completed.context_tokens == 120
    stored = completed.provider_raw[0]["items"]
    assert [item["type"] for item in stored] == ["reasoning", "function_call", "function_call_output", "message"]
    assert isinstance(stored[2]["output"], str) and "image" in stored[2]["output"]

    # Replayed later, exactly these items come back.
    history = HistoryMessage("assistant", [], provider_raw=completed.provider_raw)
    assert to_input_items(history) == stored


def test_invalid_arguments_and_the_turn_limit_leave_a_valid_history(monkeypatch):
    import app.providers.openai_provider as adapter

    monkeypatch.setattr(adapter, "MAX_TOOL_TURNS", 1)
    calls: list = []
    bad = {"type": "function_call", "call_id": "call_1", "name": "read_file", "arguments": "{not json"}
    result = collect(make_provider(calls, [events(response([bad]))]), make_request(tool_box=RecordingToolBox()))

    assert ToolCallFinished("call_1", "The arguments were not a valid JSON object.", True) in result
    completed = result[-1]
    assert completed.stop_reason == "max_turns"
    assert completed.provider_raw[0]["items"][-1]["call_id"] == "call_1"


def test_an_incomplete_response_is_reported_as_too_long():
    final = response([MESSAGE], status="incomplete")
    final.incomplete_details = SimpleNamespace(reason="max_output_tokens")
    stream = [SimpleNamespace(type="response.incomplete", response=final)]
    assert collect(make_provider([], [stream]), make_request())[-1].stop_reason == "max_tokens"


def test_openai_prices_switch_to_the_long_context_rate():
    sol = next(model for model in OPENAI_MODELS if model.id == "gpt-6.1-sol")
    assert calculate_cost(sol, TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)) == 4.00 + 15.00
    assert calculate_cost(sol, TokenUsage(input_tokens=100_000, output_tokens=100_000)) == 0.2 + 1.0
