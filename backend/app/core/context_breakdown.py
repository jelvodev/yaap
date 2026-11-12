"""What a conversation's context holds now: the categories of the last model call, with the exact total.

The split comes from the last model call's context snapshot when there is one, else from the context as it would be
assembled now. The total is exact (the last call's usage); the split is estimated and scaled to it. With Claude Code
and Codex YAAP knows only its own parts, so what is left over shows as the CLI's own prompt and history.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.tool_catalog import searchable_tool_count
from app.agents.definitions import agent_part, find_agent
from app.blueprint.context import index_part, instructions_part, project_part, standards_part
from app.blueprint.layers import Layer, active_items
from app.core.chat_history import is_part_of_history, load_branch
from app.core.context_categories import (
    ContextCategory,
    Draft,
    StepRef,
    finish_categories,
    remainder_category,
    space_categories,
    split_messages,
    split_system,
    split_tools,
)
from app.core.context_control import ContextControl, resolve_context_control
from app.core.prompts import base_prompt
from app.core.search import recall_project_memory
from app.core.search_index import message_text
from app.core.settings import resolve_languages
from app.core.summaries import (
    SUMMARY_HEADING,
    carried_texts,
    cover_point,
    latest_summary,
    summary_block,
    yaap_compacts,
)
from app.core.trimming import latest_trim, shorten
from app.core.turn_context import memory_parts, turn_context
from app.projects.memory import Recall, read_memory
from app.projects.service import project_path
from app.providers.base import ToolDefinition
from app.providers.registry import ProviderRegistry
from app.storage.models import AgentRunRecord, Conversation, Message, Project, RunStep, UsageRecord
from app.tracing.snapshots import JsonValue, SnapshotStore, cleaned, trace_dir

Basis = Literal["snapshot", "assembly"]
Blocks = list[dict[str, Any]]
Messages = list[tuple[str, Blocks]]


@dataclass(frozen=True)
class RecalledEntry:
    section: str
    text: str
    # recent: among the newest of its section; related: close to the user's message.
    reason: Literal["recent", "related"]


@dataclass(frozen=True)
class MemoryRecall:
    """What of the project memory went into the prompt."""

    whole: bool
    total_entries: int
    entries: list[RecalledEntry]


@dataclass(frozen=True)
class LastReply:
    input_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    output_tokens: int
    # Share of the prompt read from the cache, 0 to 1.
    cache_share: float
    cost: float | None


@dataclass(frozen=True)
class ContextBreakdown:
    categories: list[ContextCategory]
    # The context in use: exact from the last call's usage, else the sum of the estimates.
    total_tokens: int
    # Whether the split is estimated (the total is exact once a reply has used it).
    estimated: bool
    basis: Basis
    # The last model call had a snapshot, but it or a part of it was pruned; the assembly is shown instead.
    snapshot_gone: bool
    # The model step whose snapshot was used.
    step_id: int | None
    # Tools that wait for find_tools instead of being offered.
    deferred_tools: int
    limit_tokens: int | None
    # Where YAAP compacts by itself; empty when it does not (Claude Code, Codex, or compacting is off).
    compact_at_tokens: int | None
    memory: MemoryRecall | None
    summarised_messages: int
    can_summarise: bool
    context_tokens: int | None
    context_window: int | None
    last_reply: LastReply | None


@dataclass(frozen=True)
class ContextInputs:
    """What the breakdown needs of the running application."""

    session: Session
    providers: ProviderRegistry
    conversation: Conversation
    layers: list[Layer]
    workspace_dir: Path
    data_dir: Path
    indexer: object | None
    # The environment's secret values: what a snapshot masks, the assembly masks too.
    secrets: dict[str, str]


@dataclass(frozen=True)
class RequestParts:
    """The parts of one model request, as stored in a snapshot or assembled now."""

    system: list[tuple[str, str]]
    tools: list[ToolDefinition]
    messages: Messages


@dataclass(frozen=True)
class SnapshotLookup:
    step_id: int | None
    parts: RequestParts | None
    gone: bool


def last_main_usage(session: Session, conversation_id: int) -> UsageRecord | None:
    return session.scalar(
        select(UsageRecord)
        .where(UsageRecord.conversation_id == conversation_id, UsageRecord.purpose == "main")
        .order_by(UsageRecord.id.desc())
    )


def last_reply_of(usage: UsageRecord) -> LastReply:
    prompt = usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens
    return LastReply(
        input_tokens=usage.input_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        output_tokens=usage.output_tokens,
        cache_share=round(usage.cache_read_tokens / prompt, 3) if prompt else 0.0,
        cost=usage.cost,
    )


def context_used(usage: UsageRecord | None) -> int | None:
    """The context the last reply used: the provider's count, else everything the call took in and gave out."""
    if usage is None:
        return None
    if usage.context_tokens is not None:
        return usage.context_tokens
    return usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens + usage.output_tokens


def tool_definition(item: JsonValue) -> ToolDefinition | None:
    if not isinstance(item, dict):
        return None
    schema = item.get("input_schema")
    description = str(item.get("description") or "")
    return ToolDefinition(str(item.get("name") or ""), description, dict(schema) if isinstance(schema, dict) else {})


def snapshot_message(message: JsonValue) -> tuple[str, Blocks] | None:
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(message, dict) or not isinstance(content, list):
        return None
    blocks: Blocks = [dict(block) for block in content if isinstance(block, dict)]
    return str(message.get("role") or ""), blocks


def snapshot_parts(store: SnapshotStore, digest: str) -> RequestParts | None:
    """A snapshot as request parts; None when it or one of its parts was pruned."""
    stored = store.load(digest)
    if stored is None or not stored.complete:
        return None
    definitions = [tool_definition(item) for item in (stored.tools if isinstance(stored.tools, list) else [])]
    messages = [item for item in map(snapshot_message, stored.messages) if item is not None]
    system = [(part.source, part.text) for part in stored.system]
    return RequestParts(system, [d for d in definitions if d is not None], messages)


def last_snapshot(session: Session, store: SnapshotStore, conversation_id: int) -> SnapshotLookup:
    """The context snapshot of the conversation's last top-level model call."""
    row = session.execute(
        select(RunStep.id, RunStep.snapshot)
        .join(AgentRunRecord, RunStep.run_id == AgentRunRecord.id)
        .where(
            AgentRunRecord.conversation_id == conversation_id,
            AgentRunRecord.parent_run_id.is_(None),
            RunStep.kind == "model",
        )
        .order_by(RunStep.id.desc())
        .limit(1)
    ).first()
    if row is None or row.snapshot is None:
        return SnapshotLookup(None, None, False)
    parts = snapshot_parts(store, row.snapshot)
    return SnapshotLookup(row.id if parts else None, parts, parts is None)


def tool_step_links(session: Session, conversation_id: int, branch: list[Message]) -> dict[str, StepRef]:
    """Tool call id -> the step of that call, for the replies of this conversation's branch.

    Linked only when a reply's tool steps are as many as its tool call blocks, so a step is never guessed.
    """
    runs = dict(
        session.execute(
            select(AgentRunRecord.message_id, AgentRunRecord.id)
            .where(
                AgentRunRecord.conversation_id == conversation_id,
                AgentRunRecord.parent_run_id.is_(None),
                AgentRunRecord.message_id.is_not(None),
            )
            .order_by(AgentRunRecord.id)
        ).all()
    )
    steps: dict[int, list[int]] = {}
    rows = session.execute(
        select(RunStep.run_id, RunStep.id)
        .where(RunStep.run_id.in_(set(runs.values())), RunStep.kind == "tool")
        .order_by(RunStep.run_id, RunStep.position)
    ).all()
    for run_id, step_id in rows:
        steps.setdefault(run_id, []).append(step_id)
    links: dict[str, StepRef] = {}
    for message in branch:
        call_ids = [
            block.get("id") for block in message.content if block.get("type") == "tool_call" and block.get("id")
        ]
        run_steps = steps.get(runs.get(message.id, -1), [])
        if call_ids and len(call_ids) == len(run_steps):
            run_id = runs[message.id]
            for call_id, step_id in zip(call_ids, run_steps, strict=True):
                links[call_id] = StepRef(run_id, step_id)
    return links


def trimmed(blocks: Blocks) -> Blocks:
    """The blocks with the output of tool calls shortened, as old tool output is once YAAP trims."""
    return [
        {**block, "output": shorten(block["output"])} if isinstance(block.get("output"), str) else block
        for block in blocks
    ]


def masked_blocks(blocks: Blocks, secrets: dict[str, str]) -> Blocks:
    """The stored blocks with secret values masked, as a snapshot holds them; labels are made from these."""
    masked = cleaned(blocks, secrets)
    return [block for block in masked if isinstance(block, dict)] if isinstance(masked, list) else []


def assembled_messages(
    session: Session, branch: list[Message], recalled: Recall | None, secrets: dict[str, str]
) -> Messages:
    """The branch as the model would get it now: from the summary on, old tool output shortened."""
    summary_index = latest_summary(branch)
    trim_index = latest_trim(branch)
    messages: Messages = []
    start = 0
    if summary_index is not None:
        start = summary_index + 1
        text = (summary_block(branch[summary_index]) or {}).get("text", "")
        kept = carried_texts(branch[:start])
        blocks = [{"type": "text", "text": f"{SUMMARY_HEADING}\n\n{text}"}]
        messages.append(("user", [*blocks, *({"type": "text", "text": item} for item in kept)]))
    for position, message in enumerate(branch[start:], start):
        if not is_part_of_history(message):
            continue
        is_old = trim_index is not None and position <= trim_index
        blocks = masked_blocks(list(message.content), secrets)
        messages.append((message.role, trimmed(blocks) if is_old else blocks))
    recalled_turn = memory_parts(recalled)[1] if recalled is not None else None
    messages.append(("user", [{"type": "text", "text": turn_context(session, recalled_turn)}]))
    return messages


def assembled_parts(
    inputs: ContextInputs, branch: list[Message], project: Project | None, recalled: Recall | None
) -> RequestParts:
    """The context as it would be assembled now; its tools are not known without a run."""
    conversation, session = inputs.conversation, inputs.session
    items = active_items(inputs.layers)
    agent = find_agent(inputs.layers, conversation.agent) if conversation.agent else None
    languages = resolve_languages(session, conversation.environment_id, conversation.project_id)
    project_text = project_part(project, memory_parts(recalled)[0]) if project and recalled is not None else None
    texts = [
        ("base", base_prompt(languages)),
        ("rules", instructions_part(items)),
        ("standards", standards_part(items)),
        ("skills", index_part(items)),
        ("agent", agent_part(agent, inputs.layers) if agent else None),
        ("project", project_text),
    ]
    system = [(source, text) for source, text in texts if text]
    return RequestParts(system, [], assembled_messages(session, branch, recalled, inputs.secrets))


def memory_recall(inputs: ContextInputs, project: Project | None, branch: list[Message]) -> Recall | None:
    if project is None:
        return None
    question = next((message_text(message) for message in reversed(branch) if message.role == "user"), "")
    memory = read_memory(project_path(inputs.workspace_dir, project))
    return recall_project_memory(
        inputs.session, inputs.indexer, inputs.conversation.environment_id, project.id, memory, question
    )


def recall_view(recalled: Recall | None) -> MemoryRecall | None:
    if recalled is None or not recalled.total:
        return None
    entries = [RecalledEntry(entry.section, entry.text, "recent") for entry in recalled.recent]
    entries += [RecalledEntry(entry.section, entry.text, "related") for entry in recalled.related]
    return MemoryRecall(whole=recalled.whole, total_entries=recalled.total, entries=entries)


def compact_point(control: ContextControl) -> int | None:
    """The context size at which YAAP summarises by itself; None when it does not."""
    if control.compacts_itself or not control.auto_compact or not control.limit:
        return None
    return control.limit * control.compact_at // 100


def deferred_tool_count(parts: RequestParts) -> int:
    return sum(searchable_tool_count(text) for _, text in parts.system)


def categories_of(
    parts: RequestParts, links: dict[str, StepRef], total: int | None, control: ContextControl
) -> tuple[list[ContextCategory], int]:
    """The categories, scaled to total (else to their own sum), with the CLI remainder or the buffer and free space."""
    drafts: list[Draft] = [
        *split_system(parts.system),
        *split_tools(parts.tools),
        *split_messages(parts.messages, links),
    ]
    estimated = sum(draft.tokens for draft in drafts)
    used = total if total is not None else estimated
    # A CLI keeps YAAP's parts as estimated unless they already exceed the exact total: then they are scaled down to it
    # and nothing is left over.
    scale = not control.compacts_itself or estimated > used
    categories = finish_categories(drafts, used, scale=scale)
    if control.compacts_itself:
        categories.append(remainder_category(used, categories))
    return [*categories, *space_categories(used, control.limit, compact_point(control))], used


def context_breakdown(inputs: ContextInputs) -> ContextBreakdown:
    """Where the context goes: from the last call's snapshot, else from what would be assembled now."""
    session, conversation = inputs.session, inputs.conversation
    branch = load_branch(session, conversation.id, conversation.head_message_id)
    project = session.get(Project, conversation.project_id) if conversation.project_id else None
    recalled = memory_recall(inputs, project, branch)
    control = resolve_context_control(session, inputs.providers, conversation)
    usage = last_main_usage(session, conversation.id)
    lookup = last_snapshot(session, SnapshotStore(trace_dir(inputs.data_dir)), conversation.id)
    parts = lookup.parts or assembled_parts(inputs, branch, project, recalled)
    links = tool_step_links(session, conversation.id, branch)
    context_tokens = context_used(usage)
    categories, total = categories_of(parts, links, context_tokens, control)
    summary_index = latest_summary(branch)
    model = inputs.providers.find_model(conversation.model)
    return ContextBreakdown(
        categories=categories,
        total_tokens=total,
        estimated=True,
        basis="snapshot" if lookup.parts else "assembly",
        snapshot_gone=lookup.gone,
        step_id=lookup.step_id,
        deferred_tools=deferred_tool_count(parts),
        limit_tokens=control.limit,
        compact_at_tokens=compact_point(control),
        memory=recall_view(recalled),
        summarised_messages=summary_index + 1 if summary_index is not None else 0,
        can_summarise=yaap_compacts(session, inputs.providers, conversation) and cover_point(branch) is not None,
        context_tokens=context_tokens,
        context_window=model.context_window if model else None,
        last_reply=last_reply_of(usage) if usage else None,
    )
