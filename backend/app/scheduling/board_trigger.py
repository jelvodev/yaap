"""The *board_trigger* action of a scheduled task: poll one kind of thing and put a card in the board's Inbox for
each new one (`app.board.triggers`). It makes no model call, so a run costs nothing.

A run ends at once: it polls, makes at most the day's budget of cards (origin `trigger`, linked to the run, issue or
pull request; one card per link) and keeps how many it made in its summary. A poll that fails makes no card, and a
project whose board is off or not started gets none. A `command_failed` trigger runs its command in the runner of
the project's environment, never in YAAP's own process, and only when the task allows the command.
"""

import logging
import re
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.command_tools import command_environment
from app.agent.policy import command_allowed
from app.board.errors import BoardError
from app.board.intake import CardIntake
from app.board.issues import issue_card, project_repository
from app.board.models import BoardCardRow
from app.board.service import BoardService
from app.board.triggers import (
    Observation,
    TriggerConfig,
    checks_failed,
    ci_observation,
    command_observation,
    is_dependency_author,
    trigger_cards,
)
from app.core.reply_services import ReplyServices
from app.credentials.crypto import CredentialCipher, CredentialDecryptionError
from app.credentials.store import read_credential_value
from app.evals.checks import output_tail
from app.git.place import run_from_worker
from app.github.client import GitHubClient, GitHubError, Issue, PullRequest
from app.projects.service import ProjectError, project_path
from app.runner.service import RUNNERS, Runners, runner_path
from app.storage.models import Project, ScheduledRun, ScheduledTask
from app.targets.base import CommandExecutor, TargetError

logger = logging.getLogger(__name__)

BOARD_TRIGGER_ACTION = "board_trigger"
COMMAND_TIMEOUT = 600.0
MAX_ISSUES = 50
SUMMARY_COUNT = re.compile(r"^(\d+) new cards?\b")


class PollFailed(Exception):
    """The trigger could not look; the message is safe to show. No card is made."""


class TriggerSource(Protocol):
    """What a trigger asks GitHub; tests pass a stub."""

    def default_branch_head(self, full_name: str) -> tuple[str, str]: ...

    def checks(self, full_name: str, sha: str) -> list[dict[str, object]]: ...

    def labelled_issues(self, full_name: str, label: str) -> list[Issue]: ...

    def open_pulls(self, full_name: str) -> list[PullRequest]: ...


@dataclass(frozen=True)
class GitHubTriggerSource:
    client: GitHubClient

    def default_branch_head(self, full_name: str) -> tuple[str, str]:
        branch = self.client.repository(full_name).default_branch
        commit = self.client.request("GET", f"/repos/{full_name}/commits/{quote(branch, safe='')}").json()
        return branch, str(commit.get("sha") or "")

    def checks(self, full_name: str, sha: str) -> list[dict[str, object]]:
        return list(self.client.checks(full_name, sha))

    def labelled_issues(self, full_name: str, label: str) -> list[Issue]:
        return self.client.issues(full_name, "open", [label], limit=MAX_ISSUES)

    def open_pulls(self, full_name: str) -> list[PullRequest]:
        return self.client.pulls(full_name, "open")


class OpenedSource(AbstractContextManager[TriggerSource]):
    def __init__(self, token: str | None) -> None:
        self.client = GitHubClient(token)

    def __enter__(self) -> TriggerSource:
        return GitHubTriggerSource(self.client.__enter__())

    def __exit__(self, *details: object) -> None:
        self.client.__exit__(*details)


OpenTriggerSource = Callable[[str | None], AbstractContextManager[TriggerSource]]


# --- Counting the cards a trigger made -----------------------------------------------------------------------------


def run_summary(card_ids: Sequence[int], known: int, note: str = "") -> str:
    """The run's summary; it starts with how many cards the run made, which `cards_in_summary` reads back."""
    count = len(card_ids)
    text = f"{count} new card{'s' if count != 1 else ''}"
    text += f": {', '.join(f'#{card_id}' for card_id in card_ids)}." if card_ids else "."
    if known:
        text += f" {known} already on the board."
    return f"{text} {note}".strip()


def cards_in_summary(summary: str | None) -> int:
    found = SUMMARY_COUNT.match(summary or "")
    return int(found.group(1)) if found else 0


def day_start(now: datetime, timezone: str) -> datetime:
    """Midnight of `now`'s day in the task's time zone, in UTC; the day's budget counts from there."""
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")
    local = now.astimezone(zone)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def cards_made_since(session: Session, task_id: int, since: datetime) -> int:
    summaries = session.scalars(
        select(ScheduledRun.summary).where(ScheduledRun.task_id == task_id, ScheduledRun.started_at >= since)
    )
    return sum(cards_in_summary(summary) for summary in summaries)


def cards_this_week(session: Session, task_id: int, now: datetime | None = None) -> int:
    return cards_made_since(session, task_id, (now or datetime.now(UTC)) - timedelta(days=7))


# --- The run -------------------------------------------------------------------------------------------------------


def runner_executor(runners: Runners, project: Project) -> CommandExecutor:
    """The executor of the project's folder in its environment's runner; refused when the runner is off."""
    if not runners.enabled or runners.workspace_dir is None:
        raise PollFailed("A trigger's command runs only in the runner, and the runner is off.")
    try:
        runtime = runners.runtime(project.environment_id, f"Board trigger in {project.name}")
        return runtime.executor(runner_path(runtime.environment_dir, str(project_path(runners.workspace_dir, project))))
    except (TargetError, ProjectError) as error:
        raise PollFailed(str(error)) from error


@dataclass(frozen=True)
class TriggerServices:
    board: BoardService
    cipher: CredentialCipher
    runners: Runners = field(default_factory=lambda: RUNNERS)
    open_source: OpenTriggerSource = OpenedSource
    executor: Callable[[Runners, Project], CommandExecutor] = runner_executor
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))

    def github_observations(self, session: Session, project: Project, config: TriggerConfig) -> list[Observation]:
        full_name = project_repository(project, self.board.workspace_dir)
        if full_name is None:
            raise PollFailed("The project's repository is not on GitHub.")
        used_by = f"Board trigger: {project.name}"
        try:
            token = read_credential_value(
                session, self.cipher, project.environment_id, "github_token", project.id, used_by
            )
            with self.open_source(token) as source:
                return source_observations(source, full_name, config)
        except (GitHubError, CredentialDecryptionError) as error:
            raise PollFailed(f"GitHub: {error}") from error

    def command_observations(
        self, project: Project, task: ScheduledTask, record: ScheduledRun, command: str
    ) -> list[Observation]:
        if not command_allowed(command, tuple(task.allowed_commands or [])):
            raise PollFailed("The trigger's command is not in the task's allowed commands, so it does not run.")
        executor = self.executor(self.runners, project)
        try:
            result = run_from_worker(executor.run, command, command_environment(), COMMAND_TIMEOUT)
        except (TargetError, OSError) as error:
            raise PollFailed(f"The command could not be started: {error}") from error
        if result.exit_code == 0 and not result.timed_out:
            return []
        exit_code = None if result.timed_out else result.exit_code
        return [command_observation(task.id, record.id, command, exit_code, output_tail(result.output))]

    def observe(
        self, session: Session, project: Project, task: ScheduledTask, record: ScheduledRun, config: TriggerConfig
    ) -> list[Observation]:
        if config.kind == "command_failed":
            return self.command_observations(project, task, record, config.command or "")
        return self.github_observations(session, project, config)

    def run(self, session: Session, task: ScheduledTask, record: ScheduledRun) -> tuple[str, str]:
        """Poll once and make the cards; the run's status and summary."""
        config = TriggerConfig.model_validate(task.config or {})
        try:
            opened = self.board.opened(task.project_id)
        except BoardError as error:
            return "stopped", f"0 new cards. {error}"
        budget = config.cards_per_day - cards_made_since(session, task.id, day_start(self.clock(), task.timezone))
        if budget <= 0:
            return "limit", f"0 new cards. Today's budget of {config.cards_per_day} cards is used."
        try:
            observations = self.observe(session, opened.project, task, record, config)
        except PollFailed as error:
            return "failed", f"0 new cards. {error}"
        # Reading the token logs its use; commit it before the board writes in sessions of its own.
        session.commit()
        known = known_refs(session, task.project_id)
        room = CardIntake(self.board).room_in_inbox(session, task.project_id)
        cards = trigger_cards(config.kind, observations, known, max(0, min(budget, room)))
        already = sum(1 for observation in observations if observation.ref in known)
        made: list[int] = []
        try:
            for card in cards:
                made.append(self.board.create_card(task.project_id, card).view.id)
        except BoardError as error:
            return "failed", run_summary(made, already, f"Then: {error}")
        return "done", run_summary(made, already)


def source_observations(source: TriggerSource, full_name: str, config: TriggerConfig) -> list[Observation]:
    if config.kind == "ci_failed":
        branch, sha = source.default_branch_head(full_name)
        failed = checks_failed(source.checks(full_name, sha)) if sha else []
        return [ci_observation(full_name, branch, sha, failed)] if failed else []
    if config.kind == "new_issue":
        observations = []
        for issue in source.labelled_issues(full_name, config.label or ""):
            card = issue_card(issue, full_name)
            observations.append(Observation(card.origin.ref or "", card.title, card.description, card.kind))
        return observations
    return [
        Observation(pull.html_url, f"Dependency update: {pull.title}", f"Pull request #{pull.number} by {pull.author}.")
        for pull in source.open_pulls(full_name)
        if is_dependency_author(pull.author) and pull.html_url
    ]


def known_refs(session: Session, project_id: int) -> set[str]:
    """The origin refs of every card on the project's board, whatever their origin: an issue imported by hand is not
    made into a card again."""
    query = select(BoardCardRow.origin_ref).where(
        BoardCardRow.project_id == project_id, BoardCardRow.origin_ref.is_not(None)
    )
    return {ref for ref in session.scalars(query) if ref}


def run_board_trigger(services: ReplyServices, session: Session, task: ScheduledTask, record: ScheduledRun) -> None:
    """Run a claimed trigger task to its end. Runs in a worker thread; record is committed."""
    if services.board is None:
        status, summary = "failed", "0 new cards. The board is not available."
    else:
        try:
            status, summary = TriggerServices(services.board, services.cipher).run(session, task, record)
        except Exception:  # noqa: BLE001 - the scheduler's loop must keep running; the error is logged
            logger.exception("Board trigger %s failed.", task.id)
            status, summary = "failed", "0 new cards. The trigger failed; see YAAP's log."
    record.status, record.summary, record.finished_at = status, summary, datetime.now(UTC)
    task.running = False
    session.commit()
