import pytest

from app.core.token_hints import (
    COLD_PAUSE_SECONDS,
    ROADMAP_ITEM,
    ContextFacts,
    HintFacts,
    ReplyGap,
    cold_cache,
    mcp_share,
    repeated_read,
    session_hints,
    tool_results_heavy,
)


def facts(
    reads: dict[str, int] | None = None, gaps: list[ReplyGap] | None = None, context: ContextFacts | None = None
) -> HintFacts:
    return HintFacts(file_reads=reads or {}, gaps=gaps or [], context=context)


def context(total: int = 1000, results: int = 0, definitions: dict[str, int] | None = None) -> ContextFacts:
    return ContextFacts(total, results, definitions or {})


@pytest.mark.parametrize(
    ("reads", "expected"),
    [
        ({}, None),
        ({"a.py": 1, "b.py": 1}, None),
        ({"a.py": 2}, "a.py was read 2 times."),
        ({"a.py": 4, "b.py": 1, "c.py": 3}, "a.py was read 4 times. c.py was read 3 times."),
        (
            {"a.py": 2, "b.py": 5, "c.py": 3, "d.py": 4},
            "b.py was read 5 times. d.py was read 4 times. c.py was read 3 times.",
        ),
    ],
)
def test_repeated_read_names_the_most_read_files_and_ignores_single_reads(reads, expected):
    hint = repeated_read(facts(reads=reads))
    assert (hint.text if hint else None) == expected
    if hint:
        assert hint.roadmap_item == 58


@pytest.mark.parametrize(
    ("gaps", "cold"),
    [
        ([], 0),
        ([ReplyGap(COLD_PAUSE_SECONDS, 0, 500)], 0),
        ([ReplyGap(COLD_PAUSE_SECONDS + 1, 0, 500)], 1),
        ([ReplyGap(COLD_PAUSE_SECONDS + 1, 100, 500)], 0),
        ([ReplyGap(COLD_PAUSE_SECONDS + 1, 0, 0)], 0),
        ([ReplyGap(900, 0, 500), ReplyGap(10, 0, 500), ReplyGap(1200, 0, 700)], 2),
    ],
)
def test_cold_cache_counts_replies_after_a_long_pause_that_read_nothing_from_the_cache(gaps, cold):
    hint = cold_cache(facts(gaps=gaps))
    if cold == 0:
        assert hint is None
    else:
        assert hint is not None
        assert hint.text.startswith(f"{cold} repl")
        assert hint.roadmap_item == 64


@pytest.mark.parametrize(
    ("facts_context", "shown"),
    [
        (None, False),
        (context(total=0, results=0), False),
        (context(total=1000, results=500), False),
        (context(total=1000, results=501), True),
        (context(total=1000, results=0), False),
    ],
)
def test_tool_results_heavy_needs_more_than_half_of_the_context(facts_context, shown):
    hint = tool_results_heavy(facts(context=facts_context))
    assert (hint is not None) is shown
    if hint:
        assert "50%" in hint.text and hint.roadmap_item == 58


@pytest.mark.parametrize(
    ("definitions", "server"),
    [
        ({}, None),
        ({"yaap": 700, "mcp:docs": 300}, None),
        ({"yaap": 600, "mcp:docs": 400}, "docs"),
        ({"yaap": 100, "browser": 900}, None),
        ({"yaap": 100, "mcp:a": 300, "mcp:b": 600}, "b"),
        ({"mcp:a": 0}, None),
    ],
)
def test_mcp_share_names_the_server_over_a_third_of_the_tool_definitions(definitions, server):
    hint = mcp_share(facts(context=context(definitions=definitions)))
    assert (hint is not None) is (server is not None)
    if hint:
        assert f"MCP server {server} " in hint.text


def test_mcp_share_is_empty_without_context():
    assert mcp_share(facts()) is None


def test_session_hints_are_empty_for_empty_facts_and_ordered_otherwise():
    assert session_hints(facts()) == []
    found = session_hints(
        facts(
            reads={"a.py": 2},
            gaps=[ReplyGap(1000, 0, 10)],
            context=context(results=900, definitions={"mcp:docs": 10}),
        )
    )
    assert [hint.kind for hint in found] == ["repeated_read", "cold_cache", "tool_results_heavy", "mcp_share"]


def test_each_hint_kind_points_at_its_public_roadmap_item():
    assert ROADMAP_ITEM == {"repeated_read": 58, "cold_cache": 64, "tool_results_heavy": 58, "mcp_share": 58}


@pytest.mark.parametrize(
    ("hint", "needle"),
    [
        (repeated_read(facts(reads={"a.py": 2})), "2 times or more"),
        (cold_cache(facts(gaps=[ReplyGap(900, 0, 500)])), "more than 5 minutes"),
        (tool_results_heavy(facts(context=context(results=900))), "over 50%"),
        (mcp_share(facts(context=context(definitions={"mcp:docs": 10}))), "over 30%"),
    ],
)
def test_each_hint_names_the_threshold_that_raised_it(hint, needle):
    assert hint is not None and hint.why is not None
    assert hint.why.source == "hint" and needle in hint.why.rule
