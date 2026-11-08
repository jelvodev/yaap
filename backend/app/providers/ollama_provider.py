"""Ollama adapter: local models through Ollama's OpenAI-compatible Chat Completions API, with YAAP's
own tool loop. No API key and no cost. The model list is read from the server (`/api/tags`), with each model's
context window from `/api/show`.

The server address is the global setting `ollama_url`; empty switches Ollama off.
"""

import json
import logging
import threading
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import openai

from app.providers.base import (
    AgentEvent,
    AgentRequest,
    CallRestarted,
    Completed,
    ContentBlock,
    HistoryMessage,
    ModelCallFinished,
    ProviderError,
    ReasoningDelta,
    TextDelta,
    TokenUsage,
)
from app.providers.catalog import ModelInfo, ModelPricing, PriceTier
from app.providers.tool_loop import PlannedCall, run_tool_calls, with_retries

logger = logging.getLogger(__name__)

PROVIDER = "ollama"
MODEL_PREFIX = "ollama/"
MESSAGES_BLOCK_TYPE = "chat_messages"
MAX_TOOL_TURNS = 60
DEFAULT_CONTEXT = 32_768
REFRESH_SECONDS = 60
FREE = ModelPricing(PriceTier(0, 0, 0, 0))
UNRUN_TOOL_CALL = "This tool call was not run because the reply ended first."


class OllamaState:
    """The server address and its models, shared by the registry and the provider."""

    def __init__(self) -> None:
        self.url = ""
        self.models: list[ModelInfo] = []
        self.checked_at = 0.0
        self.error: str | None = None
        self.lock = threading.Lock()


def model_info(name: str, context: int) -> ModelInfo:
    return ModelInfo(
        provider=PROVIDER,
        id=f"{MODEL_PREFIX}{name}",
        display_name=f"{name} (Ollama)",
        context_window=context,
        efforts=["medium"],
        default_effort="medium",
        pricing=FREE,
    )


def read_models(url: str, timeout: float = 3.0) -> list[ModelInfo]:
    """The models the Ollama server has pulled. Raises httpx.HTTPError when it cannot be reached."""
    base = url.rstrip("/")
    with httpx.Client(timeout=timeout) as client:
        tags = client.get(f"{base}/api/tags")
        tags.raise_for_status()
        models = []
        for item in tags.json().get("models", []):
            name = item.get("name") or item.get("model")
            if not name:
                continue
            context = DEFAULT_CONTEXT
            try:
                shown = client.post(f"{base}/api/show", json={"model": name})
                info = shown.json().get("model_info", {}) if shown.status_code == 200 else {}
                lengths = [value for key, value in info.items() if key.endswith(".context_length") and isinstance(value, int)]
                if lengths:
                    context = lengths[0]
            except (httpx.HTTPError, ValueError):
                pass
            models.append(model_info(name, context))
    return sorted(models, key=lambda model: model.id)


def refresh(state: OllamaState, url: str, force: bool = False) -> list[ModelInfo]:
    """The server's models, read again at most every REFRESH_SECONDS (or when the address changed)."""
    with state.lock:
        if not url:
            state.url, state.models, state.error = "", [], None
            return []
        if not force and url == state.url and time.monotonic() - state.checked_at < REFRESH_SECONDS:
            return state.models
        state.url = url
        state.checked_at = time.monotonic()
        try:
            state.models = read_models(url)
            state.error = None
        except (httpx.HTTPError, ValueError) as error:
            # Keep the models known so far: a conversation on a local model still opens while Ollama is down.
            state.error = f"Could not reach Ollama at {url}: {error}"
        return state.models


def text_of(blocks: list[ContentBlock]) -> str:
    return "\n\n".join(block["text"] for block in blocks if block.get("type") == "text" and block.get("text"))


def to_messages(message: HistoryMessage) -> list[dict[str, Any]]:
    raw = message.provider_raw
    if message.role == "assistant" and raw and raw[0].get("type") == MESSAGES_BLOCK_TYPE:
        return list(raw[0]["messages"])
    if message.role == "assistant":
        return [{"role": "assistant", "content": text_of(message.content) or "(empty)"}]
    parts: list[dict[str, Any]] = []
    for block in message.content:
        if block.get("type") == "text" and block.get("text"):
            parts.append({"type": "text", "text": block["text"]})
        elif block.get("type") == "image":
            parts.append({"type": "image_url", "image_url": {"url": f"data:{block['media_type']};base64,{block['data']}"}})
    if not parts:
        parts = [{"type": "text", "text": "(empty)"}]
    if all(part["type"] == "text" for part in parts):
        return [{"role": "user", "content": "\n\n".join(part["text"] for part in parts)}]
    return [{"role": "user", "content": parts}]


def tool_definitions(request: AgentRequest) -> list[dict[str, Any]]:
    assert request.tool_box is not None
    return [
        {"type": "function", "function": {"name": tool.name, "description": tool.description, "parameters": tool.input_schema}}
        for tool in request.tool_box.definitions
    ]


def parse_arguments(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text or "{}")
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


class OllamaProvider:
    def __init__(self, url: str) -> None:
        if not url:
            raise ProviderError("Ollama is not set up: add its address in Settings.")
        self._client = openai.AsyncOpenAI(base_url=f"{url.rstrip('/')}/v1", api_key="ollama")

    async def _one_call(self, request: AgentRequest, messages: list[dict[str, Any]]) -> AsyncIterator[Any]:
        """Stream one model call; the last item is (assistant message, usage, finish reason)."""
        params: dict[str, Any] = {
            "model": request.model.removeprefix(MODEL_PREFIX),
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": request.max_tokens,
        }
        if request.tool_box is not None and request.tool_box.definitions:
            params["tools"] = tool_definitions(request)
        text, calls, usage, finish = "", {}, TokenUsage(), "stop"
        try:
            stream = await self._client.chat.completions.create(**params)
            async for chunk in stream:
                if chunk.usage is not None:
                    usage = TokenUsage(input_tokens=chunk.usage.prompt_tokens or 0, output_tokens=chunk.usage.completion_tokens or 0)
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                reasoning = getattr(delta, "reasoning", None) or getattr(delta, "reasoning_content", None)
                if reasoning:
                    yield ReasoningDelta(reasoning)
                if delta.content:
                    text += delta.content
                    yield TextDelta(delta.content)
                for call in delta.tool_calls or []:
                    entry = calls.setdefault(call.index, {"id": call.id or f"call_{call.index}", "name": "", "arguments": ""})
                    if call.id:
                        entry["id"] = call.id
                    if call.function and call.function.name:
                        entry["name"] += call.function.name
                    if call.function and call.function.arguments:
                        entry["arguments"] += call.function.arguments
                if choice.finish_reason:
                    finish = choice.finish_reason
        except openai.APIConnectionError as error:
            raise ProviderError("Could not reach Ollama. Check that it runs and its address in Settings.", transient=True) from error
        except openai.APIStatusError as error:
            raise ProviderError(f"Ollama returned an error (HTTP {error.status_code}): {error.message}") from error
        message: dict[str, Any] = {"role": "assistant", "content": text}
        if calls:
            message["tool_calls"] = [
                {"id": entry["id"], "type": "function", "function": {"name": entry["name"], "arguments": entry["arguments"] or "{}"}}
                for _, entry in sorted(calls.items())
            ]
        yield (message, usage, finish)

    async def stream(self, request: AgentRequest) -> AsyncIterator[AgentEvent]:
        system = "\n\n".join(part.text for part in request.system)
        conversation = [{"role": "system", "content": system}] if system else []
        conversation += [item for message in request.messages for item in to_messages(message)]
        reply: list[dict[str, Any]] = []
        total = TokenUsage()
        last = TokenUsage()
        finish = "stop"
        limit = request.max_model_calls or MAX_TOOL_TURNS
        limit_reached = False
        for turn in range(limit if request.tool_box is not None else 1):
            result = None
            async for event in with_retries(lambda: self._one_call(request, [*conversation, *reply])):
                if isinstance(event, (TextDelta, ReasoningDelta, CallRestarted)):
                    yield event
                else:
                    result = event
            assert result is not None
            message, last, finish = result
            total = TokenUsage(input_tokens=total.input_tokens + last.input_tokens, output_tokens=total.output_tokens + last.output_tokens)
            yield ModelCallFinished(request.model, last)
            reply.append(message)
            calls = message.get("tool_calls") or []
            if not calls or request.tool_box is None:
                break
            limit_reached = turn == limit - 1
            planned = [PlannedCall(call["id"], call["function"]["name"], parse_arguments(call["function"]["arguments"])) for call in calls]
            outcomes: dict[str, Any] = {}
            async for event in run_tool_calls(request.tool_box, planned, outcomes):
                yield event
            reply += [{"role": "tool", "tool_call_id": call.id, "content": outcomes[call.id].text or "(no output)"} for call in planned]
        answered = {item.get("tool_call_id") for item in reply if item.get("role") == "tool"}
        unrun = [
            {"role": "tool", "tool_call_id": call["id"], "content": UNRUN_TOOL_CALL}
            for item in reply
            for call in item.get("tool_calls") or []
            if call["id"] not in answered
        ]
        reply += unrun
        stop_reason = "max_tokens" if finish == "length" else "end_turn"
        if (unrun or limit_reached) and stop_reason == "end_turn":
            stop_reason = "max_turns"
        yield Completed(
            stop_reason=stop_reason,
            served_by_model=request.model,
            usage=total,
            provider_raw=[{"type": MESSAGES_BLOCK_TYPE, "messages": reply}],
            context_tokens=last.input_tokens + last.output_tokens,
        )

    async def close(self) -> None:
        await self._client.close()
