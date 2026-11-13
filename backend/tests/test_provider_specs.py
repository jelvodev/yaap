"""What every registered provider spec declares: self-compaction, default models and session format, and how a
session line is recognised."""

import json
from typing import Any

import pytest

from app.core.context_control import compacts_itself
from app.imports.sessions import detect, read_lines
from app.providers.claude_code.sessions import CLAUDE_CODE_SESSIONS
from app.providers.codex.sessions import CODEX_SESSIONS
from app.providers.registry import ProviderRegistry, ProviderSpec, default_provider_registry, session_format

ROLES = ("main", "fast", "worker", "utility")

# (provider, connection): self-compaction, whether default models are declared, and the session tool.
EXPECTED = [
    ("anthropic", "api_key", False, True, None),
    ("anthropic", "claude_code", True, True, "claude_code"),
    ("openai", "api_key", False, True, None),
    ("openai", "codex", True, True, "codex"),
    ("ollama", "local", False, False, None),
]


@pytest.fixture(scope="module")
def registry() -> ProviderRegistry:
    return default_provider_registry()


def spec_of(registry: ProviderRegistry, provider: str, connection: str) -> ProviderSpec:
    spec = registry.find_provider(provider, connection)
    assert spec is not None
    return spec


def test_every_registered_spec_is_covered_by_the_table(registry: ProviderRegistry) -> None:
    assert {(spec.name, spec.connection) for spec in registry.specs} == {(row[0], row[1]) for row in EXPECTED}


@pytest.mark.parametrize(("provider", "connection", "compacts", "has_models", "tool"), EXPECTED)
def test_spec_declares_compaction_default_models_and_session_format(
    registry: ProviderRegistry, provider: str, connection: str, compacts: bool, has_models: bool, tool: str | None
) -> None:
    spec = spec_of(registry, provider, connection)
    assert spec.compacts_itself is compacts
    if has_models:
        assert set(spec.default_models) == set(ROLES)
        assert all(isinstance(spec.default_models[role], str) and spec.default_models[role] for role in ROLES)
    else:
        assert not spec.default_models
    assert (spec.session_format.tool if spec.session_format else None) == tool


@pytest.mark.parametrize(("provider", "connection", "compacts", "_models", "_tool"), EXPECTED)
def test_context_control_follows_the_spec_of_the_connection(
    registry: ProviderRegistry, provider: str, connection: str, compacts: bool, _models: bool, _tool: str | None
) -> None:
    assert compacts_itself(registry, provider, connection) is compacts


@pytest.mark.parametrize(
    ("provider", "connection", "expected"),
    [
        ("anthropic", "claude_code", True),
        ("anthropic", "api_key", False),
        ("openai", "codex", True),
        ("openai", "api_key", False),
        ("unknown", "api_key", False),
        ("anthropic", "nonexistent", False),
    ],
)
def test_compaction_is_decided_per_connection_not_per_vendor(
    registry: ProviderRegistry, provider: str, connection: str, expected: bool
) -> None:
    assert compacts_itself(registry, provider, connection) is expected


@pytest.mark.parametrize(("tool", "label"), [("claude_code", "Claude Code"), ("codex", "Codex")])
def test_static_session_formats_are_found_by_tool_id(tool: str, label: str) -> None:
    found = session_format(tool)
    assert found is not None
    assert found.label == label


def test_unknown_tool_has_no_session_format() -> None:
    assert session_format("vim") is None


CLAUDE_LINE = {"type": "user", "message": {"role": "user", "content": "hi"}, "sessionId": "s1"}
CODEX_LINE = {"type": "session_meta", "payload": {"id": "s1"}}
UNRELATED_LINE = {"hello": "world"}


@pytest.mark.parametrize(
    ("line", "claude", "codex"),
    [
        (CLAUDE_LINE, True, False),
        ({"sessionId": "s1"}, True, False),
        (CODEX_LINE, False, True),
        ({"type": "message", "role": "assistant"}, False, True),
        ({"instructions": "x", "id": "1"}, False, True),
        (UNRELATED_LINE, False, False),
        ({"type": "user"}, False, False),
    ],
)
def test_a_session_line_belongs_to_exactly_its_own_format(line: dict[str, Any], claude: bool, codex: bool) -> None:
    assert CLAUDE_CODE_SESSIONS.matches(line) is claude
    assert CODEX_SESSIONS.matches(line) is codex


@pytest.mark.parametrize(
    ("lines", "tool"),
    [([CLAUDE_LINE], "claude_code"), ([UNRELATED_LINE, CODEX_LINE], "codex")],
)
def test_detect_picks_the_format_of_the_first_recognised_line(lines: list[dict[str, Any]], tool: str) -> None:
    assert detect(lines).tool == tool


def test_a_broken_line_is_skipped_when_reading_a_session() -> None:
    data = "\n".join(["{not json", json.dumps(CODEX_LINE), "", "[1, 2]"]).encode()
    assert read_lines(data) == [CODEX_LINE]
