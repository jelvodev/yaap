"""Context assembly: parts 1–4 of the system prompt, stable first so the prompt cache holds."""

from dataclasses import replace

from app.core.prompts import Languages, base_prompt
from app.blueprint.layers import Item, Layer, active_items
from app.projects.memory import has_entries
from app.providers.base import ContextSource, SystemPart
from app.storage.models import Project

KIND_LABELS = {"standard": "standard", "engineering": "engineering standard"}


def instructions_part(items: list[Item]) -> str | None:
    instructions = next((item for item in items if item.kind == "instructions"), None)
    if instructions is None or not instructions.document.body.strip():
        return None
    return f"# General instructions\n\n{instructions.document.body.strip()}"


def standards_part(items: list[Item]) -> str | None:
    standards = [item for item in items if item.kind in KIND_LABELS]
    if not standards:
        return None
    sections = [
        "# Standards\n\nThese standards apply to all work. Each lists its summary and the rules that must always be "
        "followed. The full text of a standard can be loaded with the load_standard tool when tools are available."
    ]
    for item in standards:
        lines = [f"## {item.name} ({KIND_LABELS[item.kind]})"]
        if item.summary:
            lines.append(item.summary)
        lines += [f"- {rule}" for rule in item.rules]
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def index_part(items: list[Item]) -> str | None:
    entries = [item for item in items if item.kind in ("skill", "command", "agent")]
    if not entries:
        return None
    lines = ["# Skills, commands and agents\n"]
    for item in entries:
        description = f": {item.summary}" if item.summary else ""
        lines.append(f"- {item.kind} `{item.name}`{description}")
    return "\n".join(lines)


def project_part(
    project: Project,
    memory: str = "",
    secret_names: list[str] | None = None,
    knowledge_names: list[str] | None = None,
    work_language: str = "English",
    host_folder: str | None = None,
) -> str:
    """Part 5 for an agent run in a project: where the agent works, how it uses YAAP's tools, and its memory.
    host_folder is the project folder on the user's computer, named when YAAP knows it."""
    on_host = f" On the user's computer this folder is `{host_folder}`." if host_folder else ""
    text = (
        f'# Project\n\nYou work in the project "{project.name}". YAAP\'s tools act on its folder; every path is '
        f"relative to the project root.{on_host}\n"
        "- Look before you change: list and read the relevant files first.\n"
        "- Change files with edit_file for small changes and write_file for new or rewritten files.\n"
        "- run_command, start_process, git_push, create_pull_request, web_fetch and install_skill wait for the user's approval. When the "
        "user declines, do not retry; ask what they want instead.\n"
        "- Run a server or watcher that keeps running with start_process, not run_command; check it with "
        "read_process_output and stop it with stop_process when it is no longer needed.\n"
        f"- Commit only when the user asks for it, with a clear message in {work_language}. Never push unless asked.\n"
        "- Load the full text of a standard with load_standard when its rules are not enough for the task.\n"
        "- Hand well-defined work (implementing a plan, fixing given failures, a series of edits) to a cheaper "
        "worker with delegate; keep planning, review and decisions yourself, and check what it changed.\n"
        "- Record decisions, conventions and lessons that later conversations need with update_memory."
    )
    if knowledge_names:
        shown = ", ".join(knowledge_names[:20]) + (f" and {len(knowledge_names) - 20} more" if len(knowledge_names) > 20 else "")
        text += (
            f"\n- The project has knowledge documents, reference material the user added: {shown}. Find what they say "
            "with search_project and read one with read_knowledge."
        )
    if secret_names:
        text += (
            "\n- Secrets you may use, only in the command of run_command or start_process, as {{secret:NAME}}: "
            + ", ".join(secret_names)
            + ". YAAP inserts the value when the command runs and hides it in every output; it becomes a shell "
            "variable, so use it unquoted or in double quotes, not in single quotes. Never write a secret into a file."
        )
    if has_entries(memory):
        text += f"\n\n## Project memory\n\n{memory.strip()}"
    return text


def build_system_parts(
    layers: list[Layer],
    project_text: str | None = None,
    agent_text: str | None = None,
    languages: Languages | None = None,
) -> list[SystemPart]:
    """Parts 1-5 of the context. Part 5 is the agent's role, then the project."""
    items = active_items(layers)
    parts = [SystemPart(text=base_prompt(languages), source="base")]
    sources: tuple[tuple[str | None, ContextSource], ...] = (
        (instructions_part(items), "rules"),
        (standards_part(items), "standards"),
        (index_part(items), "skills"),
        (agent_text, "agent"),
        (project_text, "project"),
    )
    for text, source in sources:
        if text:
            parts.append(SystemPart(text=text, source=source))
    # One cache breakpoint after the stable parts.
    parts[-1] = replace(parts[-1], cacheable=True)
    return parts
