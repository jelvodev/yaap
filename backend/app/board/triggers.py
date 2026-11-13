"""Triggers: cards in Inbox from things YAAP notices on a schedule, without a model call. A trigger is a scheduled
task with the action `board_trigger`; it polls one kind of thing (failing CI on the default branch, issues with a
label, dependency pull requests, a scheduled command that fails) and makes a card for each new one.

Everything here is pure: the trigger's configuration, what counts as a failing check or a dependency pull request,
and which observations become cards (`trigger_cards`: one card per origin ref, at most the day's budget). Polling
and writing the cards is `app.scheduling.board_trigger`.

What a trigger observes is outside text (an issue's body, a command's output): it goes into the card's description
as data, with card headings moved in, and the board shows it plainly.
"""

from collections.abc import Iterable, Sequence, Set
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent.policy import command_allowed
from app.board.card import Kind, Origin
from app.board.operations import NewCard
from app.board.stage_text import escape_headings, fence_for
from app.plans.markdown import code_span

TriggerKind = Literal["ci_failed", "new_issue", "dependency_update", "command_failed"]
TRIGGER_KINDS: tuple[TriggerKind, ...] = ("ci_failed", "new_issue", "dependency_update", "command_failed")
DEFAULT_LABEL = "yaap"
DEFAULT_CARDS_PER_DAY = 3
MAX_CARDS_PER_DAY = 20
MAX_TITLE = 200
MAX_DETAIL = 20_000
# The GitHub accounts whose pull requests are dependency updates.
DEPENDENCY_BOTS = frozenset({"dependabot[bot]", "renovate[bot]", "dependabot", "renovate", "renovate-bot"})
# Check run conclusions that mean the checks failed.
FAILED_CONCLUSIONS = frozenset({"failure", "timed_out", "action_required", "startup_failure"})

CARD_KIND: dict[TriggerKind, Kind] = {
    "ci_failed": "bug",
    "new_issue": "feature",
    "dependency_update": "chore",
    "command_failed": "bug",
}


class TriggerConfig(BaseModel):
    """A trigger's settings, kept in the scheduled task's `config`: what it polls and how many cards it may make a
    day. `label` is for `new_issue`, `command` for `command_failed` (which must also be in the task's allowed
    commands, because it runs without the user)."""

    model_config = ConfigDict(extra="forbid")

    kind: TriggerKind
    label: str | None = Field(default=None, max_length=50)
    command: str | None = Field(default=None, max_length=200)
    cards_per_day: int = Field(default=DEFAULT_CARDS_PER_DAY, ge=1, le=MAX_CARDS_PER_DAY)

    @model_validator(mode="after")
    def fits_kind(self) -> "TriggerConfig":
        label = (self.label or "").strip()
        command = " ".join((self.command or "").split())
        if self.kind == "new_issue":
            self.label = label or DEFAULT_LABEL
        else:
            self.label = None
        if self.kind == "command_failed":
            if not command:
                raise ValueError("A command trigger needs the command it runs.")
            self.command = command
        else:
            self.command = None
        return self


def trigger_refusal(config: TriggerConfig, allowed_commands: Sequence[str]) -> str | None:
    """Why the trigger cannot be saved as it is, or None. A command trigger runs without the user, so its command
    must be one the task allows for unattended runs (the task's allowed commands)."""
    if config.kind == "command_failed" and not command_allowed(config.command or "", tuple(allowed_commands)):
        return "Add the trigger's command to the task's allowed commands; it runs without you."
    return None


@dataclass(frozen=True)
class Observation:
    """One thing a trigger noticed: its link (the run, issue or pull request), which is also how it is told apart
    from the ones noticed before, a title, outside text for the description and the card's kind when it differs
    from the trigger's."""

    ref: str
    title: str
    detail: str = ""
    kind: Kind | None = None


# --- Reading what was polled ---------------------------------------------------------------------------------------


def checks_failed(checks: Iterable[dict[str, object]]) -> list[str]:
    """The names of the completed check runs that failed; empty when every check passed or some still run."""
    return sorted(
        {
            str(check.get("name") or "check")
            for check in checks
            if check.get("status") == "completed" and check.get("conclusion") in FAILED_CONCLUSIONS
        }
    )


def is_dependency_author(author: str) -> bool:
    return author.lower() in DEPENDENCY_BOTS


def ci_observation(full_name: str, branch: str, sha: str, failed: Sequence[str]) -> Observation:
    names = ", ".join(failed)
    detail = f"The latest checks of {branch} ({sha[:12]}) in {full_name} failed: {names}."
    return Observation(
        ref=f"https://github.com/{full_name}/commit/{sha}/checks",
        title=f"CI fails on {branch}: {names}",
        detail=detail,
    )


def command_observation(task_id: int, run_id: int, command: str, exit_code: int | None, output: str) -> Observation:
    ended = "was stopped (it took too long)" if exit_code is None else f"exited with code {exit_code}"
    fence = fence_for(output)
    tail = f"{fence}\n{output.rstrip()}\n{fence}"
    detail = f"The scheduled command {code_span(command)} {ended}. The end of its output:\n\n{tail}"
    return Observation(ref=f"task:{task_id}/run:{run_id}", title=f"Scheduled command fails: {command}", detail=detail)


# --- Which observations become cards -------------------------------------------------------------------------------


def short_title(title: str) -> str:
    text = " ".join(title.split()) or "Noticed by a trigger"
    return text if len(text) <= MAX_TITLE else text[: MAX_TITLE - 1].rstrip() + "…"


def trigger_card(kind: TriggerKind, observation: Observation) -> NewCard:
    detail = observation.detail.strip()
    if len(detail) > MAX_DETAIL:
        detail = detail[:MAX_DETAIL].rstrip() + "\n\n[Shortened; the rest is behind the link.]"
    header = f"Noticed by a trigger ({kind.replace('_', ' ')}): {observation.ref}"
    return NewCard(
        title=short_title(observation.title),
        kind=observation.kind or CARD_KIND[kind],
        column="inbox",
        description="\n\n".join(part for part in (header, escape_headings(detail)) if part),
        origin=Origin(type="trigger", ref=observation.ref),
    )


def trigger_cards(
    kind: TriggerKind, observations: Sequence[Observation], known_refs: Set[str], budget: int
) -> list[NewCard]:
    """The cards to make: one per observation whose ref is on no card yet (also not twice in one poll), in the
    order observed, at most `budget` of them."""
    cards: list[NewCard] = []
    seen = set(known_refs)
    for observation in observations:
        if len(cards) >= budget:
            break
        if not observation.ref or len(observation.ref) > 300 or observation.ref in seen:
            continue
        seen.add(observation.ref)
        cards.append(trigger_card(kind, observation))
    return cards
