"""Where the tokens go (tokens): the context of a conversation now, and where its tokens went over the session.

Every route is under its conversation and reads it through the conversation APIs' own dependency, so a view is open
exactly where its conversation is.
"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.agent.outputs import OutputStore
from app.api.conversation_checks import conversation_layers
from app.api.conversations import ConversationDependency
from app.api.dependencies import (
    BlueprintDirDependency,
    CipherDependency,
    DataDirDependency,
    ProviderRegistryDependency,
    SessionDependency,
    WorkspaceDirDependency,
)
from app.core.context_breakdown import (
    ContextBreakdown,
    ContextInputs,
    LastReply,
    MemoryRecall,
    context_breakdown,
)
from app.core.context_categories import ContextCategory, ContextCategoryKey, ContextGroupKey, ContextItem
from app.core.token_hints import Hint, HintKind
from app.core.token_report import TokenReport, token_report
from app.core.token_usage import ReplyTokens, TokenRow, TokenRowKind, ToolResultTokens
from app.core.why import WhyOut, why_out
from app.secrets.service import resolve_secrets

router = APIRouter(tags=["tokens"])


class ContextItemOut(BaseModel):
    label: str
    tokens: int
    # The step of the trace that made this item; empty when it cannot be matched reliably.
    step_id: int | None
    run_id: int | None


class ContextCategoryOut(BaseModel):
    key: ContextCategoryKey
    group: ContextGroupKey
    label: str
    tokens: int
    # The largest first, at most eight.
    items: list[ContextItemOut]


class LastReplyOut(BaseModel):
    input_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    output_tokens: int
    # Share of the prompt read from the cache, 0 to 1.
    cache_share: float
    cost: float | None


class RecalledEntryOut(BaseModel):
    section: str
    text: str
    # recent: among the newest of its section; related: close to the user's message.
    reason: Literal["recent", "related"]


class MemoryRecallOut(BaseModel):
    """What of the project memory went into the prompt."""

    whole: bool
    total_entries: int
    entries: list[RecalledEntryOut]


class ContextBreakdownOut(BaseModel):
    categories: list[ContextCategoryOut]
    # The context in use: exact from the last reply's usage; the sizes of the categories add up to it.
    total_tokens: int
    # The split into categories is estimated (about four characters per token), scaled to the total.
    estimated: bool
    # snapshot: the last model call's own context; assembly: what would be sent now.
    basis: Literal["snapshot", "assembly"]
    # The last call had a snapshot but it was pruned; the assembly is shown instead.
    snapshot_gone: bool
    step_id: int | None
    # Tools that wait for find_tools instead of being offered.
    deferred_tools: int
    limit_tokens: int | None
    # Where YAAP compacts by itself; empty for Claude Code and Codex, which compact their own sessions.
    compact_at_tokens: int | None
    memory: MemoryRecallOut | None = None
    # Summaries: how many messages the current summary covers, and whether "Summarise now" can run.
    summarised_messages: int = 0
    can_summarise: bool = False
    context_tokens: int | None
    context_window: int | None
    last_reply: LastReplyOut | None
    # Estimated tokens saved by shortening old tool output, which the categories count at its shortened size.
    trimmed_tool_tokens: int = 0


class TokenRowOut(BaseModel):
    kind: TokenRowKind
    # "Reply" for a reply; a worker's task or name; the label of a utility task; "Session" for the total.
    label: str
    message_id: int | None
    run_id: int | None
    started_at: datetime | None
    cache_read_tokens: int
    cache_write_tokens: int
    # Uncached input.
    input_tokens: int
    # Includes thinking: no separate thinking count is stored.
    output_tokens: int
    # None when not paid per token (a subscription).
    cost: float | None


class ReplyTokensOut(BaseModel):
    reply: TokenRowOut
    workers: list[TokenRowOut]
    utility: list[TokenRowOut]


class ToolResultTokensOut(BaseModel):
    step_id: int
    run_id: int
    message_id: int | None
    tool: str
    summary: str | None
    # Estimated from the stored output: characters / 4.
    tokens: int


class HintOut(BaseModel):
    kind: HintKind
    text: str
    remedy: str
    roadmap_item: int
    why: WhyOut | None = None


class TokenUsageOut(BaseModel):
    """Where the tokens of a conversation went. Thinking is counted inside the output tokens, because no separate
    thinking count is stored."""

    # Oldest first.
    replies: list[ReplyTokensOut]
    # Utility calls that belong to no reply.
    other_utility: list[TokenRowOut]
    # The ten largest stored tool results, largest first.
    top_tool_results: list[ToolResultTokensOut]
    # Cache read over all prompt tokens of the replies and workers; None without prompt tokens.
    cache_hit_rate: float | None
    totals: TokenRowOut
    hints: list[HintOut]


def item_out(item: ContextItem) -> ContextItemOut:
    return ContextItemOut(label=item.label, tokens=item.tokens, step_id=item.step_id, run_id=item.run_id)


def category_out(category: ContextCategory) -> ContextCategoryOut:
    return ContextCategoryOut(
        key=category.key,
        group=category.group,
        label=category.label,
        tokens=category.tokens,
        items=[item_out(item) for item in category.items],
    )


def memory_out(memory: MemoryRecall | None) -> MemoryRecallOut | None:
    if memory is None:
        return None
    entries = [RecalledEntryOut(section=e.section, text=e.text, reason=e.reason) for e in memory.entries]
    return MemoryRecallOut(whole=memory.whole, total_entries=memory.total_entries, entries=entries)


def last_reply_out(reply: LastReply | None) -> LastReplyOut | None:
    return LastReplyOut(**vars(reply)) if reply is not None else None


def breakdown_out(breakdown: ContextBreakdown) -> ContextBreakdownOut:
    return ContextBreakdownOut(
        categories=[category_out(category) for category in breakdown.categories],
        total_tokens=breakdown.total_tokens,
        estimated=breakdown.estimated,
        basis=breakdown.basis,
        snapshot_gone=breakdown.snapshot_gone,
        step_id=breakdown.step_id,
        deferred_tools=breakdown.deferred_tools,
        limit_tokens=breakdown.limit_tokens,
        compact_at_tokens=breakdown.compact_at_tokens,
        memory=memory_out(breakdown.memory),
        summarised_messages=breakdown.summarised_messages,
        can_summarise=breakdown.can_summarise,
        context_tokens=breakdown.context_tokens,
        context_window=breakdown.context_window,
        last_reply=last_reply_out(breakdown.last_reply),
        trimmed_tool_tokens=breakdown.trimmed_tool_tokens,
    )


def token_row_out(row: TokenRow) -> TokenRowOut:
    return TokenRowOut(**vars(row))


def reply_tokens_out(item: ReplyTokens) -> ReplyTokensOut:
    return ReplyTokensOut(
        reply=token_row_out(item.reply),
        workers=[token_row_out(row) for row in item.workers],
        utility=[token_row_out(row) for row in item.utility],
    )


def tool_result_out(result: ToolResultTokens) -> ToolResultTokensOut:
    return ToolResultTokensOut(**vars(result))


def hint_out(hint: Hint) -> HintOut:
    return HintOut(
        kind=hint.kind,
        text=hint.text,
        remedy=hint.remedy,
        roadmap_item=hint.roadmap_item,
        why=why_out(hint.why) if hint.why is not None else None,
    )


def report_out(report: TokenReport) -> TokenUsageOut:
    tokens = report.tokens
    return TokenUsageOut(
        replies=[reply_tokens_out(item) for item in tokens.replies],
        other_utility=[token_row_out(row) for row in tokens.other_utility],
        top_tool_results=[tool_result_out(result) for result in tokens.top_tool_results],
        cache_hit_rate=tokens.cache_hit_rate,
        totals=token_row_out(tokens.totals),
        hints=[hint_out(hint) for hint in report.hints],
    )


def context_inputs(
    request: Request,
    conversation: ConversationDependency,
    session: SessionDependency,
    providers: ProviderRegistryDependency,
    cipher: CipherDependency,
    blueprint_dir: BlueprintDirDependency,
    workspace_dir: WorkspaceDirDependency,
    data_dir: DataDirDependency,
) -> ContextInputs:
    layers = conversation_layers(
        session, blueprint_dir, workspace_dir, conversation.environment_id, conversation.project_id
    )
    return ContextInputs(
        session=session,
        providers=providers,
        conversation=conversation,
        layers=layers,
        workspace_dir=workspace_dir,
        data_dir=data_dir,
        indexer=getattr(request.app.state, "indexer", None),
        secrets=resolve_secrets(session, cipher, conversation.environment_id, conversation.project_id),
    )


@router.get("/conversations/{conversation_id}/context", response_model=ContextBreakdownOut)
def context(
    request: Request,
    conversation: ConversationDependency,
    session: SessionDependency,
    providers: ProviderRegistryDependency,
    cipher: CipherDependency,
    blueprint_dir: BlueprintDirDependency,
    workspace_dir: WorkspaceDirDependency,
    data_dir: DataDirDependency,
) -> ContextBreakdownOut:
    """What the conversation's context holds now, by category, with the exact total of the last reply."""
    inputs = context_inputs(
        request, conversation, session, providers, cipher, blueprint_dir, workspace_dir, data_dir
    )
    return breakdown_out(context_breakdown(inputs))


@router.get("/conversations/{conversation_id}/token-usage", response_model=TokenUsageOut)
def token_usage(
    request: Request,
    conversation: ConversationDependency,
    session: SessionDependency,
    providers: ProviderRegistryDependency,
    cipher: CipherDependency,
    blueprint_dir: BlueprintDirDependency,
    workspace_dir: WorkspaceDirDependency,
    data_dir: DataDirDependency,
) -> TokenUsageOut:
    """Where the tokens of the conversation went, per reply, with the hints on where the session lost some.

    Thinking is counted inside the output tokens: no separate thinking count is stored.
    """
    inputs = context_inputs(
        request, conversation, session, providers, cipher, blueprint_dir, workspace_dir, data_dir
    )
    return report_out(token_report(inputs, OutputStore(data_dir, conversation.id)))
