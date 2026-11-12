"""The built-in command /plan: make a plan without changing anything.

`/plan <what>` in a project conversation starts a read-only reply briefed by the built-in `planner` item: the agent
reads, searches and explores, then hands over its plan with `submit_plan`, which the chat shows as a draft card. A
Blueprint command named `plan` wins, as custom commands are expanded first.
"""

import re
from typing import Any

from app.blueprint.builtin import instructions as builtin_instructions
from app.blueprint.layers import Layer
from app.core.reply_errors import CommandRefused
from app.core.reply_setup import ReplySetup

PLAN_COMMAND = re.compile(r"^/plan(?:\s+(.*))?$", re.DOTALL)
MAX_REQUEST = 8000


class PlanError(Exception):
    """A /plan that cannot run; the message is safe to show."""


def plan_block(layers: list[Layer], text: str) -> dict[str, Any] | None:
    """The command block for a `/plan` message, or None when the message is not one."""
    match = PLAN_COMMAND.match(text.strip())
    if match is None:
        return None
    request = (match.group(1) or "").strip()
    if not request:
        raise PlanError("Say what to plan: /plan <what to build or change>.")
    if len(request) > MAX_REQUEST:
        raise PlanError(f"The request is over {MAX_REQUEST} characters; describe it shorter.")
    briefing = builtin_instructions("planner", layers)
    text = f"{briefing}\n\nWhat to plan:\n\n{request}"
    return {"type": "command", "name": "plan", "text": text, "read_only": True}


def plan_command(setup: ReplySetup, text: str) -> dict[str, Any] | None:
    """/plan as a built-in command: only in a project conversation, and the reply is read only."""
    if PLAN_COMMAND.match(text.strip()) is None:
        return None
    if setup.agent_project is None:
        raise CommandRefused("/plan works in a project conversation.")
    try:
        return plan_block(setup.layers, text)
    except PlanError as error:
        raise CommandRefused(str(error)) from error
