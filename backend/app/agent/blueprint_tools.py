"""The Blueprint tools: load a standard or a skill, and add an entry to the project memory."""

from typing import Any

import anyio

from app.agent.guard import note_outside
from app.agent.runs import AgentRun
from app.agent.tool_context import project_root
from app.agent.toolkit import STRING, Tool, ToolError, schema, text_argument
from app.blueprint.layers import active_items
from app.blueprint.skill_install import read_origin
from app.projects.memory import SECTIONS, ProjectMemoryError, remember


def load_item(run: AgentRun, name: str, kinds: tuple[str, ...]) -> str:
    for item in active_items(run.blueprint_layers):
        if item.kind in kinds and item.name == name:
            text = item.text()
            origin = read_origin(item.source, item.name) if item.kind == "skill" else None
            if origin is not None:
                note_outside(run, f"skill {name} from GitHub ({origin.source.repository})", text)
            return text
    available = sorted(item.name for item in active_items(run.blueprint_layers) if item.kind in kinds)
    raise ToolError(f"No {' or '.join(kinds)} named '{name}'. Available: {', '.join(available) or 'none'}.")


async def load_standard(run: AgentRun, arguments: dict[str, Any]) -> str:
    return load_item(run, text_argument(arguments, "name"), ("standard", "engineering"))


async def load_skill(run: AgentRun, arguments: dict[str, Any]) -> str:
    return load_item(run, text_argument(arguments, "name"), ("skill",))


async def update_memory(run: AgentRun, arguments: dict[str, Any]) -> str:
    section = text_argument(arguments, "section")
    entry = text_argument(arguments, "entry")
    try:
        await anyio.to_thread.run_sync(remember, project_root(run), run.environment_id, section, entry)
    except ProjectMemoryError as error:
        raise ToolError(str(error)) from error
    return f"Remembered under {section}. The entry is in .blueprint/memory.md and committed on its own."


BLUEPRINT_TOOLS = [
    Tool(
        "load_standard",
        "Load the full text of a standard or engineering standard from the Blueprint by name.",
        schema({"name": STRING}, ["name"]),
        "auto",
        load_standard,
        scope="always",
        read_only=True,
    ),
    Tool(
        "load_skill",
        "Load the full text of a skill from the Blueprint by name.",
        schema({"name": STRING}, ["name"]),
        "auto",
        load_skill,
        scope="always",
        read_only=True,
    ),
    Tool(
        "update_memory",
        "Add one entry to the project memory, which every conversation of this project sees. Use it for decisions, "
        "conventions and lessons that will matter later, not for the current task's details. Write the entry in "
        "the work language named in the system prompt, whatever language the conversation is in.",
        schema(
            {"section": {"type": "string", "enum": list(SECTIONS)}, "entry": STRING},
            ["section", "entry"],
        ),
        "auto",
        update_memory,
    ),
]
