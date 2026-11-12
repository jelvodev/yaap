"""A proposed eval case from a conversation that went well.

The start and the prompt come from the conversation itself (conversation_facts.py). The checks are proposed by the
utility model from the prompt, the final reply, the changed files and the project's test command, in the case file's
own format, and validated by the same reader as a case file (`cases.parse_checks`); checks that do not validate are
dropped. When no utility model answers, or none of its checks validate, a deterministic proposal takes their place:
the test command, the removed files as `exists: false`, and a rubric over the changed files. The budget is twice
what the conversation used, rounded up. Nothing is written here: the user edits the proposal and saves it.
"""

import json
import math
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy.orm import Session

from app.checkpoints.service import CheckpointStore
from app.core.settings import SETTING_DEFINITIONS, resolve_setting
from app.evals.cases import CASE_NAME, DEFAULT_COMMAND_TIMEOUT, MAX_SUMMARY_CHARS, evals_dir, parse_checks
from app.evals.conversation_facts import ConversationFacts, ConversationUsage, read_conversation
from app.evals.models import Budget, Check, CommandCheck, EvalCase, FileCheck, RubricCheck
from app.evals.project_commands import SECRET_PLACEHOLDER, TestCommand, find_test_command
from app.storage.models import Conversation

# Sends one prompt to the utility model; None when no utility model is available or it failed.
UtilityAsk = Callable[[str], Awaitable[str | None]]
ChecksFrom = Literal["model", "fallback"]

BUDGET_HEADROOM = 2
MAX_PROPOSED_CHECKS = 12
MAX_RUBRIC_FILES = 10
MAX_PROMPT_FILES = 40
MAX_NAME_CHARS = 60
FALLBACK_NAME = "case"

MODEL_PROMPT = """Propose checks for an eval case: a task for a coding agent with a known good outcome. The agent will
get the prompt below in a fresh copy of the project at the state before the task, and the checks decide whether its
work is as good as the original, which went well.

Reply with one JSON object only:
{{"name": "<lowercase-words-with-hyphens, at most 40 characters>",
 "summary": "<one sentence, English>",
 "checks": [<checks>]}}

Check formats (use only these keys):
- {{"kind": "command", "run": "<shell command that must exit with 0>"}}: use the project's test command, if any.
- {{"kind": "file", "path": "<relative path>", "contains": ["<text that must be in the file>"]}}; also
  "not_contains", "matches" and "not_matches" (regular expressions), or "exists": false for a file that must be gone.
  Only text that any correct solution must contain, such as a function name the prompt asks for.
- {{"kind": "rubric", "rubric": "<what a good outcome is, in one or two sentences>", "files": ["<relative path>"]}}.
At most {max_checks} checks. Never put secrets, tokens or passwords in a check.

The project's test command: {test_command}

The prompt:
{prompt}

The files the original work changed:
{files}

The files it removed:
{removed}

The final reply of the original work:
{reply}"""


@dataclass(frozen=True)
class CaseProposal:
    """A case to edit before it is saved. Its start may be empty (no commit and no checkpoint found)."""

    case: EvalCase
    # What it was made from: the start, the changed files and the commands, as read from the conversation.
    facts: ConversationFacts
    checks_from: ChecksFrom
    test_command: TestCommand | None
    # Things the user should know about the proposal before saving it.
    notes: tuple[str, ...]


async def propose_for_conversation(
    session: Session,
    checkpoints: CheckpointStore,
    conversation: Conversation,
    project_folder: Path,
    ask: UtilityAsk | None,
) -> CaseProposal:
    """The proposed case for a conversation in a project; raises CaseFromConversationError when there is none."""
    facts = await read_conversation(session, checkpoints, conversation, project_folder)
    allowlist = resolve_setting(
        session, SETTING_DEFINITIONS["command_allowlist"], conversation.environment_id, conversation.project_id
    ).value
    entries = [entry for entry in allowlist if isinstance(entry, str)] if isinstance(allowlist, list) else []
    test_command = find_test_command(facts.commands, entries, project_folder)
    return await propose_case(facts, test_command, project_folder, ask)


async def propose_case(
    facts: ConversationFacts, test_command: TestCommand | None, project_folder: Path, ask: UtilityAsk | None
) -> CaseProposal:
    answer = await ask(model_prompt(facts, test_command)) if ask is not None else None
    proposed = parse_answer(answer)
    checks = proposed.checks
    checks_from: ChecksFrom = "model"
    if not checks:
        checks, checks_from = fallback_checks(facts, test_command), "fallback"
    elif test_command is not None and not any(isinstance(check, CommandCheck) for check in checks):
        checks = (command_check(test_command), *checks)
    taken = existing_names(project_folder)
    name = free_name(proposed.name or slug(facts.title) or FALLBACK_NAME, taken)
    case = EvalCase(
        name=name,
        prompt=facts.prompt,
        start=facts.start,
        checks=checks[:MAX_PROPOSED_CHECKS],
        summary=proposed.summary or "",
        agent=facts.agent if facts.agent and CASE_NAME.match(facts.agent) else None,
        preset=facts.preset,
        budget=proposed_budget(facts.usage),
    )
    return CaseProposal(case, facts, checks_from, test_command, proposal_notes(facts, checks_from, answer is not None))


def model_prompt(facts: ConversationFacts, test_command: TestCommand | None) -> str:
    files = facts.changed_files[:MAX_PROMPT_FILES]
    return MODEL_PROMPT.format(
        max_checks=MAX_PROPOSED_CHECKS,
        test_command=test_command.command if test_command else "(none found)",
        prompt=facts.prompt[:6000],
        files="\n".join(files) or "(none)",
        removed="\n".join(facts.removed_files) or "(none)",
        reply=facts.final_reply[:3000] or "(none)",
    )


@dataclass(frozen=True)
class ModelAnswer:
    name: str | None = None
    summary: str | None = None
    checks: tuple[Check, ...] = ()


def parse_answer(answer: str | None) -> ModelAnswer:
    """The name, summary and valid checks in the utility model's answer; empty for anything unusable."""
    if not answer:
        return ModelAnswer()
    start, end = answer.find("{"), answer.rfind("}")
    if start == -1 or end <= start:
        return ModelAnswer()
    try:
        value = json.loads(answer[start : end + 1])
    except json.JSONDecodeError:
        return ModelAnswer()
    if not isinstance(value, dict):
        return ModelAnswer()
    raw = value.get("checks")
    checks: tuple[Check, ...] = ()
    if isinstance(raw, list):
        # Each check is read on its own, so one bad check drops only itself.
        checks = tuple(check for item in raw[:MAX_PROPOSED_CHECKS] for check in parse_checks([item], []))
        checks = tuple(check for check in checks if not carries_secret(check))
    name = value.get("name")
    summary = value.get("summary")
    return ModelAnswer(
        name=(slug(name) or None) if isinstance(name, str) else None,
        summary=(" ".join(summary.split())[:MAX_SUMMARY_CHARS] or None) if isinstance(summary, str) else None,
        checks=checks,
    )


def carries_secret(check: Check) -> bool:
    """A command with a secret placeholder would run with the secret's value; a case file never names one."""
    return isinstance(check, CommandCheck) and SECRET_PLACEHOLDER in check.run


def fallback_checks(facts: ConversationFacts, test_command: TestCommand | None) -> tuple[Check, ...]:
    checks: list[Check] = []
    if test_command is not None:
        checks.append(command_check(test_command))
    checks.extend(FileCheck(f"file: {path}", path, exists=False) for path in facts.removed_files[:MAX_RUBRIC_FILES])
    kept = tuple(path for path in facts.changed_files if path not in facts.removed_files)[:MAX_RUBRIC_FILES]
    task = facts.prompt.strip().splitlines()[0][:200]
    rubric = f"The work does what the prompt asks ({task}), completely and without unrelated changes."
    checks.append(RubricCheck(f"rubric: {rubric}"[:120], rubric, kept))
    return tuple(checks)


def command_check(test_command: TestCommand) -> CommandCheck:
    return CommandCheck(f"command: {test_command.command}"[:120], test_command.command, DEFAULT_COMMAND_TIMEOUT)


def proposed_budget(usage: ConversationUsage | None) -> Budget | None:
    """Twice what the conversation used, rounded up: room for a different path without letting a run go wild."""
    if usage is None or usage.steps == 0:
        return None
    tokens = math.ceil(usage.tokens * BUDGET_HEADROOM / 1000) * 1000 if usage.tokens else None
    cost = math.ceil(usage.cost_usd * BUDGET_HEADROOM * 100) / 100 if usage.cost_usd else None
    seconds = math.ceil(usage.seconds * BUDGET_HEADROOM / 60) * 60 if usage.seconds > 0 else None
    return Budget(steps=usage.steps * BUDGET_HEADROOM, tokens=tokens, cost_usd=cost, seconds=seconds)


def proposal_notes(facts: ConversationFacts, checks_from: ChecksFrom, model_answered: bool) -> tuple[str, ...]:
    notes: list[str] = []
    if facts.start_source == "checkpoint":
        notes.append(
            "No commit of the project is older than the first message, so the case starts at the conversation's "
            "first checkpoint. The saved case keeps that checkpoint (pinned against the 30-day removal of "
            "checkpoints), but it holds only the files; a commit start also keeps the project's history."
        )
    elif facts.start_source == "none":
        notes.append("No start was found: give the commit the case starts at.")
    if facts.user_messages > 1:
        notes.append(
            f"The conversation has {facts.user_messages} messages of yours; the case plays only the first. Add "
            "to the prompt what the later ones asked for."
        )
    if checks_from == "fallback":
        notes.append(
            "The utility model's checks could not be used, so these are the standard ones."
            if model_answered
            else "No utility model answered, so these are the standard checks."
        )
    return tuple(notes)


def slug(text: str) -> str:
    """A case name from free text: lowercase letters, digits and hyphens, at most 60 characters."""
    value = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:MAX_NAME_CHARS].strip("-")
    return value if CASE_NAME.match(value) else ""


def existing_names(project_folder: Path) -> set[str]:
    folder = evals_dir(project_folder)
    return {file.stem for file in folder.glob("*.md")} if folder.is_dir() else set()


def free_name(name: str, taken: set[str]) -> str:
    """The name, or the name with the first free number after it."""
    if name not in taken:
        return name
    number = 2
    while True:
        suffix = f"-{number}"
        candidate = f"{name[: MAX_NAME_CHARS - len(suffix)].rstrip('-')}{suffix}"
        if candidate not in taken:
            return candidate
        number += 1
