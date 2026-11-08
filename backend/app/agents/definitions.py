"""Agent definitions: agents/<name>.md in a Blueprint layer.

Front matter:
    description: one line about what the agent does
    preset: the model preset it works with, by name (optional)
    tools: the YAAP tools it may use (optional; without it, every tool the conversation offers)
    skills: skills whose full text is always in its context (optional)
    commands: the /commands it offers (optional; without it, all)
The body is the role prompt.
"""

from dataclasses import dataclass, field
from typing import Any

from app.blueprint.layers import Item, Layer, active_items


@dataclass(frozen=True)
class AgentDefinition:
    name: str
    description: str
    role_prompt: str
    source_label: str
    path: str
    preset: str | None = None
    # A model role instead of a preset: `worker` runs this agent on the worker model.
    role: str | None = None
    tools: frozenset[str] | None = None
    skills: tuple[str, ...] = ()
    commands: tuple[str, ...] | None = None
    warnings: tuple[str, ...] = field(default=())


def text_list(value: Any) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = [part.strip() for part in value.split(",")]
    if not isinstance(value, list):
        return None
    return tuple(str(item).strip() for item in value if str(item).strip())


def definition_from(item: Item, known_tools: set[str]) -> AgentDefinition:
    front = item.document.front_matter
    tools = text_list(front.get("tools"))
    warnings = []
    if tools is not None:
        unknown = sorted(set(tools) - known_tools)
        if unknown:
            warnings.append(f"Unknown tools are ignored: {', '.join(unknown)}.")
    preset = front.get("preset")
    role = str(front.get("role") or "").strip().lower() or None
    if role is not None and role not in ("main", "worker"):
        warnings.append(f"Unknown role '{role}' is ignored; use main or worker.")
        role = None
    return AgentDefinition(
        name=item.name,
        description=item.summary,
        role_prompt=item.document.body.strip(),
        source_label=item.source.label,
        path=item.path,
        preset=str(preset).strip() if preset else None,
        role=role,
        tools=frozenset(tools) & known_tools if tools is not None else None,
        skills=text_list(front.get("skills")) or (),
        commands=text_list(front.get("commands")),
        warnings=tuple(warnings),
    )


def agent_definitions(layers: list[Layer]) -> list[AgentDefinition]:
    from app.agent.tools import TOOLS_BY_NAME

    known = set(TOOLS_BY_NAME)
    return [definition_from(item, known) for item in active_items(layers) if item.kind == "agent"]


def find_agent(layers: list[Layer], name: str | None) -> AgentDefinition | None:
    if not name:
        return None
    return next((agent for agent in agent_definitions(layers) if agent.name == name), None)


def agent_part(agent: AgentDefinition, layers: list[Layer]) -> str:
    """Part 5 of the context: who the agent is in this conversation, with the full text of its skills."""
    lines = [f"# Your role: {agent.name}"]
    if agent.description:
        lines.append(agent.description)
    if agent.role_prompt:
        lines.append(agent.role_prompt)
    skills = {item.name: item for item in active_items(layers) if item.kind == "skill"}
    for name in agent.skills:
        if name in skills:
            lines.append(f'<skill name="{name}">\n{skills[name].document.body.strip()}\n</skill>')
    return "\n\n".join(lines)


READ_TOOLS = [
    "list_files",
    "read_file",
    "search",
    "git_status",
    "git_diff",
    "load_standard",
    "load_skill",
    "list_attachment",
    "read_attachment",
    "search_attachment",
]

AGENT_TEMPLATES: dict[str, str] = {
    "developer": """---
description: Builds and changes code in the project, tests it and commits when asked.
preset: Balanced
---

You are the developer. You turn the user's request into working code that follows the standards of this Blueprint.

- Read the relevant code before you change it, and keep changes to what was asked.
- Run the project's tests or build after a change when the project has them, and report the outcome plainly.
- Explain what you changed in a few lines; mention anything you could not verify.
""",
    "reviewer": f"""---
description: Reviews changes and code against the standards, without changing anything.
preset: Deep
tools: [{", ".join(READ_TOOLS)}]
---

You are the reviewer. You read the changes (git_diff) and the code around them, and you report findings; you never
change files.

- Order findings by severity: correctness and security first, then standards, then style.
- For each finding give the file and line, what is wrong, why it matters and a concrete fix.
- Say clearly when you found nothing that needs to change.
""",
    "architect": f"""---
description: Designs and plans changes before code is written, weighing options against the standards.
preset: Deep
tools: [{", ".join(READ_TOOLS)}]
---

You are the architect. You help the user decide how to build something before it is built.

- Start from the current structure of the project; read it before proposing anything.
- Offer a recommendation with the main alternative and the trade-off, not a survey of every option.
- End with a short, ordered plan of steps that a developer can carry out.
""",
}
