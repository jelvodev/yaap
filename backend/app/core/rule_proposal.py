"""A rule proposed from a correction the user made: the prompt, the model's answer read leniently, and a fallback.

The utility model drafts one lasting rule for the standards from a message of the user and the reply it corrected.
Without a usable answer the draft is the user's own text, so the user always has something to review. Nothing is
written here; the user reviews the draft first.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.blueprint.builtin import render, shipped_instructions
from app.blueprint.layers import active_items
from app.blueprint.levels import layers_for
from app.core.builtin_prompts import utility_instructions
from app.core.settings import resolve_languages
from app.core.utility import UtilityContext, UtilitySkipped, run_utility_or_skip
from app.storage.models import Conversation, Environment, Message, Project

RuleLevel = Literal["global", "environment", "project"]
LEVELS: tuple[RuleLevel, ...] = ("global", "environment", "project")
MAX_RULE_CHARS = 500
MAX_TEXT_CHARS = 3000
RULE_MAX_TOKENS = 300

# The answer format that parse_rule_proposal() reads; the instructions before it may be replaced.
RULE_ANSWER = (
    'Answer with JSON only: {"rule": "...", "level": "global" | "environment" | "project", "path": "..." or null}\n'
    "- rule: one short imperative sentence in {language}, general enough to apply next time, without details of this "
    "task.\n"
    "- level: project when the rule is about this project only ({project}), environment when it is about this way of "
    "working ({environment}), global when it should hold everywhere.\n"
    "- path: an existing standard from the list below that the rule clearly belongs to, or null."
)


class RuleProposalError(Exception):
    """The message cannot become a rule; the text says why."""


@dataclass(frozen=True)
class RuleDraft:
    rule: str
    level: RuleLevel
    path: str | None
    # False when no model drafted it and the draft is the user's own text.
    from_model: bool
    # Why no model drafted it, when the model policy skipped the task.
    skipped: str | None = None


@dataclass(frozen=True)
class RuleSituation:
    """What the prompt needs to know about the correction."""

    language: str
    project: str | None
    environment: str
    standards: list[str]
    reply: str
    correction: str


def rule_prompt(situation: RuleSituation, instructions: str | None = None) -> str:
    """What the utility model is asked: the instructions, which the user may replace, then the answer format."""
    lead = instructions if instructions is not None else shipped_instructions("learner", "Rule")
    asked = render(
        RULE_ANSWER,
        {"language": situation.language, "project": situation.project or "none", "environment": situation.environment},
    )
    return (
        f"{lead}\n\n{asked}\n\nStandards: {'; '.join(situation.standards) or 'none'}\n\n"
        f"The assistant's reply that was corrected:\n{situation.reply[:MAX_TEXT_CHARS] or '(none)'}\n\n"
        f"The user's correction:\n{situation.correction[:MAX_TEXT_CHARS]}"
    )


def parse_rule_proposal(answer: str | None) -> dict[str, Any] | None:
    if not answer:
        return None
    start, end = answer.find("{"), answer.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        value = json.loads(answer[start : end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) and isinstance(value.get("rule"), str) and value["rule"].strip() else None


def text_of(message: Message) -> str:
    return " ".join(block.get("text", "") for block in message.content if block.get("type") == "text").strip()


def situation_of(
    session: Session, conversation: Conversation, message_id: int, blueprint_dir: Path, workspace_dir: Path
) -> tuple[RuleSituation, Project | None]:
    """The correction and its surroundings; raises RuleProposalError when the message cannot become a rule."""
    message = session.get(Message, message_id)
    if message is None or message.conversation_id != conversation.id or message.role != "user":
        raise RuleProposalError("Only your own messages can become a rule.")
    correction = text_of(message)
    if not correction:
        raise RuleProposalError("This message has no text to turn into a rule.")
    parent = session.get(Message, message.parent_id) if message.parent_id else None
    reply = text_of(parent) if parent is not None and parent.role == "assistant" else ""
    environment = session.get_one(Environment, conversation.environment_id)
    project = session.get(Project, conversation.project_id) if conversation.project_id else None
    layers = layers_for(blueprint_dir, workspace_dir, environment, project)
    standards = [
        f"{item.path} ({item.summary})" if item.summary else item.path
        for item in active_items(layers)
        if item.kind in ("standard", "engineering")
    ]
    language = resolve_languages(session, environment.id, project.id if project else None).work
    project_name = project.name if project else None
    return RuleSituation(language, project_name, environment.name, standards, reply, correction), project


def draft_from(answer: str | UtilitySkipped | None, correction: str, project_exists: bool) -> RuleDraft:
    """The draft from the model's answer, or from the correction itself when the answer holds no rule."""
    skipped = answer.message if isinstance(answer, UtilitySkipped) else None
    proposal = parse_rule_proposal(answer if isinstance(answer, str) else None)
    fallback_level: RuleLevel = "project" if project_exists else "environment"
    if proposal is None:
        return RuleDraft(correction[:MAX_RULE_CHARS], fallback_level, None, False, skipped)
    level: RuleLevel = proposal["level"] if proposal.get("level") in LEVELS else fallback_level
    if level == "project" and not project_exists:
        level = "environment"
    path = proposal.get("path") if isinstance(proposal.get("path"), str) and proposal.get("path") else None
    return RuleDraft(" ".join(proposal["rule"].split())[:MAX_RULE_CHARS], level, path, True)


async def draft_rule(
    context: UtilityContext,
    session: Session,
    conversation: Conversation,
    message_id: int,
    blueprint_dir: Path,
    workspace_dir: Path,
) -> RuleDraft:
    """A rule drafted from one of the user's messages; raises RuleProposalError when it cannot become a rule."""
    situation, project = situation_of(session, conversation, message_id, blueprint_dir, workspace_dir)
    instructions = utility_instructions(context, conversation.environment_id, "learner", "Rule")
    answer = await run_utility_or_skip(
        context,
        conversation.environment_id,
        rule_prompt(situation, instructions),
        conversation.id,
        max_tokens=RULE_MAX_TOKENS,
        task="rule",
    )
    return draft_from(answer, situation.correction, project is not None)
