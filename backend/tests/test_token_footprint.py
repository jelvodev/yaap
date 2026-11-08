"""The token footprint (app/evals/footprint.py, footprint_run.py): every part of every situation, and the same numbers
twice."""

import pytest

from app.evals.footprint import (
    Footprint,
    Part,
    Situation,
    estimate_tokens,
    message_parts,
    render_table,
    to_json,
    tool_group,
)
from app.evals.footprint_run import measure_footprint
from app.providers.base import AgentRequest, HistoryMessage


@pytest.mark.parametrize(
    ("name", "group"),
    [
        ("read_file", "yaap"),
        ("finish_goal", "yaap"),
        ("browser__browser_click", "browser"),
        ("tracker__get_issue", "mcp:tracker"),
        ("my-server__do__thing", "mcp:my-server"),
    ],
)
def test_tools_are_grouped_by_where_they_come_from(name: str, group: str) -> None:
    assert tool_group(name) == group


@pytest.mark.parametrize(("chars", "tokens"), [(0, 0), (1, 1), (4, 1), (5, 2), (4000, 1000)])
def test_tokens_are_estimated_as_a_quarter_of_the_characters(chars: int, tokens: int) -> None:
    assert estimate_tokens(chars) == tokens


def test_the_turn_context_is_measured_with_a_fixed_date() -> None:
    def request(moment: str) -> AgentRequest:
        context = f"[Context for this message]\nDate and time: {moment} (UTC)."
        content = [{"type": "text", "text": "Hello"}, {"type": "text", "text": context}]
        return AgentRequest("m", "medium", [], [HistoryMessage("user", content)])

    monday = message_parts(request("Monday 9 November 2026, 08:05"))
    wednesday = message_parts(request("Wednesday 11 November 2026, 17:45"))
    assert monday == wednesday
    assert monday[0] == Part("message", "message:user", 5)


def test_the_table_and_json_hold_every_part() -> None:
    situation = Situation("demo", "a demo", (Part("system", "system:base", 400), Part("tools", "tools:yaap", 80, 2)))
    footprint = Footprint((situation,), ("a note",))
    data = to_json(footprint)
    assert data["situations"][0]["totals"]["all"] == {"chars": 480, "tokens": 120}
    assert "system:base" in render_table(footprint) and "tools:yaap" in render_table(footprint)


@pytest.fixture(scope="module")
def measured() -> tuple[Footprint, Footprint]:
    return measure_footprint(), measure_footprint()


def test_the_footprint_is_the_same_twice(measured: tuple[Footprint, Footprint]) -> None:
    first, second = measured
    assert to_json(first) == to_json(second)


def test_every_situation_has_its_parts(measured: tuple[Footprint, Footprint]) -> None:
    situations = {situation.name: situation for situation in measured[0].situations}
    assert list(situations) == ["plain_chat", "project", "goal_turn"]
    names = {name: {part.name: part for part in situation.parts} for name, situation in situations.items()}
    for parts in names.values():
        assert {"system:base", "tools:yaap", "message:user", "message:turn_context"} <= set(parts)
        # Since 46.4 and 46.5 the browser tools wait for open_browser and the tracker's 22 tools for find_tools.
        assert "tools:mcp:tracker" not in parts and "tools:browser" not in parts
    # The project's own system part only in a project.
    assert "system:project" not in names["plain_chat"]
    for name in ("project", "goal_turn"):
        assert "system:project" in names[name]
    project = situations["project"]
    results = [part.name for part in project.parts if part.group == "tool_result"]
    assert results == ["result:1:list_files", "result:2:search", "result:3:read_file", "result:4:run_command"]
    assert names["project"]["result:3:read_file"].chars > 10_000
    # Stored from 6,000 characters on (46.3): the model sees its start, its end and a note.
    assert 4_000 < names["project"]["result:4:run_command"].chars < 6_000
    assert "finish_goal" in dict(situations["goal_turn"].tools) and "finish_goal" not in dict(project.tools)
    assert names["goal_turn"]["message:user"].chars > names["project"]["message:user"].chars
