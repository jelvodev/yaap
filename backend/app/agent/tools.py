"""The registry of YAAP's agent tools: one implementation, offered to every model through MCP or YAAP's
own runner.

Each tool module defines its tools as a list; this module only lists them, in the order models see them. A new tool
module adds one line to TOOLS. Plugins add tools at load time with register_tool. Calls go through the gate in
app.agent.gate.
"""

from app.agent.attachment_tools import ATTACHMENT_TOOLS
from app.agent.blueprint_tools import BLUEPRINT_TOOLS
from app.agent.board_tools import BOARD_TOOLS
from app.agent.code_tools import CODE_TOOLS
from app.agent.command_tools import COMMAND_TOOLS
from app.agent.delegation_tools import DELEGATION_TOOLS, delegate_tool_for
from app.agent.document_tools import DOCUMENT_TOOLS
from app.agent.file_tools import FILE_TOOLS
from app.agent.git_tools import GIT_TOOLS
from app.agent.on_demand_tools import ON_DEMAND_TOOLS
from app.agent.output_tools import OUTPUT_TOOLS
from app.agent.plan_tools import PLAN_TOOLS
from app.agent.process_tools import PROCESS_TOOLS
from app.agent.runs import AgentRun
from app.agent.search_tools import SEARCH_TOOLS
from app.agent.skill_tools import SKILL_TOOLS
from app.agent.state_tools import STATE_TOOLS
from app.agent.toolkit import Tool
from app.agent.web_tools import WEB_TOOLS

TOOLS: list[Tool] = [
    *FILE_TOOLS,
    *COMMAND_TOOLS,
    *PROCESS_TOOLS,
    *GIT_TOOLS,
    *WEB_TOOLS,
    *OUTPUT_TOOLS,
    *BLUEPRINT_TOOLS,
    *STATE_TOOLS,
    *SEARCH_TOOLS,
    *CODE_TOOLS,
    *DELEGATION_TOOLS,
    *PLAN_TOOLS,
    *BOARD_TOOLS,
    *ATTACHMENT_TOOLS,
    *SKILL_TOOLS,
    *DOCUMENT_TOOLS,
    *ON_DEMAND_TOOLS,
]
TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}
# Tools that only add other tools to the run; over MCP every tool is listed at once, so these are left out there.
ON_DEMAND_NAMES = frozenset(tool.name for tool in ON_DEMAND_TOOLS)

# Tools that exist only when the run has what they need: the AgentRun attribute that must be set.
CONDITIONAL_TOOLS = {
    "delegate": "delegator",
    "finish_goal": "goal",
    "submit_plan": "plans",
    **{tool.name: "card" for tool in BOARD_TOOLS},
    "search_project": "searcher",
    "read_knowledge": "searcher",
    "web_search": "web_search",
    "read_output": "outputs",
    "open_document": "documents",
    "open_browser": "browser_on_demand",
    "find_tools": "mcp_behind_search",
    **{tool.name: "code_intel" for tool in CODE_TOOLS},
}

# On a computer over SSH: the file, command and process tools work there, also outside a project. Git tools and the
# semantic project search need the project folder in YAAP's container; git runs as a command there.
TARGET_TOOLS = frozenset(
    {
        "list_files",
        "read_file",
        "search",
        "write_file",
        "edit_file",
        "run_command",
        "start_process",
        "read_process_output",
        "stop_process",
        "list_processes",
    }
)
NOT_ON_TARGET = frozenset({"git_status", "git_diff", "git_commit", "git_push", "create_pull_request", "search_project"})


def register_tool(tool: Tool) -> None:
    """Add a tool from a plugin next to the core tools; the plugin host checks the name first."""
    if tool.name in TOOLS_BY_NAME:
        raise ValueError(f"A tool named {tool.name} exists already.")
    TOOLS.append(tool)
    TOOLS_BY_NAME[tool.name] = tool


def unregister_tool(name: str) -> None:
    tool = TOOLS_BY_NAME.pop(name, None)
    if tool is not None:
        TOOLS.remove(tool)


def tools_for(
    has_project: bool, has_attachments: bool, allowed: set[str] | None = None, on_target: bool = False
) -> frozenset[str]:
    """The tools of one run: what fits the conversation, narrowed to what its agent may use."""
    names = {
        tool.name
        for tool in TOOLS
        if tool.scope == "always"
        or (tool.scope == "project" and has_project)
        or (tool.scope == "attachments" and has_attachments)
        or (tool.scope == "project_attachments" and has_project and has_attachments)
    }
    if on_target:
        names = (names | TARGET_TOOLS) - NOT_ON_TARGET
    if allowed is not None:
        names &= allowed
    return frozenset(names)


def own_tools(run: AgentRun) -> list[Tool]:
    """The run's registry tools that it has what they need for."""
    return [
        delegate_tool_for(run) if tool.name == "delegate" else tool
        for tool in TOOLS
        if tool.name in run.tool_names
        and (tool.name not in CONDITIONAL_TOOLS or getattr(run, CONDITIONAL_TOOLS[tool.name]))
    ]


def run_tools(run: AgentRun) -> list[Tool]:
    """The tools a run offers to its next model call: its own registry tools, then its extra tools without those the
    catalog holds back until they join (the browser's until open_browser). Adapters with their own tool loop read it
    before every model call, so the list grows during the run."""
    held_back = run.catalog.deferred - run.state.joined_tools
    extra = sorted(run.extra_tools.items())
    return own_tools(run) + [tool for name, tool in extra if name not in TOOLS_BY_NAME and name not in held_back]


def mcp_listed_tools(run: AgentRun) -> list[Tool]:
    """The tools YAAP's MCP endpoint lists for a run (Claude Code, Codex): every tool at once, without the tools that
    only add others. A CLI reads the list once when its session starts and would not see tools that join later."""
    own = [tool for tool in own_tools(run) if tool.name not in ON_DEMAND_NAMES]
    return own + [tool for name, tool in sorted(run.extra_tools.items()) if name not in TOOLS_BY_NAME]


def find_tool(run: AgentRun, name: str) -> Tool | None:
    """The tool a call names. An extra tool the catalog still holds back is found too: over MCP every tool is listed
    at once, and a call passes the same gate either way."""
    if name in TOOLS_BY_NAME:
        return TOOLS_BY_NAME[name] if name in run.tool_names else None
    return run.extra_tools.get(name)
