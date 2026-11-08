"""The install_skill tool: the agent proposes a skill from GitHub; the user reviews and approves it.

Installing always asks, whatever the tool permissions say: the handler shows the review on the approval card itself,
so the permission gate does not ask a second time.
"""

import asyncio
from typing import Any

from app.agent.guard import guard_reason
from app.agent.runs import AgentRun, ask_approval
from app.agent.toolkit import STRING, Tool, ToolError, schema, text_argument, truncate
from app.blueprint.layers import BlueprintError, Layer
from app.blueprint.skill_install import Candidate, GitHubSkills, Source, install_at_commit, preview

LEVELS = ("global", "environment", "project")


def target_layer(run: AgentRun, level: str) -> Layer:
    for layer in run.blueprint_layers:
        if layer.kind == level:
            return layer
    raise ToolError(f"This conversation has no {level} level. Choose one of: "
                    f"{', '.join(layer.kind for layer in run.blueprint_layers)}.")


def review(source: Source, candidate: Candidate, layer: Layer) -> str:
    files = "\n".join(f"  {file.path} ({file.size} bytes)" for file in candidate.files)
    warnings = "\n".join(f"- {warning}" for warning in candidate.warnings) or "- none"
    exists = (layer.root / "skills" / candidate.name).exists()
    return (
        f"Skill: {candidate.name}\n"
        f"Description: {candidate.description or '(none)'}\n"
        f"Source: {source.tree_url(candidate.path)}\n"
        f"Level: {layer.label}{' (replaces the skill with this name there)' if exists else ''}\n"
        f"Warnings:\n{warnings}\n"
        f"Files ({len(candidate.files)}, {candidate.size} bytes):\n{files}\n\n"
        f"--- SKILL.md ---\n{truncate(candidate.text, 6000)}"
    )


async def install_skill(run: AgentRun, arguments: dict[str, Any]) -> str:
    url = text_argument(arguments, "url")
    wanted = text_argument(arguments, "skill", required=False).strip()
    default_level = "environment" if any(layer.kind == "environment" for layer in run.blueprint_layers) else "global"
    level = str(arguments.get("level") or default_level)
    if level not in LEVELS:
        raise ToolError("level is global, environment or project.")
    layer = target_layer(run, level)
    github = GitHubSkills(run.github_token)
    try:
        source, candidates = await asyncio.to_thread(preview, github, url)
    except BlueprintError as error:
        raise ToolError(str(error)) from error
    if wanted:
        matches = [c for c in candidates if wanted in (c.name, c.path, c.folder_name)]
    else:
        matches = candidates
    if len(matches) != 1:
        listing = "\n".join(f"- {c.name} ({c.path or '/'}): {c.description[:120]}" for c in candidates)
        prefix = f"No skill named '{wanted}' at this link." if wanted and not matches else "This link holds several skills."
        return f"{prefix} Call install_skill again with skill set to one of these names:\n{listing}"
    candidate = matches[0]
    if candidate.error:
        raise ToolError(candidate.error)
    summary = f"Install skill {candidate.name} from {source.repository}@{source.commit[:7]} into {layer.label}"
    decision = await ask_approval(run, "install_skill", summary, review(source, candidate, layer), guard_reason(run))
    if decision != "approve":
        raise ToolError("The user declined installing this skill. Do not retry it; ask the user what to do instead.")
    try:
        await asyncio.to_thread(
            install_at_commit, github, layer, url, source.ref, source.commit, [(candidate.path, candidate.name)]
        )
    except BlueprintError as error:
        raise ToolError(str(error)) from error
    return (
        f"Installed the skill '{candidate.name}' in {layer.label} from {source.repository}@{source.commit[:7]}. "
        "It is available from the next message on, through load_skill."
    )


SKILL_TOOLS = [
    Tool(
        "install_skill",
        "Install a skill from a GitHub link (a repository, a folder or a SKILL.md). The user reviews the skill and "
        "approves the install. When the link holds several skills, name one in skill. level is global, environment "
        "(default) or project.",
        schema(
            {"url": STRING, "skill": STRING, "level": {"type": "string", "enum": list(LEVELS)}},
            ["url"],
        ),
        "auto",
        install_skill,
        scope="always",
        asks_itself=True,
    ),
]
