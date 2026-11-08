"""The tools that steer the run itself: delegate a task to a sub-run, and finish the conversation's goal.

The sub-runs are made by the run's `Delegator` (app.agent.delegation); goals by app.core.goals.
"""

from typing import Any

import anyio

from app.agent.runs import AgentRun
from app.agent.toolkit import STRING, Tool, ToolError, schema, text_argument


async def delegate(run: AgentRun, arguments: dict[str, Any]) -> str:
    # Imported here: delegation builds a sub-run's toolbox, which imports the registry, which imports this module.
    from app.agent.delegation import DelegationError, report_text

    if run.delegator is None:
        raise ToolError("Delegation is not available in this run.")
    task = text_argument(arguments, "task")
    agent = text_argument(arguments, "agent", required=False).strip() or None
    role = str(arguments.get("role") or "worker")
    if role not in ("worker", "main"):
        raise ToolError("role must be worker or main.")
    worker = text_argument(arguments, "worker", required=False).strip() or None
    mode = str(arguments.get("mode") or "work")
    if mode not in ("work", "explore"):
        raise ToolError("mode must be work or explore.")
    try:
        result = await run.delegator.delegate(run, task, agent, role, worker, mode)
    except DelegationError as error:
        raise ToolError(str(error)) from error
    return report_text(result)


async def finish_goal(run: AgentRun, arguments: dict[str, Any]) -> str:
    if run.goal is None:
        raise ToolError("This conversation has no open goal.")
    status = str(arguments.get("status") or "")
    if status not in ("done", "blocked"):
        raise ToolError("status must be done or blocked.")
    return await anyio.to_thread.run_sync(run.goal.finish, status, text_argument(arguments, "summary"))


DELEGATION_TOOLS = [
    Tool(
        "delegate",
        "Hand a self-contained task to a sub-run, on the cheaper worker model unless you name an agent or role main. "
        "It does not see this conversation: give the goal, files, constraints and how to check. mode explore only "
        "reads and searches and reports files, lines and a conclusion: use it to find things out without filling "
        "your context. Otherwise it works with your tools; keep planning and decisions yourself and check its changes.",
        schema(
            {
                "task": STRING,
                "mode": {"type": "string", "enum": ["work", "explore"], "description": "Default work."},
                "agent": {"type": "string", "description": "Optional agent definition, by name."},
                "role": {"type": "string", "enum": ["worker", "main"], "description": "Default worker."},
                "worker": {
                    "type": "string",
                    "description": (
                        "Optional: a preset by name (its worker model), claude_code or codex (CLIs on the user's "
                        "subscription). Default: this conversation's worker model."
                    ),
                },
            },
            ["task"],
        ),
        "auto",
        delegate,
    ),
    Tool(
        "finish_goal",
        "End the conversation's goal: status done when it is reached, blocked when you cannot go on without the "
        "user. The summary says what was achieved, or what you need.",
        schema(
            {"status": {"type": "string", "enum": ["done", "blocked"]}, "summary": STRING},
            ["status", "summary"],
        ),
        "auto",
        finish_goal,
    ),
]
