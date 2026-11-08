"""The project search tools: search earlier conversations, memory and knowledge, and read a knowledge document.
Knowledge documents count as outside content."""

from typing import Any

import anyio

from app.agent.guard import note_outside
from app.agent.runs import AgentRun
from app.agent.toolkit import STRING, Tool, ToolError, schema, text_argument
from app.core.search import MATCH_END, MATCH_START

SEARCH_SCOPES = {
    "all": ("message", "memory", "knowledge"),
    "conversations": ("message",),
    "memory": ("memory",),
    "knowledge": ("knowledge",),
}
MAX_KNOWLEDGE_READ = 50_000


async def search_project(run: AgentRun, arguments: dict[str, Any]) -> str:
    if run.searcher is None or run.project_id is None:
        raise ToolError("Search works in a project conversation.")
    query = text_argument(arguments, "query")
    scope = str(arguments.get("include") or "all")
    if scope not in SEARCH_SCOPES:
        raise ToolError("include must be all, conversations, memory or knowledge.")
    items = await anyio.to_thread.run_sync(
        run.searcher.search, run.environment_id, run.project_id, query, SEARCH_SCOPES[scope], run.conversation_id
    )
    if not items:
        return f"Nothing found for: {query}"
    lines = []
    for number, item in enumerate(items, start=1):
        snippet = " ".join(item.snippet.replace(MATCH_START, "").replace(MATCH_END, "").split())
        if item.kind == "memory":
            lines.append(f"{number}. Project memory: {snippet}")
            continue
        if item.kind == "knowledge":
            note_outside(run, f"knowledge document {item.path}", snippet)
            lines.append(f'{number}. Knowledge document "{item.path}" (read it with read_knowledge): {snippet}')
            continue
        who = "user" if item.role == "user" else "assistant"
        date = item.created_at.date().isoformat() if item.created_at else ""
        lines.append(f"{number}. Conversation \"{item.conversation_title}\" ({who}, {date}): {snippet}")
    return "\n".join(lines)


async def read_knowledge(run: AgentRun, arguments: dict[str, Any]) -> str:
    if run.searcher is None or run.project_id is None:
        raise ToolError("Project knowledge exists in a project conversation.")
    name = text_argument(arguments, "name")
    found = await anyio.to_thread.run_sync(run.searcher.knowledge, run.project_id, name)
    if found is None:
        names = await anyio.to_thread.run_sync(run.searcher.knowledge_names, run.project_id)
        raise ToolError(f"No knowledge document '{name}'. Documents: {', '.join(names) or 'none'}.")
    document, text = found
    offset = max(int(arguments.get("offset") or 0), 0)
    part = text[offset : offset + MAX_KNOWLEDGE_READ]
    note_outside(run, f"knowledge document {document}", part)
    end = offset + len(part)
    # The note goes first, so it stays visible where a long result is shortened.
    more = f"\n[Characters {offset}-{end} of {len(text)}. Continue with offset {end}.]" if end < len(text) else ""
    return f"# {document}{more}\n\n{part}"


SEARCH_TOOLS = [
    Tool(
        "search_project",
        "Search this project's earlier conversations, memory and knowledge documents by words and meaning: what was "
        "decided, tried or explained before, or what the documents say. Returns the best matches with an excerpt.",
        schema(
            {
                "query": STRING,
                "include": {
                    "type": "string",
                    "enum": ["all", "conversations", "memory", "knowledge"],
                    "description": "Default all.",
                },
            },
            ["query"],
        ),
        "auto",
        search_project,
        read_only=True,
    ),
    Tool(
        "read_knowledge",
        "Read a knowledge document of this project by name (or a unique part of it), as text, 50,000 characters at "
        "a time; use offset to continue.",
        schema({"name": STRING, "offset": {"type": "integer"}}, ["name"]),
        "auto",
        read_knowledge,
        read_only=True,
    ),
]
