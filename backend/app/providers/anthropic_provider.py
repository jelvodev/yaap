"""Anthropic adapter: maps the neutral request to the Messages API and the stream back to neutral events."""

import logging
from collections.abc import AsyncIterator
from typing import Any

import anthropic
import httpx2

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
    ToolBox,
    ToolResult,
)
from app.providers.messages_content import to_messages_content
from app.providers.tool_loop import PlannedCall, run_tool_calls, with_retries

SERVER_SIDE_FALLBACK_BETA = "server-side-fallback-2026-07-01"
# A reply that used tools is stored as all its turns: assistant turns with their tool calls and the tool results.
TURNS_BLOCK_TYPE = "anthropic_turns"
# Raw blocks this adapter can replay. A reply from another connection (Claude Code, another vendor) is sent as text.
REPLAYABLE_TYPES = frozenset({"text", "thinking", "redacted_thinking", "tool_use", TURNS_BLOCK_TYPE})
MAX_TOOL_TURNS = 60
UNRUN_TOOL_CALL = "This tool call was not run because the reply ended first."

logger = logging.getLogger(__name__)


def to_anthropic_messages(message: HistoryMessage) -> list[dict[str, Any]]:
    raw = message.provider_raw
    if message.role == "assistant" and raw and all(block.get("type") in REPLAYABLE_TYPES for block in raw):
        # Earlier assistant turns are replayed exactly as the API returned them, thinking blocks included.
        if raw[0].get("type") == TURNS_BLOCK_TYPE:
            return list(raw[0]["messages"])
        return [{"role": "assistant", "content": raw}]
    return [{"role": message.role, "content": to_messages_content(message.content) or [{"type": "text", "text": "(empty)"}]}]


def to_anthropic_conversation(messages: list[HistoryMessage]) -> list[dict[str, Any]]:
    """The history as API messages. Turns of the same role in a row are joined, as the API expects alternation."""
    result: list[dict[str, Any]] = []
    for message in messages:
        for converted in to_anthropic_messages(message):
            if result and result[-1]["role"] == converted["role"]:
                result[-1] = {"role": converted["role"], "content": [*result[-1]["content"], *converted["content"]]}
            else:
                result.append(converted)
    return result


def tool_definitions(tool_box: ToolBox) -> list[dict[str, Any]]:
    return [
        {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}
        for tool in tool_box.definitions
    ]


def tool_result_block(tool_use_id: str, text: str, is_error: bool, images: tuple[tuple[str, str], ...] = ()) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": text or "(no output)"}]
    content += [
        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}
        for media_type, data in images
    ]
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": content, "is_error": is_error}


def stored_tool_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tool results as stored for replay: images are left out, so the database does not fill with screenshots."""
    stored = []
    for result in results:
        content = [block for block in result["content"] if block["type"] == "text"]
        if len(content) < len(result["content"]):
            content.append({"type": "text", "text": "[The tool also returned an image, shown to the model at the time.]"})
        stored.append({**result, "content": content})
    return stored


def unanswered_tool_calls(assistant_content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        tool_result_block(block["id"], UNRUN_TOOL_CALL, True)
        for block in assistant_content
        if block.get("type") == "tool_use"
    ]


def add_usage(total: TokenUsage, usage: Any) -> TokenUsage:
    # usage.cache_creation splits the cache writes by how long they are kept; an answer may lack it.
    by_ttl = getattr(usage, "cache_creation", None)
    hour_writes = (getattr(by_ttl, "ephemeral_1h_input_tokens", 0) or 0) if by_ttl is not None else 0
    return TokenUsage(
        input_tokens=total.input_tokens + (usage.input_tokens or 0),
        output_tokens=total.output_tokens + (usage.output_tokens or 0),
        cache_read_tokens=total.cache_read_tokens + (usage.cache_read_input_tokens or 0),
        cache_write_tokens=total.cache_write_tokens + (usage.cache_creation_input_tokens or 0),
        cache_write_1h_tokens=total.cache_write_1h_tokens + hour_writes,
    )


def to_anthropic_system(request: AgentRequest) -> list[dict[str, Any]]:
    """The system parts; the cacheable ones get a breakpoint, kept for an hour when the request asks for it. The
    automatic breakpoint on the conversation stays at five minutes: an hour entry must come before five-minute ones,
    which it does, as the system comes before the messages."""
    control: dict[str, Any] = {"type": "ephemeral"}
    if request.cache_ttl == "1h":
        control["ttl"] = "1h"
    blocks: list[dict[str, Any]] = []
    for part in request.system:
        block: dict[str, Any] = {"type": "text", "text": part.text}
        if part.cacheable:
            block["cache_control"] = dict(control)
        blocks.append(block)
    return blocks


def vendor_details(error: anthropic.APIStatusError) -> str:
    """Anthropic's own error text and request id, for the user and for a support request."""
    details = []
    body = error.body
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        vendor_message = str(body["error"].get("message", "")).strip()
        if vendor_message and vendor_message.lower() != "error":
            details.append(f"Anthropic says: {vendor_message}")
    if error.request_id:
        details.append(f"Request id: {error.request_id}")
    return (" " + ". ".join(details) + ".") if details else ""


def to_provider_error(error: anthropic.APIError) -> ProviderError:
    if isinstance(error, anthropic.APIConnectionError):
        return ProviderError("Could not reach Anthropic. Check the network connection.", transient=True)
    if not isinstance(error, anthropic.APIStatusError):
        return ProviderError("Unexpected error while talking to Anthropic.")

    logger.warning(
        "Anthropic returned HTTP %s (%s), request id %s", error.status_code, error.type, error.request_id
    )
    details = vendor_details(error)
    if isinstance(error, anthropic.AuthenticationError):
        return ProviderError(f"Anthropic rejected the API key. Check it in Settings.{details}")
    if isinstance(error, anthropic.PermissionDeniedError):
        return ProviderError(f"The API key has no permission for this request.{details}")
    if isinstance(error, anthropic.RateLimitError):
        return ProviderError(
            "Anthropic refused the request with a rate limit error (HTTP 429). If this happens on every "
            "message, check the credit balance and the spend and rate limits of the key's workspace in the "
            f"Anthropic Console.{details}"
        )
    if isinstance(error, anthropic.BadRequestError):
        return ProviderError(f"Anthropic rejected the request.{details}")
    return ProviderError(
        f"Anthropic returned an error (HTTP {error.status_code}). Try again later.{details}",
        transient=error.status_code >= 500,
    )


class AnthropicProvider:
    def __init__(self, api_key: str, server_side_fallback_models: frozenset[str]) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._server_side_fallback_models = server_side_fallback_models

    def _build_params(self, request: AgentRequest, messages: list[dict[str, Any]]) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "system": to_anthropic_system(request),
            "messages": messages,
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": {"effort": request.effort},
            # Automatic caching of the conversation prefix.
            "cache_control": {"type": "ephemeral"},
        }
        if request.tool_box is not None:
            params["tools"] = tool_definitions(request.tool_box)
        return params

    def _open_stream(self, request: AgentRequest, messages: list[dict[str, Any]]):  # type: ignore[no-untyped-def]
        params = self._build_params(request, messages)
        if request.model in self._server_side_fallback_models:
            # On a policy refusal the API re-runs the request on a recommended fallback model.
            return self._client.beta.messages.stream(
                **params, betas=[SERVER_SIDE_FALLBACK_BETA], fallbacks="default"
            )
        return self._client.messages.stream(**params)

    async def _one_call(self, request: AgentRequest, messages: list[dict[str, Any]]) -> AsyncIterator[AgentEvent | Any]:
        """Stream one model call; the last item is the final message."""
        try:
            async with self._open_stream(request, messages) as stream:
                async for event in stream:
                    if event.type != "content_block_delta":
                        continue
                    if event.delta.type == "text_delta":
                        yield TextDelta(event.delta.text)
                    elif event.delta.type == "thinking_delta":
                        yield ReasoningDelta(event.delta.thinking)
                yield await stream.get_final_message()
        except anthropic.APIError as error:
            raise to_provider_error(error) from error
        except httpx2.HTTPError as error:
            raise ProviderError("The connection to Anthropic broke off.", transient=True) from error

    async def stream(self, request: AgentRequest) -> AsyncIterator[AgentEvent]:
        """One reply. With a tool box this is a tool loop: call the model, run the tools it asks for, repeat."""
        conversation = to_anthropic_conversation(request.messages)
        turns: list[dict[str, Any]] = []
        usage = TokenUsage()
        final: Any = None
        for _ in range((request.max_model_calls or MAX_TOOL_TURNS) if request.tool_box is not None else 1):
            messages = [*conversation, *turns]
            async for item in with_retries(lambda: self._one_call(request, messages)):
                if isinstance(item, (TextDelta, ReasoningDelta, CallRestarted)):
                    yield item
                else:
                    final = item
            call_usage = add_usage(TokenUsage(), final.usage)
            usage = add_usage(usage, final.usage)
            yield ModelCallFinished(final.model, call_usage)
            raw: list[ContentBlock] = [block.model_dump(mode="json", exclude_none=True) for block in final.content]
            turns.append({"role": "assistant", "content": raw})
            if final.stop_reason != "tool_use" or request.tool_box is None:
                break
            calls = [
                PlannedCall(block["id"], block["name"], block.get("input") if isinstance(block.get("input"), dict) else {})
                for block in raw
                if block.get("type") == "tool_use"
            ]
            outcomes: dict[str, ToolResult] = {}
            async for event in run_tool_calls(request.tool_box, calls, outcomes):
                yield event
            results = [
                tool_result_block(call.id, outcomes[call.id].text, outcomes[call.id].is_error, outcomes[call.id].images)
                for call in calls
            ]
            turns.append({"role": "user", "content": results})
            # Later calls see the images; the stored turns keep only the text.
        if final is None:
            raise ProviderError("Anthropic returned no reply.")

        stop_reason = final.stop_reason
        if stop_reason == "tool_use":
            # The turn limit was reached while the model still wanted tools.
            stop_reason = "max_turns"
        # Tool calls without a result would make the stored history invalid for the next request.
        if unrun := unanswered_tool_calls(turns[-1]["content"]):
            turns.append({"role": "user", "content": unrun})
        stored_turns = [
            {"role": turn["role"], "content": stored_tool_results(turn["content"]) if turn["role"] == "user" else turn["content"]}
            for turn in turns
        ]
        provider_raw = turns[0]["content"] if len(turns) == 1 else [{"type": TURNS_BLOCK_TYPE, "messages": stored_turns}]
        refusal_category = None
        if final.stop_reason == "refusal" and getattr(final, "stop_details", None) is not None:
            refusal_category = final.stop_details.category
        last = final.usage
        yield Completed(
            stop_reason=stop_reason,
            served_by_model=final.model,
            usage=usage,
            provider_raw=provider_raw,
            refusal_category=refusal_category,
            context_tokens=(last.input_tokens or 0)
            + (last.cache_read_input_tokens or 0)
            + (last.cache_creation_input_tokens or 0)
            + (last.output_tokens or 0)
            if len(turns) > 1
            else None,
        )

    async def close(self) -> None:
        await self._client.close()
