"""The token view of a conversation: where its tokens went and which hints follow from it."""

from dataclasses import dataclass

from app.agent.outputs import OutputStore
from app.core.context_breakdown import ContextBreakdown, ContextInputs, context_breakdown
from app.core.token_hints import ContextFacts, Hint, HintFacts, session_hints
from app.core.token_usage import SessionTokens, session_tokens

TOOL_RESULTS_KEY = "messages_tool_results"
YAAP_TOOLS_KEY = "tools_yaap"
JOINED_TOOLS_KEY = "tools_joined"
# Joined tool definitions beyond the items a category lists; they count towards the total, not towards a server.
UNLISTED_TOOLS = "other"


@dataclass(frozen=True)
class TokenReport:
    tokens: SessionTokens
    hints: list[Hint]


def context_facts(breakdown: ContextBreakdown) -> ContextFacts:
    """What the hints need of the context breakdown: its size, its tool results and its tool definitions by group."""
    by_key = {category.key: category for category in breakdown.categories}
    results = by_key.get(TOOL_RESULTS_KEY)
    definitions: dict[str, int] = {}
    yaap = by_key.get(YAAP_TOOLS_KEY)
    if yaap is not None:
        definitions["yaap"] = yaap.tokens
    joined = by_key.get(JOINED_TOOLS_KEY)
    if joined is not None:
        for item in joined.items:
            if item.tool_group is not None:
                definitions[item.tool_group] = definitions.get(item.tool_group, 0) + item.tokens
        unlisted = joined.tokens - sum(item.tokens for item in joined.items)
        if unlisted > 0:
            definitions[UNLISTED_TOOLS] = unlisted
    return ContextFacts(breakdown.total_tokens, results.tokens if results is not None else 0, definitions)


def token_report(inputs: ContextInputs, outputs: OutputStore | None) -> TokenReport:
    """Where the tokens of the conversation went, with the hints on where the session lost some."""
    tokens = session_tokens(inputs.session, inputs.conversation.id, outputs)
    context = context_facts(context_breakdown(inputs))
    return TokenReport(tokens, session_hints(HintFacts(tokens.file_reads, tokens.gaps, context)))
