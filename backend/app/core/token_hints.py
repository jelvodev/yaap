"""Hints on where a session lost tokens. Each hint is a pure function of the facts the token view already has; a hint
points at the public roadmap item that would help and changes nothing."""

from collections import Counter
from dataclasses import dataclass
from typing import Literal

from app.core.why import Why

HintKind = Literal["repeated_read", "cold_cache", "tool_results_heavy", "mcp_share"]

# The default prompt cache lives five minutes; a reply that starts after a longer pause finds it expired.
COLD_PAUSE_SECONDS = 300
# Tool results holding more than half of the context are the first thing to trim.
TOOL_RESULTS_SHARE = 0.5
# One outside MCP server holding almost a third of the tool definitions is worth a look.
MCP_SHARE = 0.30
# A read twice is already a repeat; the text names only the few most-read files to stay short.
REPEATED_READS = 2
NAMED_PATHS = 3
MCP_PREFIX = "mcp:"

# The public roadmap item that would help with each kind of hint.
ROADMAP_ITEM: dict[HintKind, int] = {
    "repeated_read": 58,
    "cold_cache": 64,
    "tool_results_heavy": 58,
    "mcp_share": 58,
}


@dataclass(frozen=True)
class ReplyGap:
    """One reply after the first: the pause before it and its cache use."""

    pause_seconds: float
    cache_read_tokens: int
    cache_write_tokens: int


@dataclass(frozen=True)
class ContextFacts:
    """From the latest context breakdown."""

    context_tokens: int
    tool_result_tokens: int
    # By tool group: "yaap", "browser", "mcp:<server>".
    tool_definition_tokens: dict[str, int]


@dataclass(frozen=True)
class HintFacts:
    # Path -> how many times read_file read it this session.
    file_reads: dict[str, int]
    gaps: list[ReplyGap]
    context: ContextFacts | None


@dataclass(frozen=True)
class Hint:
    kind: HintKind
    # What was seen, in plain words with numbers.
    text: str
    # What would help.
    remedy: str
    # The public roadmap item that would help.
    roadmap_item: int
    why: Why | None = None


def repeated_read(facts: HintFacts) -> Hint | None:
    repeats = Counter({path: count for path, count in facts.file_reads.items() if count >= REPEATED_READS})
    if not repeats:
        return None
    # Ties keep the order of the reads, so the text is stable.
    named = repeats.most_common(NAMED_PATHS)
    text = " ".join(f"{path} was read {count} times." for path, count in named)
    why = Why(f"A file read {REPEATED_READS} times or more in a session is named as a repeated read.", "hint")
    return Hint("repeated_read", text, "Skipping a re-read of an unchanged file.", ROADMAP_ITEM["repeated_read"], why)


def cold_cache(facts: HintFacts) -> Hint | None:
    cold = [
        gap
        for gap in facts.gaps
        if gap.pause_seconds > COLD_PAUSE_SECONDS and gap.cache_read_tokens == 0 and gap.cache_write_tokens > 0
    ]
    if not cold:
        return None
    count = len(cold)
    written = sum(gap.cache_write_tokens for gap in cold)
    noun = "reply" if count == 1 else "replies"
    text = (
        f"{count} {noun} started with a cold cache after a pause of more than {COLD_PAUSE_SECONDS // 60} minutes "
        f"and wrote {written:,} tokens to it again."
    )
    remedy = "A summary or a new conversation after a long pause, before the history is sent again."
    why = Why(
        f"A pause of more than {COLD_PAUSE_SECONDS // 60} minutes finds the cache expired, so the next reply writes it "
        "again.",
        "hint",
    )
    return Hint("cold_cache", text, remedy, ROADMAP_ITEM["cold_cache"], why)


def tool_results_heavy(facts: HintFacts) -> Hint | None:
    context = facts.context
    if context is None or context.context_tokens <= 0:
        return None
    share = context.tool_result_tokens / context.context_tokens
    if share <= TOOL_RESULTS_SHARE:
        return None
    text = f"Tool results hold {round(share * 100)}% of the context ({context.tool_result_tokens:,} tokens)."
    remedy = "Leaving out what is not needed any more from old tool results."
    percent = round(TOOL_RESULTS_SHARE * 100)
    why = Why(f"Tool results over {percent}% of the context are the first thing to trim.", "hint")
    return Hint("tool_results_heavy", text, remedy, ROADMAP_ITEM["tool_results_heavy"], why)


def mcp_share(facts: HintFacts) -> Hint | None:
    context = facts.context
    if context is None:
        return None
    total = sum(context.tool_definition_tokens.values())
    if total <= 0:
        return None
    servers = {
        group.removeprefix(MCP_PREFIX): tokens
        for group, tokens in context.tool_definition_tokens.items()
        if group.startswith(MCP_PREFIX)
    }
    if not servers:
        return None
    server, tokens = max(servers.items(), key=lambda item: item[1])
    share = tokens / total
    if share <= MCP_SHARE:
        return None
    text = f"The MCP server {server} holds {round(share * 100)}% of the tool definitions ({tokens:,} tokens)."
    remedy = "Turning off the tools of that server that this work does not use."
    why = Why(f"One MCP server holding over {round(MCP_SHARE * 100)}% of the tool definitions is worth a look.", "hint")
    return Hint("mcp_share", text, remedy, ROADMAP_ITEM["mcp_share"], why)


def session_hints(facts: HintFacts) -> list[Hint]:
    """Every hint that applies, in a fixed order."""
    found = (repeated_read(facts), cold_cache(facts), tool_results_heavy(facts), mcp_share(facts))
    return [hint for hint in found if hint is not None]
