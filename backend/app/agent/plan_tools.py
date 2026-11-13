"""The tool that hands over a plan: `submit_plan`, called once at the end of a /plan reply.

It writes only a row in `plans` (app.plans), so it is allowed in a read-only reply. The plan is shown to the user as a
draft card; nothing in it runs until the user approves it.
"""

from typing import Any

import anyio

from app.agent.runs import AgentRun
from app.agent.toolkit import STRING, Tool, ToolError, schema
from app.core.reply_event_models import PlanSubmittedEvent
from app.plans.service import InvalidPlan, PlanConflict

CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "How a person knows the step is done."},
        "run": {
            "type": "string",
            "description": "Optional: one command line YAAP runs to prove it, such as a test or a type check.",
        },
    },
    "required": ["text"],
    "additionalProperties": False,
}
STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "title": STRING,
        "details": {"type": "string", "description": "What to change and why."},
        "files": {"type": "array", "items": STRING, "description": "Paths relative to the project, with /."},
        "checks": {"type": "array", "items": CHECK_SCHEMA, "description": "1 to 10 checks that prove the step."},
    },
    "required": ["title", "checks"],
    "additionalProperties": False,
}


async def submit_plan(run: AgentRun, arguments: dict[str, Any]) -> str:
    plans, project_id = run.plans, run.project_id
    if plans is None or project_id is None:
        raise ToolError("Plans can be handed over only in a project conversation.")
    try:
        view = await anyio.to_thread.run_sync(plans.submit, project_id, run.conversation_id, arguments)
    except InvalidPlan as error:
        listed = "\n".join(f"- {problem}" for problem in error.errors)
        raise ToolError(f"The plan was not accepted. Fix all of these and call submit_plan again:\n{listed}") from error
    except PlanConflict as error:
        raise ToolError(str(error)) from error
    run.events.put_nowait(PlanSubmittedEvent(plan_id=view.id))
    warnings = "".join(f"\nNote: {warning}" for warning in view.warnings)
    return f"Plan {view.id} is shown to the user as a draft.{warnings}"


PLAN_TOOLS = [
    Tool(
        "submit_plan",
        "Hand over your plan, once, at the end of planning: a title, a short summary, the risks and the steps, each "
        "with the files it touches and the checks that prove it. The user sees it as a draft, edits it and approves "
        "it before anything is built. Returns the plan's number, or every problem to fix.",
        schema(
            {
                "title": STRING,
                "summary": STRING,
                "risks": {"type": "array", "items": STRING},
                "steps": {"type": "array", "items": STEP_SCHEMA, "description": "1 to 30 steps, in build order."},
            },
            ["title", "steps"],
        ),
        "auto",
        submit_plan,
    ),
]
