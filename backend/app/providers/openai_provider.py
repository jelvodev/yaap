"""OpenAI adapter: the Responses API, with YAAP's own tool loop.

Requests are stateless (store=False): every request carries the whole conversation. Earlier replies made here are
replayed as the exact output items the API returned, reasoning items with their encrypted content included, so the
model keeps its reasoning across turns and tool calls.
"""

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx2
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
    ToolBox,
    ToolResult,
)
from app.providers.tool_loop import PlannedCall, run_tool_calls, with_retries

logger = logging.getLogger(__name__)

# A reply is stored as the output items of all its calls plus the tool results, under one block of this type.
ITEMS_BLOCK_TYPE = "openai_items"
MAX_TOOL_TURNS = 60
UNRUN_TOOL_CALL = "This tool call was not run because the reply ended first."
# Fields of output items that only describe the response they came from; they are left out when replaying.
RESPONSE_ONLY_FIELDS = ("status",)


def to_input_content(blocks: list[ContentBlock]) -> list[dict[str, Any]]:
    """Neutral user content as Responses input parts: text, images and PDFs."""
    parts: list[dict[str, Any]] = []
    for block in blocks:
        kind = block.get("type")
        if kind == "text" and block.get("text"):
            parts.append({"type": "input_text", "text": block["text"]})
        elif kind == "image":
            parts.append({"type": "input_image", "image_url": f"data:{block['media_type']};base64,{block['data']}"})
        elif kind == "document":
            parts.append(
                {
                    "type": "input_file",
                    "filename": block.get("title") or "document.pdf",
                    "file_data": f"data:{block['media_type']};base64,{block['data']}",
                }
            )
    return parts


def assistant_text(message: HistoryMessage) -> str:
    return "\n\n".join(block["text"] for block in message.content if block.get("type") == "text" and block.get("text"))


def to_input_items(message: HistoryMessage) -> list[dict[str, Any]]:
    raw = message.provider_raw
    if message.role == "assistant" and raw and raw[0].get("type") == ITEMS_BLOCK_TYPE:
        return list(raw[0]["items"])
    if message.role == "assistant":
        # A reply from another connection or vendor goes along as its text.
        return [{"role": "assistant", "content": assistant_text(message) or "(empty)"}]
    return [{"role": "user", "content": to_input_content(message.content) or [{"type": "input_text", "text": "(empty)"}]}]


def replayable(item: dict[str, Any]) -> dict[str, Any]:
    cleaned = {key: value for key, value in item.items() if key not in RESPONSE_ONLY_FIELDS}
    if cleaned.get("type") != "reasoning":
        # Without store, the API knows no item ids; reasoning items keep theirs with their encrypted content.
        cleaned.pop("id", None)
    return cleaned


def tool_output(call_id: str, text: str, images: tuple[tuple[str, str], ...] = ()) -> dict[str, Any]:
    if not images:
        return {"type": "function_call_output", "call_id": call_id, "output": text or "(no output)"}
    parts: list[dict[str, Any]] = [{"type": "input_text", "text": text or "(no output)"}]
    parts += [{"type": "input_image", "image_url": f"data:{media_type};base64,{data}"} for media_type, data in images]
    return {"type": "function_call_output", "call_id": call_id, "output": parts}


def stored_item(item: dict[str, Any]) -> dict[str, Any]:
    """Tool outputs are stored without their images, so the database does not fill with screenshots."""
    if item.get("type") != "function_call_output" or isinstance(item.get("output"), str):
        return item
    text = "\n".join(part["text"] for part in item["output"] if part.get("type") == "input_text")
    return {**item, "output": text + "\n[The tool also returned an image, shown to the model at the time.]"}


def tool_definitions(tool_box: ToolBox) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
            # YAAP's schemas have optional arguments, which strict mode does not allow.
            "strict": False,
        }
        for tool in tool_box.definitions
    ]


def usage_of(response: Any) -> TokenUsage:
    usage = response.usage
    if usage is None:
        return TokenUsage()
    cached = getattr(usage.input_tokens_details, "cached_tokens", 0) or 0
    return TokenUsage(
        input_tokens=max((usage.input_tokens or 0) - cached, 0),
        output_tokens=usage.output_tokens or 0,
        cache_read_tokens=cached,
    )


def add_usage(total: TokenUsage, more: TokenUsage) -> TokenUsage:
    return TokenUsage(
        input_tokens=total.input_tokens + more.input_tokens,
        output_tokens=total.output_tokens + more.output_tokens,
        cache_read_tokens=total.cache_read_tokens + more.cache_read_tokens,
        cache_write_tokens=total.cache_write_tokens + more.cache_write_tokens,
    )


def stop_reason_of(response: Any, items: list[dict[str, Any]]) -> str:
    if response.status == "incomplete":
        reason = getattr(response.incomplete_details, "reason", None)
        return "refusal" if reason == "content_filter" else "max_tokens"
    for item in items:
        if item.get("type") == "message" and any(part.get("type") == "refusal" for part in item.get("content", [])):
            return "refusal"
    return "end_turn"


def to_provider_error(error: openai.APIError) -> ProviderError:
    if isinstance(error, openai.APIConnectionError):
        return ProviderError("Could not reach OpenAI. Check the network connection.")
    if not isinstance(error, openai.APIStatusError):
        return ProviderError("Unexpected error while talking to OpenAI.")
    logger.warning("OpenAI returned HTTP %s, request id %s", error.status_code, error.request_id)
    details = []
    body = error.body
    if isinstance(body, dict):
        error_body = body.get("error") if isinstance(body.get("error"), dict) else body
        vendor_message = str(error_body.get("message") or "").strip()
        if vendor_message:
            details.append(f"OpenAI says: {vendor_message}")
    if error.request_id:
        details.append(f"Request id: {error.request_id}")
    suffix = (" " + ". ".join(details) + ".") if details else ""
    if isinstance(error, openai.AuthenticationError):
        return ProviderError(f"OpenAI rejected the API key. Check it in Settings.{suffix}")
    if isinstance(error, openai.PermissionDeniedError):
        return ProviderError(f"The API key has no permission for this request.{suffix}")
    if isinstance(error, openai.RateLimitError):
        return ProviderError(
            "OpenAI refused the request with a rate limit error (HTTP 429). If this happens on every message, "
            f"check the billing and limits of the key's project on the OpenAI platform.{suffix}"
        )
    if isinstance(error, openai.BadRequestError):
        return ProviderError(f"OpenAI rejected the request.{suffix}")
    return ProviderError(
        f"OpenAI returned an error (HTTP {error.status_code}). Try again later.{suffix}",
        transient=error.status_code >= 500,
    )


def parse_arguments(call: dict[str, Any]) -> dict[str, Any] | None:
    try:
        arguments = json.loads(call.get("arguments") or "{}")
    except json.JSONDecodeError:
        return None
    return arguments if isinstance(arguments, dict) else None


class OpenAIProvider:
    def __init__(self, api_key: str) -> None:
        self._client = openai.AsyncOpenAI(api_key=api_key)

    def _params(self, request: AgentRequest, items: list[dict[str, Any]]) -> dict[str, Any]:
        reasoning: dict[str, Any] = {"effort": request.effort}
        if request.effort != "none":
            reasoning["summary"] = "auto"
        params: dict[str, Any] = {
            "model": request.model,
            "instructions": "\n\n".join(part.text for part in request.system),
            "input": items,
            "max_output_tokens": request.max_tokens,
            "reasoning": reasoning,
            "store": False,
            "include": ["reasoning.encrypted_content"],
            "stream": True,
        }
        if request.tool_box is not None:
            params["tools"] = tool_definitions(request.tool_box)
        return params

    async def _one_call(self, request: AgentRequest, items: list[dict[str, Any]]) -> AsyncIterator[AgentEvent | Any]:
        """Stream one model call; the last item is the final response."""
        final = None
        try:
            stream = await self._client.responses.create(**self._params(request, items))
            async for event in stream:
                if event.type == "response.output_text.delta":
                    yield TextDelta(event.delta)
                elif event.type == "response.reasoning_summary_text.delta":
                    yield ReasoningDelta(event.delta)
                elif event.type == "response.reasoning_summary_part.added" and event.summary_index > 0:
                    yield ReasoningDelta("\n\n")
                elif event.type in ("response.completed", "response.incomplete"):
                    final = event.response
                elif event.type == "response.failed":
                    message = getattr(event.response.error, "message", None) or "the response failed"
                    raise ProviderError(f"OpenAI could not finish the reply: {message}.")
                elif event.type == "error":
                    raise ProviderError(f"OpenAI returned an error: {event.message}")
        except openai.APIError as error:
            raise to_provider_error(error) from error
        except httpx2.HTTPError as error:
            raise ProviderError("The connection to OpenAI broke off.", transient=True) from error
        if final is None:
            raise ProviderError("OpenAI ended the reply without a result.")
        yield final

    async def stream(self, request: AgentRequest) -> AsyncIterator[AgentEvent]:
        conversation = [item for message in request.messages for item in to_input_items(message)]
        reply: list[dict[str, Any]] = []
        usage = TokenUsage()
        final: Any = None
        last_usage = TokenUsage()
        limit_reached = False
        limit = request.max_model_calls or MAX_TOOL_TURNS
        for turn in range(limit if request.tool_box is not None else 1):
            items = [*conversation, *reply]
            async for event in with_retries(lambda: self._one_call(request, items)):
                if isinstance(event, (TextDelta, ReasoningDelta, CallRestarted)):
                    yield event
                else:
                    final = event
            last_usage = usage_of(final)
            usage = add_usage(usage, last_usage)
            yield ModelCallFinished(final.model, last_usage)
            output = [replayable(item.model_dump(mode="json", exclude_none=True)) for item in final.output]
            reply += output
            calls = [item for item in output if item.get("type") == "function_call"]
            if not calls or request.tool_box is None or final.status != "completed":
                break
            limit_reached = turn == limit - 1
            planned = [PlannedCall(call["call_id"], call["name"], parse_arguments(call)) for call in calls]
            outcomes: dict[str, ToolResult] = {}
            async for event in run_tool_calls(request.tool_box, planned, outcomes):
                yield event
            reply += [tool_output(call.id, outcomes[call.id].text, outcomes[call.id].images) for call in planned]

        answered = {item["call_id"] for item in reply if item.get("type") == "function_call_output"}
        unrun = [
            tool_output(item["call_id"], UNRUN_TOOL_CALL)
            for item in reply
            if item.get("type") == "function_call" and item["call_id"] not in answered
        ]
        stop_reason = stop_reason_of(final, reply)
        if (unrun or limit_reached) and stop_reason == "end_turn":
            stop_reason = "max_turns"
        reply += unrun
        yield Completed(
            stop_reason=stop_reason,
            served_by_model=final.model,
            usage=usage,
            provider_raw=[{"type": ITEMS_BLOCK_TYPE, "items": [stored_item(item) for item in reply]}],
            context_tokens=last_usage.input_tokens + last_usage.cache_read_tokens + last_usage.output_tokens,
        )

    async def close(self) -> None:
        await self._client.close()
