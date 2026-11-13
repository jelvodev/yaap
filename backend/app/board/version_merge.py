"""*Merge into the version*: a reviewed card's branch is merged into its version's branch, checked, and delivered.

The git work is in app.board.version_branch. Here: which checks may run, the one-merge-at-a-time rule per version, the
run of the checks through a run's executor on the merge copy (like the criteria checks of a review, never in YAAP's
own process) and what the result does to the card. A merge that conflicts or fails a check moves nothing: the card goes
back to Build with the output in its description.
"""

import dataclasses
import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import anyio
from sqlalchemy import select

from app.agent.toolkit import ToolError
from app.board.card import Card, revise
from app.board.errors import ActionRunning, BoardError, BoardNotStarted, BoardOff, InvalidVersion, MergeRefused
from app.board.flow import Refusal, step
from app.board.models import BoardCardRow
from app.board.route import CardRoute
from app.board.service import BoardService
from app.board.stage_text import escape_headings, fence_for
from app.board.version_branch import (
    MergeCopy,
    MergeOutcome,
    VersionBranchError,
    default_branch,
    deliver_version,
    make_merge_copy,
    merge_card,
    merge_folder,
    merge_refusal,
    merged_into,
    remove_merge_copy,
    version_branch_name,
)
from app.core.background import BackgroundWork
from app.core.reply_services import ReplyServices
from app.evals.cases import DEFAULT_COMMAND_TIMEOUT
from app.evals.checks import command_result
from app.evals.models import CommandCheck
from app.git.place import git_place
from app.git.service import Repository
from app.plans.build_run import close_build_run, open_build_run
from app.plans.service import BuildRefused, PlanNotFound, PlanView
from app.plans.workspace import PlaceOf
from app.reviews.criteria_checks import approved_commands, card_criteria, criteria_commands, may_run, run_context

logger = logging.getLogger(__name__)

MergeStatus = Literal["running", "merged", "conflict", "failed"]
CRITERIA_HEADING = "### Acceptance criteria"
NOT_SHOWN = "The command was not shown with the button, so it was not approved."
NOT_APPROVED = "Approving the card's plan did not approve this command (the criterion changed after it)."
FAILED_HEADING = "Merge into the version failed"
NOTE_LIMIT = 3000
OUTPUT_LIMIT = 4000


@dataclass(frozen=True)
class MergeResult:
    status: MergeStatus
    output: str
    commit: str | None
    finished_at: datetime | None


@dataclass(frozen=True)
class MergeState:
    """What the card's *Merge into the version* button shows."""

    version: str | None
    branch: str | None
    merged: bool
    running: bool
    offered: bool
    refusal: str | None
    # The version's own `run:` criteria: the user sees them next to the button and sends them back with the click.
    version_commands: tuple[str, ...]
    # The commands of this card and the cards already merged that will run, each approved with its card's plan.
    card_commands: tuple[str, ...]
    last: MergeResult | None


@dataclass(frozen=True)
class CardCommands:
    """A card's criteria commands and the commands approving its plan approved."""

    criteria: tuple[str, ...]
    approved: tuple[str, ...]


@dataclass(frozen=True)
class MergeJob:
    project_id: int
    card_id: int
    title: str
    version: str
    card_branch: str
    view: PlanView
    place: Repository
    place_of: PlaceOf
    project_folder: Path
    run: tuple[str, ...]
    refused: tuple[tuple[str, str], ...]


def merge_commands(
    version_commands: Sequence[str], shown: Sequence[str], cards: Sequence[CardCommands]
) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    """The commands a merge runs and those it refuses with why. A version command runs only when it is exactly one the
    user was shown; a card's criteria command only when approving that card's plan approved it. Once each, in order."""
    run: list[str] = []
    refused: list[tuple[str, str]] = []
    for command in version_commands:
        if command in shown:
            run.append(command)
        else:
            refused.append((command, NOT_SHOWN))
    for card in cards:
        for command in card.criteria:
            if may_run(command, card.approved):
                run.append(command)
            else:
                refused.append((command, NOT_APPROVED))
    allowed = tuple(dict.fromkeys(run))
    pending = tuple(dict.fromkeys(item for item in refused if item[0] not in allowed))
    return allowed, pending


def merge_failed(card: Card, output: str, now: datetime) -> Card:
    """The card back in Build with the failed merge's output in its description."""
    text = escape_headings(output.strip()[-NOTE_LIMIT:]) or "no output"
    fence = fence_for(text)
    note = f"**{FAILED_HEADING}** ({now.strftime('%Y-%m-%d %H:%M')} UTC):\n\n{fence}\n{text}\n{fence}"
    description = f"{card.description.rstrip()}\n\n{note}" if card.description.strip() else note
    return revise(card, {"column": "build", "description": description, "updated": now})


@dataclass
class VersionMerges:
    board: BoardService
    route: CardRoute
    services: Callable[[], ReplyServices]
    work: BackgroundWork[None]
    lock: threading.Lock = field(default_factory=threading.Lock)
    # (project, version) with a merge running: one merge at a time per version.
    running: set[tuple[int, str]] = field(default_factory=set)
    # The last result per (project, card), in memory.
    results: dict[tuple[int, int], MergeResult] = field(default_factory=dict)

    # --- Reading (blocking) ----------------------------------------------------------------------------------------

    def place_for(self, project_id: int) -> tuple[Repository, PlaceOf, Path]:
        where = self.route.builds.project_place(self.services(), project_id)

        def place_of(path: Path) -> Repository:
            return git_place(where.environment_id, path)

        return place_of(where.folder), place_of, where.folder

    def plan_of(self, card: Card) -> PlanView | None:
        if card.links.plan_id is None:
            return None
        try:
            return self.route.plans().get(card.links.plan_id)
        except PlanNotFound:
            return None

    def version_commands(self, project_id: int, version: str) -> tuple[str, ...]:
        try:
            text = self.board.version(project_id, version).acceptance_criteria
        except InvalidVersion:
            return ()
        return criteria_commands(card_criteria(f"{CRITERIA_HEADING}\n{text}")) if text.strip() else ()

    def card_commands(self, card: Card) -> CardCommands:
        view = self.plan_of(card)
        approved = approved_commands(view) if view is not None else ()
        return CardCommands(criteria_commands(card_criteria(card.specification)), approved)

    def merged_cards(self, project_id: int, version: str, place: Repository, skip: int) -> list[CardCommands]:
        """The other cards of the version whose branch is in the version branch."""
        with self.board.session_factory() as session:
            ids = session.scalars(
                select(BoardCardRow.card_id).where(
                    BoardCardRow.project_id == project_id,
                    BoardCardRow.version == version,
                    BoardCardRow.board_column.in_(("review", "done")),
                    BoardCardRow.card_id != skip,
                )
            ).all()
        opened = self.board.opened(project_id)
        found: list[CardCommands] = []
        for card_id in ids:
            try:
                card = self.board.read_card(opened.repository, card_id, opened.repository.head())
            except BoardError:
                continue  # A broken card file has no branch to count.
            if card.links.branch and merged_into(place, version, card.links.branch):
                found.append(self.card_commands(card))
        return found

    def state(self, project_id: int, card_id: int) -> MergeState:
        """What the button shows for the card. Blocking."""
        card, facts = self.route.card_and_facts(project_id, card_id)
        version, branch = card.version, card.links.branch
        last = self.results.get((project_id, card_id))
        if version is None:
            return MergeState(None, None, False, False, False, "The card has no version.", (), (), last)
        try:
            version_branch = version_branch_name(version)
            place, _place_of, _folder = self.place_for(project_id)
            default = default_branch(place)[0]
            refusal = merge_refusal(card.column, facts.review == "done", version, facts.plan_status, branch, default)
            merged = bool(branch) and merged_into(place, version, branch or "")
            others = self.merged_cards(project_id, version, place, card_id)
        except VersionBranchError as error:
            return MergeState(version, None, False, False, False, str(error), (), (), last)
        with self.lock:
            running = (project_id, version) in self.running
        shown = self.version_commands(project_id, version)
        run, _refused = merge_commands(shown, shown, [self.card_commands(card), *others])
        offered = refusal is None and not merged and not running
        card_commands = tuple(command for command in run if command not in shown)
        return MergeState(version, version_branch, merged, running, offered, refusal, shown, card_commands, last)

    # --- Starting --------------------------------------------------------------------------------------------------

    def start(self, project_id: int, card_id: int, shown: tuple[str, ...]) -> MergeJob:
        """Check that the card may be merged and take its version's lock. Blocking; `run` follows on the event loop."""
        card, facts = self.route.card_and_facts(project_id, card_id)
        version, branch = card.version, card.links.branch
        try:
            place, place_of, folder = self.place_for(project_id)
            refusal = merge_refusal(
                card.column, facts.review == "done", version, facts.plan_status, branch, default_branch(place)[0]
            )
        except VersionBranchError as error:
            raise MergeRefused(str(error)) from error
        view = self.plan_of(card)
        if refusal is not None or version is None or branch is None or view is None:
            raise MergeRefused(refusal or "The card's build cannot be found.")
        if merged_into(place, version, branch):
            raise MergeRefused("The card is already merged into its version.")
        others = self.merged_cards(project_id, version, place, card_id)
        commands = self.version_commands(project_id, version)
        run, refused = merge_commands(commands, shown, [self.card_commands(card), *others])
        job = MergeJob(project_id, card_id, card.title, version, branch, view, place, place_of, folder, run, refused)
        with self.lock:
            if (project_id, version) in self.running:
                raise ActionRunning(f"A merge into version {version} is running.")
            self.running.add((project_id, version))
            self.results[(project_id, card_id)] = MergeResult("running", "", None, None)
        return job

    async def merge(self, project_id: int, card_id: int, shown: tuple[str, ...]) -> MergeState:
        """*Merge into the version*: validated now, run in the background. On the event loop."""
        job = await anyio.to_thread.run_sync(self.start, project_id, card_id, shown)
        try:
            self.work.start(self.run(job), name=f"version-merge-{project_id}-{card_id}")
        except BaseException:
            self.release(job)
            raise
        return await anyio.to_thread.run_sync(self.state, project_id, card_id)

    def release(self, job: MergeJob) -> None:
        with self.lock:
            self.running.discard((job.project_id, job.version))

    # --- Running ---------------------------------------------------------------------------------------------------

    async def run(self, job: MergeJob) -> None:
        """Merge in a copy, run the checks there, deliver the version branch, then move the card. A conflict or a
        failure moves nothing in the project."""
        result = MergeResult("failed", "The merge stopped before it finished.", None, None)
        # Cut off (YAAP stops): nothing moved in the project, and the card stays where it is.
        move = False
        try:
            result = await self.attempt(job)
            move = True
        except (VersionBranchError, BuildRefused, BoardError) as error:
            result, move = MergeResult("failed", str(error), None, None), True
        except Exception as error:  # noqa: BLE001 - background work: the merge fails, the cause is logged
            logger.exception("The merge of card #%s into version %s failed.", job.card_id, job.version)
            result = MergeResult("failed", f"The merge failed unexpectedly: {type(error).__name__}.", None, None)
            move = True
        finally:
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(self.finish, job, result, move)

    async def attempt(self, job: MergeJob) -> MergeResult:
        if job.refused:
            lines = [f"Not run: {command}\n{why}" for command, why in job.refused]
            return MergeResult("failed", "\n\n".join(lines), None, None)
        copy, outcome = await anyio.to_thread.run_sync(self.merge_in_copy, job)
        if not outcome.merged or outcome.commit is None:
            return MergeResult("conflict", outcome.output, None, None)
        failure = await self.check(job)
        if failure is not None:
            return MergeResult("failed", failure, None, None)
        await anyio.to_thread.run_sync(deliver_version, job.place, copy, outcome.commit)
        return MergeResult("merged", outcome.output, outcome.commit, None)

    def merge_in_copy(self, job: MergeJob) -> tuple[MergeCopy, MergeOutcome]:
        copy = make_merge_copy(job.place, job.place_of, job.version, job.card_branch)
        message = f"Merge card #{job.card_id} into version {job.version}: {job.title}"[:200]
        return copy, merge_card(copy, message)

    async def check(self, job: MergeJob) -> str | None:
        """Run the allowed commands on the merge copy through a run's executor; the first failure, or None."""
        if not job.run:
            return None
        services = self.services()
        view = dataclasses.replace(job.view, build_folder=merge_folder(job.version))
        build = await anyio.to_thread.run_sync(open_build_run, services, view)
        try:
            context = run_context(build.run)
            for command in job.run:
                check = CommandCheck(f"command: {command}"[:120], command, DEFAULT_COMMAND_TIMEOUT)
                result = await command_result(check, context)
                if result.status != "passed":
                    return f"{command}\n{result.detail}"[:OUTPUT_LIMIT]
        except ToolError as error:
            return f"The checks could not be run: {error}"
        finally:
            close_build_run(services, build, "complete")
        return None

    def finish(self, job: MergeJob, result: MergeResult, move: bool) -> None:
        """Remove the copy, move the card by the result (unless the merge was cut off), record the result and release
        the version. Blocking."""
        try:
            try:
                remove_merge_copy(job.project_folder, job.version)
            except VersionBranchError as error:
                logger.warning("The merge copy of version %s was not removed: %s", job.version, error)
            if move:
                self.move_card(job, result)
        finally:
            done = dataclasses.replace(result, finished_at=datetime.now(UTC))
            with self.lock:
                self.results[(job.project_id, job.card_id)] = done
                self.running.discard((job.project_id, job.version))

    def move_card(self, job: MergeJob, result: MergeResult) -> None:
        try:
            if result.status == "merged":
                self.board.apply_event(job.project_id, job.card_id, "merged", "yaap")
                return
            opened = self.board.opened(job.project_id)

            def change(card: Card) -> Card | None:
                outcome = step(self.board.facts(job.project_id, card), "merge_failed", "yaap")
                if isinstance(outcome, Refusal):
                    return None
                return merge_failed(card, result.output, self.board.clock())

            self.board.write_card(opened, job.card_id, change, {"moved": 1})
        except (BoardOff, BoardNotStarted):
            return
        except BoardError as error:
            logger.warning("Card #%s did not follow its merge: %s", job.card_id, error)
