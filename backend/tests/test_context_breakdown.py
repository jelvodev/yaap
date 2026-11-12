"""The categories of the context breakdown (core/context_categories.py): mapping, splitting and scaling."""

import pytest

from app.agent.tool_catalog import searchable_tool_count
from app.core.context_breakdown import RequestParts, categories_of, masked_blocks, trimmed_tool_tokens
from app.core.context_categories import (
    ContextCategory,
    StepRef,
    finish_categories,
    remainder_category,
    scale_to_total,
    space_categories,
    split_messages,
    split_system,
    split_tools,
    system_category,
)
from app.core.context_control import ContextControl
from app.providers.base import ToolDefinition
from app.storage.models import Message

TOOL_CALL = {
    "type": "tool_call",
    "id": "c1",
    "name": "read_file",
    "input": {"path": "src/api.py"},
    "output": "x" * 400,
}


@pytest.mark.parametrize(
    ("estimates", "total", "expected"),
    [
        ([10, 20, 30], 600, [100, 200, 300]),
        ([1, 1, 1], 100, [34, 33, 33]),
        ([5, 3, 2], 7, [4, 2, 1]),
        ([0, 0, 0], 100, [0, 0, 0]),
        ([10, 20], 0, [0, 0]),
        ([42], 1_000, [1_000]),
        ([0, 7], 9, [0, 9]),
        ([], 5, []),
    ],
)
def test_scaled_estimates_add_up_to_the_exact_total(estimates, total, expected):
    scaled = scale_to_total(estimates, total)

    assert scaled == expected
    assert sum(scaled) in (total, 0)


@pytest.mark.parametrize(
    ("source", "text", "key"),
    [
        ("base", "You are YAAP.", "system_base"),
        ("project", "Project: x", "system_project"),
        ("repo_map", "src/a.py", "system_repo_map"),
        ("rules", "Rules", "blueprint_rules"),
        ("standards", "Standards", "blueprint_standards"),
        ("skills", "Skills", "blueprint_skills"),
        ("agent", "You are the reviewer.", "system_other"),
        ("target", "A remote machine.", "system_other"),
        ("document", "Shared.", "system_other"),
        ("delegation", "Do this.", "system_other"),
        ("utility", "Summarise.", "system_other"),
        ("other", "## MCP servers\n\n- github (3 tools)", "system_mcp_servers"),
        ("other", "Something else", "system_other"),
    ],
)
def test_a_system_part_goes_into_the_category_of_its_source(source, text, key):
    assert system_category(source, text) == key


def test_tools_split_into_yaap_browser_and_each_mcp_server():
    definitions = [
        ToolDefinition("read_file", "Read a file.", {}),
        ToolDefinition("browser__click", "Click.", {}),
        ToolDefinition("tracker__get_issue", "Get an issue.", {}),
        ToolDefinition("tracker__list_issues", "List issues.", {}),
    ]

    drafts = {draft.key: draft for draft in split_tools(definitions)}

    assert [item.label for item in drafts["tools_yaap"].items] == ["read_file"]
    assert sorted(item.label for item in drafts["tools_joined"].items) == ["MCP: tracker", "browser"]
    groups = {item.label: item.tool_group for draft in drafts.values() for item in draft.items}
    assert groups == {"read_file": "yaap", "browser": "browser", "MCP: tracker": "mcp:tracker"}


def test_messages_split_into_the_kinds_of_content_with_step_links():
    messages = [
        ("user", [{"type": "text", "text": "Open the api"}]),
        (
            "assistant",
            [
                {"type": "reasoning", "text": "I should read it."},
                TOOL_CALL,
                {"type": "text", "text": "Done."},
            ],
        ),
    ]

    drafts = {draft.key: draft for draft in split_messages(messages, {"c1": StepRef(3, 17)})}

    assert set(drafts) == {
        "messages_user",
        "messages_answers",
        "messages_thinking",
        "messages_tool_calls",
        "messages_tool_results",
    }
    (result,) = drafts["messages_tool_results"].items
    assert (result.label, result.step_id, result.run_id, result.tokens) == ("read_file src/api.py", 17, 3, 100)


def test_a_cli_connection_keeps_yaap_parts_unscaled_and_shows_the_rest_as_remainder():
    drafts = split_system([("base", "x" * 400)]) + split_tools([ToolDefinition("read_file", "Read.", {})])
    estimates = sum(draft.tokens for draft in drafts)

    categories = finish_categories(drafts, 5_000, scale=False)
    categories.append(remainder_category(5_000, categories))

    assert sum(category.tokens for category in categories[:-1]) == estimates
    assert categories[-1].key == "cli_remainder" and categories[-1].group == "cli"
    assert categories[-1].tokens == 5_000 - estimates


def test_yaap_connections_scale_their_parts_to_the_total_and_list_the_largest_items_first():
    parts = [("base", "x" * 400), ("rules", "y" * 1_200)]
    categories = finish_categories(split_system(parts), 1_000, scale=True)

    assert sum(category.tokens for category in categories) == 1_000
    assert [category.key for category in categories] == ["system_base", "blueprint_rules"]
    assert all(isinstance(category, ContextCategory) for category in categories)


@pytest.mark.parametrize(
    ("total", "limit", "compact_at", "buffer", "free"),
    [
        (300, 1_000, 700, 400, 300),
        (800, 1_000, 700, 0, 200),
        (1_200, 1_000, 700, 0, 0),
        (300, 1_000, None, None, 700),
        (300, None, None, None, None),
    ],
)
def test_the_buffer_runs_to_the_compaction_point_and_the_free_space_to_the_limit(
    total, limit, compact_at, buffer, free
):
    sizes = {category.key: category.tokens for category in space_categories(total, limit, compact_at)}

    assert sizes.get("buffer") == buffer
    assert sizes.get("free") == free


def test_a_cli_whose_parts_exceed_the_exact_total_scales_them_down_and_has_no_remainder():
    parts = RequestParts([("base", "x" * 4_000)], [], [("user", [{"type": "text", "text": "y" * 4_000}])])
    control = ContextControl(window=None, limit=None, source="default", compacts_itself=True)

    categories, used = categories_of(parts, {}, 1_000, control)

    sizes = {category.key: category.tokens for category in categories}
    assert used == 1_000 and sum(sizes.values()) == 1_000 and sizes["cli_remainder"] == 0


def test_a_cli_keeps_its_estimates_and_a_remainder_when_they_fit_in_the_exact_total():
    parts = RequestParts([("base", "x" * 400)], [], [])
    control = ContextControl(window=None, limit=None, source="default", compacts_itself=True)

    categories, _ = categories_of(parts, {}, 1_000, control)

    sizes = {category.key: category.tokens for category in categories}
    assert sizes == {"system_base": 100, "cli_remainder": 900}


@pytest.mark.parametrize(
    ("listing", "count"),
    [
        ("", 0),
        ("## MCP servers\n\n- github (3 tools, find_tools)\n- tracker (1 tool, find_tools)", 4),
        ("## MCP servers\n\n- github (3 tools)\n- tracker (2 tools, find_tools)", 2),
        ("Some other part with - a dash (5 tools, find_tools) inside a line", 0),
    ],
)
def test_the_tools_waiting_for_find_tools_are_counted_from_the_server_listing(listing, count):
    assert searchable_tool_count(listing) == count


def test_labels_of_an_assembled_context_hide_secret_values():
    secret = "hunter2-hunter2-hunter2"
    block = {**TOOL_CALL, "input": {"command": f"curl -u admin:{secret} https://example.test"}}

    drafts = split_messages([("assistant", masked_blocks([block], {"TOKEN": secret}))], {})

    labels = [item.label for draft in drafts for item in draft.items]
    assert labels and all(secret not in label for label in labels)
    assert any("[secret:TOKEN]" in label for label in labels)


def branch_with(*, trimmed: bool) -> list[Message]:
    long_call = {**TOOL_CALL, "output": "y" * 10_000}
    reply = Message(role="assistant", content=[long_call], provider_raw={}, status="complete")
    last = Message(role="user", content=[{"type": "trim"}] if trimmed else [{"type": "text", "text": "Hi"}])
    return [Message(role="user", content=[{"type": "text", "text": "Go"}]), reply, last]


@pytest.mark.parametrize(("trimmed", "saved"), [(False, False), (True, True)])
def test_tokens_saved_by_trimming_show_only_after_a_trim(trimmed, saved):
    assert (trimmed_tool_tokens(branch_with(trimmed=trimmed)) > 0) is saved
