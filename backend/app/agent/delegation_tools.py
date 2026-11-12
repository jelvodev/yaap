"""The tools that steer the run itself: delegate a task to a sub-run, and finish the conversation's goal.

The sub-runs are made by the run's `Delegator` (app.agent.delegation); goals by app.core.goals.
"""

from typing import Any

import anyio

from app.agent.brief import Brief, BriefError, parse_brief
from app.agent.escalation import Role
from app.agent.runs import AgentRun
from app.agent.toolkit import STRING, Tool, ToolError, schema, text_argument


async def delegate(run: AgentRun, arguments: dict[str, Any]) -> str:
    # Imported here: delegation builds a sub-run's toolbox, which imports the registry, which imports this module.
    from app.agent.delegation import DelegationError

    if run.delegator is None:
        raise ToolError("Delegation is not available in this run.")
    brief, role = _brief_and_role(arguments, run.delegator.by_brief(run.environment_id))
    agent = text_argument(arguments, "agent", required=False).strip() or None
    worker = text_argument(arguments, "worker", required=False).strip() or None
    mode = str(arguments.get("mode") or "work")
    if mode not in ("work", "explore"):
        raise ToolError("mode must be work or explore.")
    try:
        return await run.delegator.delegate(run, brief, agent, role, worker, mode)
    except DelegationError as error:
        raise ToolError(str(error)) from error


def _brief_and_role(arguments: dict[str, Any], by_brief: bool) -> tuple[Brief, Role | None]:
    """The task as a brief and the asked role. Free mode keeps the task as given and defaults to the worker; in brief
    mode no role asked leaves the choice to the delegation (checkable work goes to the worker, the rest stays)."""
    asked = arguments.get("role")
    if by_brief:
        if asked is not None and asked not in ("worker", "main"):
            raise ToolError("role must be worker or main.")
        try:
            return parse_brief(arguments), asked or None
        except BriefError as error:
            raise ToolError(str(error)) from error
    role = str(asked or "worker")
    if role not in ("worker", "main"):
        raise ToolError("role must be worker or main.")
    return Brief(task=text_argument(arguments, "task")), role


async def finish_goal(run: AgentRun, arguments: dict[str, Any]) -> str:
    if run.goal is None:
        raise ToolError("This conversation has no open goal.")
    status = str(arguments.get("status") or "")
    if status not in ("done", "blocked"):
        raise ToolError("status must be done or blocked.")
    return await anyio.to_thread.run_sync(run.goal.finish, status, text_argument(arguments, "summary"))


WORKER_PROPERTY = {
    "type": "string",
    "description": (
        "Optional: a preset by name (its worker model), claude_code or codex (CLIs on the user's "
        "subscription). Default: this conversation's worker model."
    ),
}

FREE_DELEGATE = Tool(
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
            "worker": WORKER_PROPERTY,
        },
        ["task"],
    ),
    "auto",
    delegate,
)

BRIEF_DELEGATE = Tool(
    "delegate",
    "Hand a self-contained task to a sub-run. It does not see this conversation: give the brief fields (files, "
    "facts, done_when, check) so the worker does not have to search. Exploring goes to the worker (mode explore; up "
    "to three explore tasks run at once when you call them in parallel). Work goes to the cheap worker only with a "
    "check; without one it runs on the main model unless you set role worker. YAAP runs the check itself; a failed "
    "check goes back to the worker once and then to the main model. Keep planning, architecture, hard debugging and "
    "review yourself.",
    schema(
        {
            "task": STRING,
            "files": {
                "type": "array",
                "items": STRING,
                "description": "Files to start from: paths relative to the project, optionally with a line range as "
                "path:12-40.",
            },
            "facts": {"type": "string", "description": "What is already known and the worker need not find out."},
            "done_when": {"type": "string", "description": "When the work counts as done."},
            "check": {
                "type": "string",
                "description": "A single command that proves the work, such as a test. YAAP runs it after the worker.",
            },
            "mode": {"type": "string", "enum": ["work", "explore"], "description": "Default work."},
            "agent": {"type": "string", "description": "Optional agent definition, by name."},
            "role": {
                "type": "string",
                "enum": ["worker", "main"],
                "description": "Default: worker with a check, main without one.",
            },
            "worker": WORKER_PROPERTY,
        },
        ["task"],
    ),
    "auto",
    delegate,
)


def delegate_tool_for(run: AgentRun) -> Tool:
    """The `delegate` tool this run offers: with the brief fields when its preset delegates by brief."""
    delegator = run.delegator
    return BRIEF_DELEGATE if delegator is not None and delegator.by_brief(run.environment_id) else FREE_DELEGATE


DELEGATION_TOOLS = [
    FREE_DELEGATE,
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
