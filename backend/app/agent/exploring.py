"""Exploring by a worker: `delegate(mode="explore")` starts a sub-run that only reads.

An exploring sub-run gets the reading and searching tools of its parent and nothing else: the registry tools and the
extra tools (outside MCP servers, the browser) that are marked read-only and run without asking. Which tools those
are follows from the tools' own flags, so a new reading tool joins without a list to keep. The sub-run's access level
is read only as well, so the permission decision refuses anything that would change something even if it were
offered; and as its tools never ask, it needs no approvals. Its report goes back capped, so finding something out
costs the parent a short answer with files, line numbers and a conclusion instead of the files themselves.
"""

from dataclasses import replace

from app.agent.runs import AgentRun
from app.agent.toolkit import Tool
from app.agent.tools import TOOLS

# Read-only for the injection guard, but not reading: it opens the shared document in a panel for the user.
NOT_EXPLORING = frozenset({"open_document"})
MAX_EXPLORE_REPORT = 2000


def explores(tool: Tool) -> bool:
    """Whether an exploring sub-run may use the tool: it only reads and never asks."""
    return tool.read_only and tool.permission == "auto" and tool.name not in NOT_EXPLORING


def explore_tools() -> frozenset[str]:
    """The registry tools an exploring sub-run may use (plugins add tools at load time, so this is read per call)."""
    return frozenset(tool.name for tool in TOOLS if explores(tool))


def explore_only(run: AgentRun) -> None:
    """Narrow a freshly made sub-run to reading and searching, before it is offered any tool."""
    run.config = replace(
        run.config,
        tool_names=run.tool_names & explore_tools(),
        policy=replace(run.policy, access="read_only"),
    )
    run.services = replace(
        run.services, extra_tools={name: tool for name, tool in run.extra_tools.items() if explores(tool)}
    )


def capped_report(report: str) -> str:
    """The report as the parent gets it: at most MAX_EXPLORE_REPORT characters, with a note when it was cut."""
    if len(report) <= MAX_EXPLORE_REPORT:
        return report
    cut = report[:MAX_EXPLORE_REPORT].rstrip()
    return f"{cut}\n\n[Report cut at {MAX_EXPLORE_REPORT:,} characters; {len(report) - len(cut):,} more were left out.]"
