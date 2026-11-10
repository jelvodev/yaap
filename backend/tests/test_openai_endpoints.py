"""OpenAI-compatible endpoints: the setting, the model listing, the shared adapter and the policy decisions."""

import asyncio
import json

import httpx
import openai
import pytest

from app.core.model_policy import (
    LevelPolicy,
    ModelPolicy,
    PolicySource,
    PolicyTarget,
    check_policy,
    connection_key,
    model_refusal,
)
from app.providers.base import AgentRequest, Completed, HistoryMessage, ProviderError, SystemPart, TextDelta, ToolDefinition
from app.providers.chat_completions import ChatCompletionsProvider
from app.providers.endpoints import Endpoint, check_endpoints, parse_endpoints, parse_models, refresh_endpoints
from app.providers.registry import default_provider_registry

OPENROUTER = Endpoint("openrouter", "https://openrouter.ai/api/v1")
LISTING = {
    "data": [
        {
            "id": "vendor/priced",
            "name": "Vendor Priced",
            "context_length": 200000,
            "pricing": {"prompt": "0.000003", "completion": "0.000015", "input_cache_read": "0.0000003"},
            "top_provider": {"max_completion_tokens": 8192},
        },
        {"id": "plain-model"},
        {"id": "vllm-model", "max_model_len": 8192, "pricing": {"prompt": "-1", "completion": "-1"}},
        {"name": "no id"},
    ]
}


def test_models_get_context_and_prices_from_the_listing_when_present():
    models = {model.id: model for model in parse_models(OPENROUTER, LISTING)}
    assert sorted(models) == ["openrouter/plain-model", "openrouter/vendor/priced", "openrouter/vllm-model"]
    priced = models["openrouter/vendor/priced"]
    assert priced.provider == "openrouter" and priced.display_name == "Vendor Priced (openrouter)"
    assert priced.context_window == 200000 and priced.max_output_tokens == 8192
    tier = priced.pricing.standard
    assert (tier.input, tier.output, tier.cache_read, tier.cache_write) == pytest.approx((3.0, 15.0, 0.3, 3.0))
    plain = models["openrouter/plain-model"]
    assert plain.context_window == 32_768 and plain.pricing.standard.input == 0 and plain.pricing.standard.output == 0
    # A negative price in the listing (a routing model) is no price; the context field of vLLM is read.
    vllm = models["openrouter/vllm-model"]
    assert vllm.context_window == 8192 and vllm.pricing.standard.input == 0


@pytest.mark.parametrize("payload", [None, [], {"data": None}, {"data": "x"}, {"data": [1, None]}])
def test_a_listing_of_another_shape_has_no_models(payload):
    assert parse_models(OPENROUTER, payload) == []


def many(count):
    return [{"name": f"e{number}", "base_url": "http://x/v1"} for number in range(count)]


@pytest.mark.parametrize(
    ("value", "problem"),
    [
        ([], None),
        ([{"name": "lm-studio", "base_url": "http://localhost:1234/v1", "local": True}], None),
        ([{"name": "or", "base_url": "https://openrouter.ai/api/v1"}], None),
        ("x", "list"),
        ([{"name": "Bad Name", "base_url": "http://x/v1"}], "lowercase"),
        ([{"name": "", "base_url": "http://x/v1"}], "lowercase"),
        ([{"name": "ollama", "base_url": "http://x/v1"}], "provider of YAAP"),
        ([{"name": "anthropic", "base_url": "http://x/v1"}], "provider of YAAP"),
        ([{"name": "a", "base_url": "http://x/v1"}, {"name": "a", "base_url": "http://y/v1"}], "twice"),
        ([{"name": "a", "base_url": "ftp://x"}], "http(s)"),
        ([{"name": "a", "base_url": "http://x /v1"}], "http(s)"),
        ([{"name": "a"}], "http(s)"),
        ([{"name": "a", "base_url": "http://user:pass@x/v1"}], "http(s)"),
        ([{"name": "a", "base_url": "http://user@x/v1"}], "http(s)"),
        ([{"name": "a\n", "base_url": "http://x/v1"}], "lowercase"),
        ([{"name": "a", "base_url": "http://x/v1", "local": "yes"}], "true or false"),
        ([{"name": "a", "base_url": "http://x/v1", "key": "secret"}], "object"),
        (["a"], "object"),
        (many(21), "at most"),
    ],
)
def test_the_endpoints_setting_is_validated(value, problem):
    result = check_endpoints(value, default_provider_registry())
    if problem is None:
        assert result is None
    else:
        assert result is not None and problem in result


def test_the_endpoints_setting_is_global_and_validated_through_the_api(client):
    clash = {"value": [{"name": "openai", "base_url": "http://x/v1"}]}
    assert client.put("/api/v1/settings/openai_endpoints", json=clash).status_code == 422
    own = {"value": [{"name": "lan", "base_url": "http://localhost:1/v1"}]}
    assert client.put("/api/v1/environments/1/settings/openai_endpoints", json=own).status_code in (400, 422)


def server(request: httpx.Request) -> httpx.Response:
    if request.url.host == "down":
        return httpx.Response(500)
    assert request.url.path.endswith("/models")
    return httpx.Response(200, json=LISTING if request.url.host == "openrouter.ai" else {"data": [{"id": "qwen"}]})


@pytest.fixture
def listing(monkeypatch):
    original = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **options: original(transport=httpx.MockTransport(server), **options))


def test_endpoints_become_providers_with_prefixed_models(listing):
    registry = default_provider_registry()
    endpoints = parse_endpoints(
        [
            {"name": "openrouter", "base_url": "https://openrouter.ai/api/v1/"},
            {"name": "lan", "base_url": "http://lan:1234/v1", "local": True},
        ]
    )
    refresh_endpoints(registry, endpoints)
    assert registry.find_model("openrouter/vendor/priced") is not None and registry.find_model("lan/qwen") is not None
    remote, lan = registry.find_provider("openrouter"), registry.find_provider("lan")
    assert remote is not None and lan is not None
    # Priced models are metered, unpriced ones are not; only the endpoint set as local is local; no key is needed.
    assert (remote.metered, remote.local, remote.credential_kind, remote.runs_agents) == (True, False, None, True)
    assert (lan.metered, lan.local, lan.credential_kind) == (False, True, None)
    assert registry.provider_label("lan") == "lan"
    # One endpoint is gone after the setting changes; the built-in providers stay.
    refresh_endpoints(registry, endpoints[:1])
    assert registry.find_provider("lan") is None and registry.find_model("lan/qwen") is None
    assert registry.find_provider("ollama") is not None and registry.find_provider("openai", "api_key") is not None


def test_an_endpoint_that_is_down_keeps_its_models_only_while_its_address_is_the_same(listing):
    registry = default_provider_registry()
    refresh_endpoints(registry, [Endpoint("lan", "http://lan:1234/v1")])
    assert registry.find_model("lan/qwen") is not None and registry.endpoints.errors == {}
    refresh_endpoints(registry, [Endpoint("lan", "http://down/v1")])
    assert registry.find_model("lan/qwen") is None
    assert "Could not read the models of lan" in registry.endpoints.errors["lan"]


@pytest.mark.parametrize(
    ("local", "only_local", "allowed"),
    [
        (True, True, True),
        (False, True, False),
        (True, False, True),
        (False, False, True),
    ],
)
def test_only_local_models_allows_an_endpoint_set_as_local(listing, local, only_local, allowed):
    registry = default_provider_registry()
    refresh_endpoints(registry, [Endpoint("lan", "http://lan:1234/v1", local)])
    spec = registry.find_provider("lan")
    model = registry.find_model("lan/qwen")
    assert spec is not None and model is not None
    target = PolicyTarget(model.id, model.display_name, connection_key(spec), spec.display_name, local=spec.local)
    policy = ModelPolicy(((PolicySource("environment", "Work"), LevelPolicy(local_only=only_local)),))
    assert (model_refusal(policy, target) is None) is allowed


def test_a_policy_can_name_an_endpoint_connection_and_model(listing):
    registry = default_provider_registry()
    assert check_policy({"connections": ["lan:endpoint"]}, registry) is not None
    refresh_endpoints(registry, [Endpoint("lan", "http://lan:1234/v1")])
    assert check_policy({"connections": ["lan:endpoint"], "models": ["lan/qwen"]}, registry) is None
    # The endpoint is its own vendor: its models belong to the provider named like it.
    assert {model.provider for model in registry.models if model.id.startswith("lan/")} == {"lan"}


def sse(*events: dict) -> bytes:
    return b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events) + b"data: [DONE]\n\n"


def provider_over(handler) -> ChatCompletionsProvider:
    provider = ChatCompletionsProvider("http://lan:1234/v1", "none", "lan", "lan/")
    provider._client = openai.AsyncOpenAI(
        base_url="http://lan:1234/v1",
        api_key="none",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return provider


class Box:
    definitions = [ToolDefinition("list_files", "List files", {"type": "object", "properties": {}}, True)]


def request_with(tool_box=None) -> AgentRequest:
    return AgentRequest(
        model="lan/vendor/qwen",
        effort="medium",
        system=[SystemPart("Be brief.")],
        messages=[HistoryMessage("user", [{"type": "text", "text": "Hi"}])],
        tool_box=tool_box,
    )


def test_the_adapter_calls_the_endpoint_with_the_model_id_without_its_prefix():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), json.loads(request.content)))
        chunk = {"choices": [{"index": 0, "delta": {"content": "Hello"}, "finish_reason": "stop"}]}
        usage = {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 2}}
        return httpx.Response(200, content=sse(chunk, usage), headers={"content-type": "text/event-stream"})

    async def collect():
        return [event async for event in provider_over(handler).stream(request_with())]

    events = asyncio.run(collect())
    assert seen[0][0] == "http://lan:1234/v1/chat/completions" and seen[0][1]["model"] == "vendor/qwen"
    assert "tools" not in seen[0][1]
    assert "".join(event.text for event in events if isinstance(event, TextDelta)) == "Hello"
    assert isinstance(events[-1], Completed) and events[-1].usage.input_tokens == 7


@pytest.mark.parametrize(
    ("status", "message", "names_model"),
    [
        (400, "this model does not support tools", True),
        (422, "Tool use is not supported", True),
        (400, "context length exceeded", False),
        (500, "tools are down", False),
    ],
)
def test_a_model_that_rejects_tools_gives_an_error_naming_the_model(status, message, names_model):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": message}})

    async def collect():
        return [event async for event in provider_over(handler).stream(request_with(Box()))]

    with pytest.raises(ProviderError) as caught:
        asyncio.run(collect())
    assert ("The model vendor/qwen on lan does not accept tools" in caught.value.message) is names_model
    assert message in caught.value.message
