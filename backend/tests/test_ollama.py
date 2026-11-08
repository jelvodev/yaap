"""Local models through Ollama."""

import asyncio
import json
from types import SimpleNamespace

import httpx

from app.providers import ollama_provider
from app.providers.base import AgentRequest, Completed, HistoryMessage, SystemPart, TextDelta, ToolCallFinished, ToolDefinition, ToolResult
from app.providers.ollama_provider import OllamaProvider, OllamaState, refresh, to_messages
from app.providers.registry import default_provider_registry


def fake_server(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/tags":
        return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}, {"name": "llama3.2:3b"}]})
    if request.url.path == "/api/show":
        name = json.loads(request.content)["model"]
        info = {"qwen3.context_length": 40960} if name.startswith("qwen") else {}
        return httpx.Response(200, json={"model_info": info})
    return httpx.Response(404)


def test_models_are_read_from_the_server(monkeypatch):
    original = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **options: original(transport=httpx.MockTransport(fake_server), **options))
    registry = default_provider_registry()
    assert registry.find_model("ollama/qwen3:8b") is None
    refresh(registry.ollama, "http://ollama:11434")
    qwen = registry.find_model("ollama/qwen3:8b")
    assert qwen is not None and qwen.context_window == 40960 and qwen.pricing.standard.input == 0
    assert registry.find_model("ollama/llama3.2:3b").context_window == ollama_provider.DEFAULT_CONTEXT
    spec = registry.find_provider("ollama")
    assert spec is not None and not spec.metered and spec.credential_kind is None and spec.runs_agents
    # A server that is down keeps the models known so far.
    monkeypatch.setattr(httpx, "Client", lambda **options: original(transport=httpx.MockTransport(lambda _r: httpx.Response(500)), **options))
    refresh(registry.ollama, "http://ollama:11434", force=True)
    assert registry.find_model("ollama/qwen3:8b") is not None and "Could not reach Ollama" in registry.ollama.error
    refresh(registry.ollama, "")
    assert registry.find_model("ollama/qwen3:8b") is None


def test_the_ollama_address_is_a_global_setting(client):
    assert client.put("/api/v1/settings/ollama_url", json={"value": "not a url"}).status_code == 422
    assert client.put("/api/v1/settings/ollama_url", json={"value": "http://host.docker.internal:11434"}).status_code == 200
    assert client.put("/api/v1/environments/1/settings/ollama_url", json={"value": "http://x:1"}).status_code in (400, 422)


def chunk(content=None, tool_calls=None, finish=None, usage=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls, reasoning=None)
    choices = [SimpleNamespace(delta=delta, finish_reason=finish)] if content is not None or tool_calls or finish else []
    return SimpleNamespace(choices=choices, usage=usage)


class Stream:
    def __init__(self, chunks):
        self.chunks = chunks

    def __aiter__(self):
        async def generate():
            for item in self.chunks:
                yield item

        return generate()


class FakeCompletions:
    def __init__(self, turns):
        self.turns = turns
        self.requests = []

    async def create(self, **params):
        self.requests.append(params)
        return Stream(self.turns.pop(0))


class Box:
    definitions = [ToolDefinition("list_files", "List files", {"type": "object", "properties": {}}, True)]

    async def call(self, name, arguments):
        return ToolResult("a.py\nb.py", False)


def test_the_tool_loop_runs_over_chat_completions():
    call = SimpleNamespace(index=0, id="call_1", function=SimpleNamespace(name="list_files", arguments="{}"))
    usage = SimpleNamespace(prompt_tokens=100, completion_tokens=20)
    completions = FakeCompletions(
        [
            [chunk(tool_calls=[call]), chunk(finish="tool_calls"), chunk(usage=usage)],
            [chunk(content="Two files."), chunk(finish="stop"), chunk(usage=usage)],
        ]
    )
    provider = OllamaProvider("http://ollama:11434")
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    request = AgentRequest(
        model="ollama/qwen3:8b",
        effort="medium",
        system=[SystemPart("Be brief.")],
        messages=[HistoryMessage("user", [{"type": "text", "text": "Which files?"}])],
        tool_box=Box(),
    )

    async def collect():
        return [event async for event in provider.stream(request)]

    events = asyncio.run(collect())
    assert completions.requests[0]["model"] == "qwen3:8b" and completions.requests[0]["messages"][0]["role"] == "system"
    assert any(isinstance(event, ToolCallFinished) for event in events)
    assert "".join(event.text for event in events if isinstance(event, TextDelta)) == "Two files."
    completed = events[-1]
    assert isinstance(completed, Completed) and completed.usage.input_tokens == 200
    replay = completed.provider_raw[0]["messages"]
    assert [item["role"] for item in replay] == ["assistant", "tool", "assistant"]
    assert completions.requests[1]["messages"][-1] == {"role": "tool", "tool_call_id": "call_1", "content": "a.py\nb.py"}
    # The stored reply is replayed as it was; other vendors' replies go as text.
    assert to_messages(HistoryMessage("assistant", [], completed.provider_raw)) == replay
    assert to_messages(HistoryMessage("assistant", [{"type": "text", "text": "Hi"}], [{"type": "anthropic_turns"}])) == [
        {"role": "assistant", "content": "Hi"}
    ]


def test_without_an_address_the_provider_explains():
    from app.providers.base import ProviderError

    try:
        OllamaProvider("")
        raise AssertionError("expected an error")
    except ProviderError as error:
        assert "Settings" in error.message
    assert OllamaState().models == []
