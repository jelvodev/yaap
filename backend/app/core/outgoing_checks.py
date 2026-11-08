"""Outgoing checks: nothing leaves YAAP for a model with a known secret value or a listed pattern in it.

Before a model request leaves (every provider, the prompts handed to Claude Code and Codex included) and before an
outgoing A2A message is sent, the text is checked for

- the values of the secrets the request's environment and project know (the vault's entries the agent may use, as
  the placeholders resolve them), also URL-encoded and in base64, as tool output is masked;
- the patterns the model policy lists (regular expressions, checked when saved).

A hit blocks the request with a message that names the secret or the pattern, never the value it matched. Nothing is
redacted: a changed prompt would change the answer without anyone noticing. Placeholders (`{{secret:NAME}}`) are not
values, and are left out before the patterns are matched, so they always pass.

The functions that decide are pure; `outgoing_rules` reads what they need, and `checked_provider` is the one hook
where a model request leaves YAAP.
"""

import re
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.model_policy import ListedPattern, ModelPolicy
from app.core.model_policy_store import resolve_model_policy
from app.credentials.crypto import CredentialCipher
from app.providers.base import AgentEvent, AgentRequest, ContentBlock, ModelProvider, ProviderError
from app.secrets.service import PLACEHOLDER, masked_forms, resolve_secrets

# Content block fields that hold file data (base64), not text that was written or read.
BINARY_FIELDS = frozenset({"data", "media_type", "source"})


@dataclass(frozen=True)
class OutgoingRules:
    """What the outgoing check looks for: secret values by name and the policy's patterns."""

    secrets: dict[str, str] = field(default_factory=dict)
    patterns: tuple[ListedPattern, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.secrets and not self.patterns


def text_refusal(text: str, rules: OutgoingRules) -> str | None:
    """Why this text may not leave YAAP, or None. Placeholders pass."""
    if not text or rules.empty:
        return None
    for name, value in sorted(rules.secrets.items()):
        if any(form in text for form in masked_forms(value)):
            return (
                f"Blocked before it left YAAP: the text contains the value of the secret {name}. Remove it; a command "
                f"can use it as {{{{secret:{name}}}}} without the value."
            )
    without_placeholders = PLACEHOLDER.sub(" ", text)
    for listed in rules.patterns:
        if re.search(listed.pattern, without_placeholders):
            return (
                f"Blocked before it left YAAP by the outgoing check of {listed.source.label}: the text matches the "
                f"pattern `{listed.pattern}`. Remove that data and try again, or change the pattern in Settings › "
                "Models › Policy."
            )
    return None


def block_texts(blocks: list[ContentBlock]) -> Iterator[str]:
    """The text in neutral content blocks, also inside tool calls and results; file data is left out."""

    def walk(value: Any) -> Iterator[str]:
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for key, item in value.items():
                if key not in BINARY_FIELDS:
                    yield from walk(item)
        elif isinstance(value, list):
            for item in value:
                yield from walk(item)

    for block in blocks:
        yield from walk(block)


def request_texts(request: AgentRequest) -> Iterator[str]:
    for part in request.system:
        yield part.text
    for message in request.messages:
        yield from block_texts(message.content)


def request_refusal(request: AgentRequest, rules: OutgoingRules) -> str | None:
    """Why this model request may not leave YAAP, or None."""
    if rules.empty:
        return None
    for text in request_texts(request):
        refusal = text_refusal(text, rules)
        if refusal is not None:
            return refusal
    return None


def outgoing_rules(
    session: Session,
    cipher: CredentialCipher,
    environment_id: int,
    project_id: int | None,
    policy: ModelPolicy | None = None,
) -> OutgoingRules:
    """The secrets and patterns a request in this environment and project is checked for."""
    if policy is None:
        policy = resolve_model_policy(session, environment_id, project_id)
    return OutgoingRules(resolve_secrets(session, cipher, environment_id, project_id), policy.patterns)


class CheckedProvider:
    """A provider whose requests are checked before they leave; a blocked request ends as a provider error, which
    every caller already shows (the chat as the reply's error, delegation as the task's report)."""

    def __init__(self, provider: ModelProvider, rules: OutgoingRules) -> None:
        self.provider = provider
        self.rules = rules

    async def stream(self, request: AgentRequest) -> AsyncIterator[AgentEvent]:
        refusal = request_refusal(request, self.rules)
        if refusal is not None:
            raise ProviderError(refusal)
        async for event in self.provider.stream(request):
            yield event

    async def close(self) -> None:
        await self.provider.close()


def checked_provider(provider: ModelProvider, rules: OutgoingRules) -> ModelProvider:
    """The provider with the outgoing check in front of it: the hook where a model request leaves YAAP."""
    return provider if rules.empty else CheckedProvider(provider, rules)
