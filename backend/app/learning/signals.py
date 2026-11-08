"""Signals on a run's tool steps: what a later review can learn from.

The gate (`agent.gate.call_tool`) asks `step_signal` about every tool call it records. Three signals, each cheap
enough to decide while the run works:

- **declined**: the user declined the call; the note is the reason they wrote in the approval card, if any.
- **test_failed_twice**: `run_command` ran a test command (the same detection as eval cases use,
  `evals.project_commands.is_test_command`) that failed (an exit code other than 0, or stopped) for the second time in
  this run; the note is the command. Commands count as the same with their whitespace collapsed.
- **known_search**: the agent looked for what its prompt already held. Deliberately simple: `read_file` of the
  project layer's `instructions.md`, a standard, an engineering standard or the memory (all of which are in the
  prompt); or `search_project`/`search` whose query has at least MIN_QUERY_WORDS significant words of which at
  least KNOWN_SHARE appear in one Blueprint rule or one memory entry. The note names that item.

Corrections by the user are not detected live; the review of a conversation (app/learning/proposals) finds them.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.agent.runs import AgentRun
from app.blueprint.layers import PROJECT_LAYER_FOLDER, BlueprintError, active_items
from app.evals.project_commands import is_test_command
from app.projects.memory import MEMORY_FILE, WORD, parse_entries, read_memory
from app.tracing.recorder import SIGNAL_NOTE_CHARS, ApprovalOutcome, SignalKind

MIN_QUERY_WORDS = 2
KNOWN_SHARE = 0.75
TEST_FAILURES = 2
SEARCH_TOOLS = frozenset({"search_project", "search"})
PASSED = re.compile(r"^Exit code 0(?:\n|$)")
# Words that say nothing about what was searched for.
STOP_WORDS = frozenset(
    {
        "the", "and", "for", "with", "that", "this", "from", "what", "how", "where", "which", "are", "was", "use",
        "not", "all", "any", "can", "does", "into", "when", "who", "why", "you", "our", "its", "def", "class",
    }
)
# Files of the project layer whose content is always in the prompt.
PROMPT_FILES = re.compile(
    rf"^{re.escape(PROJECT_LAYER_FOLDER)}/"
    rf"(?:instructions\.md|{re.escape(MEMORY_FILE)}|(?:standards|engineering)/[^/]+\.md)$"
)


@dataclass(frozen=True)
class Signal:
    kind: SignalKind
    note: str | None = None


def clip(text: str | None) -> str | None:
    if not text:
        return None
    text = " ".join(text.split())
    return text if len(text) <= SIGNAL_NOTE_CHARS else text[: SIGNAL_NOTE_CHARS - 1] + "…"


def significant_words(text: str) -> set[str]:
    return {word.lower() for word in WORD.findall(text)} - STOP_WORDS


def known_match(query: str, known: Iterable[tuple[str, str]]) -> str | None:
    """The label of the first known text (label, text) that holds most of the query's significant words."""
    words = significant_words(query)
    if len(words) < MIN_QUERY_WORDS:
        return None
    for label, text in known:
        if len(words & significant_words(text)) / len(words) >= KNOWN_SHARE:
            return label
    return None


def prompt_file(path: str) -> bool:
    """Whether a project path is a Blueprint file whose content the prompt already holds."""
    normalized = path.strip().replace("\\", "/").removeprefix("./")
    return PROMPT_FILES.match(normalized) is not None


def failed_test_command(name: str, arguments: dict[str, Any], output: str, is_error: bool) -> str | None:
    """The test command of a run_command call that failed; None for anything else."""
    command = arguments.get("command")
    if name != "run_command" or not isinstance(command, str) or not is_test_command(command):
        return None
    if not is_error and PASSED.match(output):
        return None
    return " ".join(command.split())


def known_texts(run: AgentRun) -> list[tuple[str, str]]:
    """The Blueprint rules and memory entries the run's prompt holds, labelled for a note."""
    known: list[tuple[str, str]] = []
    try:
        for item in active_items(run.blueprint_layers):
            known += [(f"rule in {item.source.label} {item.path}: {rule}", rule) for rule in item.rules]
        if run.project_folder is not None:
            entries = parse_entries(read_memory(run.project_folder))
            known += [(f"memory entry: {entry.text}", entry.text) for entry in entries]
    except (OSError, BlueprintError):
        return known  # A file that cannot be read gives no signal; the run goes on.
    return known


def search_signal(run: AgentRun, name: str, arguments: dict[str, Any]) -> Signal | None:
    if name == "read_file":
        path = str(arguments.get("path") or "")
        return Signal("known_search", f"read {path}, which the prompt already holds") if prompt_file(path) else None
    if name not in SEARCH_TOOLS:
        return None
    query = str(arguments.get("query") or arguments.get("pattern") or "")
    label = known_match(query, known_texts(run))
    return Signal("known_search", clip(f"searched for '{query}'; the prompt holds the {label}")) if label else None


def step_signal(
    run: AgentRun,
    name: str,
    arguments: dict[str, Any],
    output: str,
    is_error: bool,
    approval: ApprovalOutcome | None,
    reason: str | None,
) -> Signal | None:
    """The signal of one tool call, if it has one. Counts failed test commands on the run's state."""
    if approval == "declined":
        return Signal("declined", clip(reason))
    command = failed_test_command(name, arguments, output, is_error)
    if command is not None:
        failures = run.state.failed_tests.get(command, 0) + 1
        run.state.failed_tests[command] = failures
        return Signal("test_failed_twice", clip(command)) if failures == TEST_FAILURES else None
    if is_error:
        return None
    return search_signal(run, name, arguments)

