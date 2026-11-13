"""The agent's tool for the project's Current state: the short block at the top of the memory that every new
conversation of the project starts from."""

from typing import Any

import anyio

from app.agent.runs import AgentRun
from app.agent.tool_context import project_root
from app.agent.toolkit import STRING, Tool, ToolError, schema, text_argument
from app.projects.current_state import MAX_STATE_CHARS
from app.projects.state_refresh import StateError, save_state


async def update_current_state(run: AgentRun, arguments: dict[str, Any]) -> str:
    text = text_argument(arguments, "text")
    try:
        await anyio.to_thread.run_sync(save_state, project_root(run), run.environment_id, text, "written by the agent")
    except StateError as error:
        raise ToolError(str(error)) from error
    return "The current state is updated in .blueprint/memory.md and committed on its own."


STATE_TOOLS = [
    Tool(
        "update_current_state",
        "Rewrite the project's current state: the short block every new conversation of this project starts from. "
        "Use it at the end of a goal or a build, and when the user stops a long piece of work. Replace the whole "
        f"block, at most {MAX_STATE_CHARS} characters, with three bold lead-ins: **Where it stands:** what works and "
        "what was finished last; **Open:** what is unfinished or waits for a decision; **Next step:** the one thing "
        "to do next. No headings. Write it in the work language named in the system prompt.",
        schema({"text": STRING}, ["text"]),
        "auto",
        update_current_state,
    ),
]
