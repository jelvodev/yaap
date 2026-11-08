"""The tool registry offers exactly the tools it offered before `agent/tools.py` was split into modules.

`tool_list_snapshot.json` was generated from the code before the split: the tools in order, with every name,
description, input schema and the properties the gate reads, and the registry's sets. A change to a tool's text or
schema is a change to what models see, so it updates the snapshot on purpose.
"""

import json
from pathlib import Path

from app.agent.toolkit import Tool
from app.agent.tools import CONDITIONAL_TOOLS, NOT_ON_TARGET, TARGET_TOOLS, TOOLS, TOOLS_BY_NAME

SNAPSHOT = Path(__file__).with_name("tool_list_snapshot.json")


def described(tool: Tool) -> dict[str, object]:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.input_schema,
        "permission": tool.permission,
        "scope": tool.scope,
        "read_only": tool.read_only,
        "asks_itself": tool.asks_itself,
        "takes_secrets": tool.takes_secrets,
        "describes": tool.describe is not None,
        "conditional": tool.needs_approval is not None,
    }


def test_the_tool_list_is_the_one_from_before_the_split() -> None:
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert [tool["name"] for tool in expected["tools"]] == [tool.name for tool in TOOLS]
    for want, tool in zip(expected["tools"], TOOLS, strict=True):
        assert described(tool) == want, tool.name
    assert CONDITIONAL_TOOLS == expected["conditional_tools"]
    assert sorted(TARGET_TOOLS) == expected["target_tools"]
    assert sorted(NOT_ON_TARGET) == expected["not_on_target"]
    assert list(TOOLS_BY_NAME) == [tool.name for tool in TOOLS]
