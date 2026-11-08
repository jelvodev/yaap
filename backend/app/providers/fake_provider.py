"""Provider that replays scripted responses, for tests that must not call a real model."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.providers.base import (
    AgentEvent,
    AgentRequest,
    Completed,
    ModelCallFinished,
    ProviderError,
    ReasoningDelta,
    TextDelta,
    TokenUsage,
    ToolCallFinished,
    ToolCallStarted,
)


@dataclass(frozen=True)
class ScriptedResponse:
    text: str = ""
    reasoning: str = ""
    stop_reason: str = "end_turn"
    refusal_category: str | None = None
    error: str | None = None
    usage: TokenUsage = TokenUsage(input_tokens=10, output_tokens=5)
    # Seconds before each word, for tests of replies that take a while.
    delay: float = 0.0
    # Tools to call through the request's tool box before answering: (name, arguments).
    tool_calls: tuple[tuple[str, dict], ...] = ()
    # The reply as several model calls with these usages: the first before the tool calls, the others after them.
    # Without them the reply reports no model calls, as a provider that only says Completed.
    call_usages: tuple[TokenUsage, ...] = ()


def total_usage(usages: tuple[TokenUsage, ...]) -> TokenUsage:
    return TokenUsage(
        input_tokens=sum(usage.input_tokens for usage in usages),
        output_tokens=sum(usage.output_tokens for usage in usages),
        cache_read_tokens=sum(usage.cache_read_tokens for usage in usages),
        cache_write_tokens=sum(usage.cache_write_tokens for usage in usages),
        cache_write_1h_tokens=sum(usage.cache_write_1h_tokens for usage in usages),
    )


class FakeProvider:
    def __init__(
        self,
        model_id: str,
        responses: list[ScriptedResponse],
        requests: list[AgentRequest],
        billing: str = "metered",
    ) -> None:
        self._model_id = model_id
        self._responses = responses
        self._requests = requests
        self._billing = billing

    async def stream(self, request: AgentRequest) -> AsyncIterator[AgentEvent]:
        self._requests.append(request)
        response = self._responses.pop(0)
        if response.reasoning:
            yield ReasoningDelta(response.reasoning)
        if response.call_usages:
            yield ModelCallFinished(request.model, response.call_usages[0])
        for index, (name, arguments) in enumerate(response.tool_calls):
            if request.tool_box is None:
                raise ProviderError("This scripted response needs tools, but the request has none.")
            yield ToolCallStarted(f"fake-call-{index}", name, arguments)
            result = await request.tool_box.call(name, arguments)
            yield ToolCallFinished(f"fake-call-{index}", result.text, result.is_error)
        for word in response.text.split(" "):
            if word:
                if response.delay:
                    await asyncio.sleep(response.delay)
                yield TextDelta(word + " ")
        for usage in response.call_usages[1:]:
            yield ModelCallFinished(request.model, usage)
        if response.error:
            raise ProviderError(response.error)
        raw = []
        if response.reasoning:
            raw.append({"type": "thinking", "thinking": response.reasoning, "signature": "fake-signature"})
        if response.text:
            raw.append({"type": "text", "text": response.text})
        yield Completed(
            stop_reason=response.stop_reason,
            served_by_model=request.model,
            usage=total_usage(response.call_usages) if response.call_usages else response.usage,
            provider_raw=raw,
            refusal_category=response.refusal_category,
            billing=self._billing,
        )

    async def close(self) -> None:
        return None
