"""The board service: reading a project's board and changing its cards and versions.

Every change goes through `write`: it reads at the branch's head, builds the change, and commits it with the head as
the expected value; when the branch moved in between it reads and builds once more, then gives up with
BoardConflict. After each write the index is synced. Moves and stage events go through the pure `flow.step`.
Changes made close together by YAAP are squashed into one commit (`squash.should_squash`)."""

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Generic, TypeVar

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.blueprint.levels import layers_for
from app.board.card import BrokenCard, Card, Column, Kind, parse_checklist, render_card
from app.board.defaults import default_definition
from app.board.errors import (
    BoardError,
    BoardConflict,
    BoardExists,
    BoardFull,
    BoardNotStarted,
    BoardOff,
    BoardUnavailable,
    CardBroken,
    CardBuilding,
    CardChanged,
    CardNotFound,
    HeadMoved,
    InvalidVersion,
    MoveRefused,
    NoRepository,
    ProjectNotFound,
    PushNotAllowed,
)
from app.board.flow import Actor, CardEvent, CardFacts, Refusal, review_from_status, step
from app.board.index import BoardIndexer, parse_file_card
from app.board.links import conversations_of, forget_card, record_conversation, with_conversation, with_plan
from app.board.models import BoardCardRow, BoardState, BoardVersionRow, ConversationRole, StorageKind
from app.board.operations import (
    MAX_CARDS,
    MAX_VERSIONS,
    NewCard,
    answered,
    checked_version_name,
    moved,
    new_card,
    patched,
    put_version,
    sent_back,
)
from app.board.paths import card_path, version_path
from app.board.publish import BoardPusher
from app.board.repository import BoardRepository, Change, CommitInfo, Delete, Put
from app.board.squash import Tally, board_message, should_squash, squash_message
from app.board.storage import BoardStorage
from app.board.switch import board_enabled, board_push_allowed
from app.board.versions import BrokenVersion, Version, parse_version, render_version
from app.board.views import (
    BoardView,
    CardDetail,
    LinkedConversation,
    PushView,
    Related,
    VersionView,
    card_view,
    columns_of,
    current_version,
    version_views,
)
from app.core.settings import Source
from app.plans.model import next_status
from app.plans.models import PlanRecord
from app.plans.store import PlanStore
from app.storage.models import Environment, Project, Review

Result = TypeVar("Result")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Planned(Generic[Result]):
    """A change built from what was read at a head: the files to write, what it counts as, and what to return."""

    changes: tuple[Change, ...]
    tally: Tally
    result: Result


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


@dataclass(frozen=True)
class Opened:
    project: Project
    repository: BoardRepository


@dataclass(frozen=True)
class BoardService:
    session_factory: sessionmaker[Session]
    storage: BoardStorage
    indexer: BoardIndexer
    # Where the Blueprint is, for a definition of done from a standard; None uses the defaults in code.
    blueprint_dir: Path | None = None
    workspace_dir: Path | None = None
    pusher: BoardPusher = BoardPusher()
    clock: Callable[[], datetime] = utc_now

    # --- opening -------------------------------------------------------------------------------------------------

    def project(self, project_id: int) -> Project:
        with self.session_factory() as session:
            project = session.get(Project, project_id)
            if project is None:
                raise ProjectNotFound()
            session.expunge(project)
            return project

    def opened(self, project_id: int) -> Opened:
        """The project's board, after checking the switch and that the board was started; the index is synced."""
        project = self.project(project_id)
        with self.session_factory() as session:
            if not board_enabled(session, project.environment_id, project.id).enabled:
                raise BoardOff()
            if session.get(BoardState, project.id) is None:
                raise BoardNotStarted()
        repository = self.storage.open(project)
        self.indexer.sync(project.id, repository)
        return Opened(project, repository)

    # --- writing -------------------------------------------------------------------------------------------------

    def write(self, opened: Opened, build: Callable[[str | None], Planned[Result]]) -> Result:
        """Build the change at the head and commit it there; once more after HeadMoved, then BoardConflict."""
        repository = opened.repository
        for attempt in (1, 2):
            head = repository.head()
            planned = build(head)
            if not planned.changes:
                return planned.result
            message = board_message(planned.tally)
            squash = False
            if head is not None:
                info = repository.commit_info(head)
                squash = self.may_squash(opened.project.id, repository, info)
                message = squash_message(info.message, message) if squash else message
            try:
                repository.commit_changes(planned.changes, message, head, squash)
            except HeadMoved:
                if attempt == 2:
                    raise BoardConflict() from None
                # The second build reads the index too (facts, ids), so it must see the change that came between.
                self.indexer.sync(opened.project.id, repository)
                continue
            self.indexer.sync(opened.project.id, repository)
            return planned.result
        raise BoardConflict()

    def may_squash(self, project_id: int, repository: BoardRepository, info: CommitInfo) -> bool:
        with self.session_factory() as session:
            state = session.get(BoardState, project_id)
            pushed = state is not None and state.last_pushed_commit == info.commit
        if len(info.parents) > 1:
            return False
        age = self.clock() - info.authored_at
        return should_squash(info.author, age, repository.on_any_remote(info.commit), pushed)

    @staticmethod
    def read_card(repository: BoardRepository, card_id: int, head: str | None) -> Card:
        content = repository.read(card_path(card_id), head) if head is not None else None
        if content is None:
            raise CardNotFound(card_id)
        parsed = parse_file_card(content)
        if isinstance(parsed, BrokenCard):
            raise CardBroken(card_id, parsed.error)
        return parsed

    def facts(self, project_id: int, card: Card) -> CardFacts:
        """The flow's facts for a card as just read, with its plan, review and the cards it waits for."""
        with self.session_factory() as session:
            plan = session.get(PlanRecord, card.links.plan_id) if card.links.plan_id is not None else None
            review = session.get(Review, card.links.review_id) if card.links.review_id is not None else None
            query = select(BoardCardRow.card_id, BoardCardRow.board_column).where(
                BoardCardRow.project_id == project_id, BoardCardRow.card_id.in_(card.waits_for)
            )
            columns = {card_id: column for card_id, column in session.execute(query)}
        return CardFacts(
            column=card.column,
            plan_status=plan.status if plan is not None else None,
            review=review_from_status(review.status if review is not None else None),
            open_waits=tuple(card_id for card_id in card.waits_for if columns.get(card_id, "done") != "done"),
            open_questions=sum(1 for question in card.questions if question.answer is None),
            has_specification=bool(card.specification.strip()),
        )

    def write_card(self, opened: Opened, card_id: int, change: Callable[[Card], Card | None], tally: Tally) -> None:
        """Read the card at the head, change it and write it; `change` returning None writes nothing."""

        def build(head: str | None) -> Planned[None]:
            card = change(self.read_card(opened.repository, card_id, head))
            changes: tuple[Change, ...] = () if card is None else (Put(card_path(card_id), render_card(card)),)
            return Planned(changes, tally, None)

        self.write(opened, build)

    # --- the board -----------------------------------------------------------------------------------------------

    def enable(self, project_id: int, storage: StorageKind = "branch", create_repository: bool = False) -> BoardView:
        """Start the project's board. The branch itself is made with the first card, so nothing is committed now."""
        project = self.project(project_id)
        if storage != self.storage.kind:
            raise BoardUnavailable("That place for the board is not available.")
        with self.session_factory() as session:
            if not board_enabled(session, project.environment_id, project.id).enabled:
                raise BoardOff()
            if session.get(BoardState, project.id) is not None:
                raise BoardExists()
        if not self.storage.has_repository(project):
            if not create_repository:
                raise NoRepository()
            self.storage.create_repository(project)
        repository = self.storage.open(project)
        repository.head()  # fails early, before the state is stored, when git cannot read the repository
        with self.session_factory() as session:
            session.add(BoardState(project_id=project.id, storage=storage, next_card_id=1, created_at=self.clock()))
            try:
                session.commit()
            except IntegrityError as error:  # started at the same moment by another request
                raise BoardExists() from error
        self.indexer.rebuild(project.id, repository)
        return self.overview(project_id, None)

    def overview(self, project_id: int, since: str | None, reconcile: bool = True) -> BoardView:
        """The board. Opening it (no `since`) also brings cards up to date with plans that changed meanwhile."""
        project = self.project(project_id)
        with self.session_factory() as session:
            switch = board_enabled(session, project.environment_id, project.id)
            state = session.get(BoardState, project.id)
            push_allowed = board_push_allowed(session, project.environment_id, project.id)
            last_pushed = state.last_pushed_commit if state is not None else None
        if not switch.enabled:
            return BoardView(False, switch.source, "off", message="The board is switched off for this project.")
        if state is None:
            message = None if self.storage.has_repository(project) else NoRepository.default_sentence
            return BoardView(True, switch.source, "not_started", message=message)
        info = self.storage.describe(project)
        try:
            repository = self.storage.open(project)
            head = repository.head()
            if since is not None and since == head:
                return BoardView(True, switch.source, "ready", info.kind, info.location, head, unchanged=True)
            self.indexer.sync(project.id, repository)
            versions = self.version_files(repository, head)
        except BoardUnavailable as error:
            return BoardView(True, switch.source, "unavailable", info.kind, info.location, message=str(error))
        if reconcile and since is None and head is not None:
            try:
                self.reconcile_plans(project_id)
            except BoardError as error:  # the board is shown as it is; the next opening tries again
                logger.warning("Board of project %s not reconciled with its plans: %s", project_id, error.code)
        return self.board_view(
            project.id, switch.source, info.kind, info.location, head, versions, push_allowed, last_pushed
        )

    @staticmethod
    def version_files(repository: BoardRepository, head: str | None) -> dict[str, Version | BrokenVersion]:
        if head is None:
            return {}
        files = [entry.path for entry in repository.list_files(head) if entry.path.startswith("versions/")]
        found: dict[str, Version | BrokenVersion] = {}
        for path, content in repository.read_many(files, head).items():
            name = path.removeprefix("versions/").removesuffix(".md")
            found[name] = (
                parse_version(content.text, path)
                if content.text is not None
                else BrokenVersion(path, name, "The file could not be read.")
            )
        return found

    def board_view(
        self,
        project_id: int,
        source: Source,
        storage: StorageKind,
        location: str,
        head: str | None,
        versions: Mapping[str, Version | BrokenVersion],
        push_allowed: bool,
        last_pushed: str | None,
    ) -> BoardView:
        with self.session_factory() as session:
            # Without a head the branch is gone; its rows are kept for when it comes back, but not shown.
            query = select(BoardCardRow).where(BoardCardRow.project_id == project_id)
            rows = list(session.scalars(query)) if head is not None else []
            version_query = select(BoardVersionRow).where(BoardVersionRow.project_id == project_id)
            version_rows = list(session.scalars(version_query))
            related = Related.read(session, project_id, rows)
        views = sorted((card_view(row, related) for row in rows), key=lambda view: view.id)
        version_list = version_views(version_rows, versions, [view for view in views if not view.broken])
        return BoardView(
            enabled=True,
            enabled_source=source,
            state="ready",
            storage=storage,
            location=location,
            head=head,
            columns=columns_of(view for view in views if not view.broken),
            broken=tuple(view for view in views if view.broken),
            versions=version_list,
            current_version=current_version(version_list),
            push=PushView(push_allowed, head is not None and head == last_pushed),
        )

    # --- cards ---------------------------------------------------------------------------------------------------

    def definition_for(self, project: Project, kind: Kind) -> str:
        if self.blueprint_dir is None or self.workspace_dir is None:
            return default_definition(kind)
        with self.session_factory() as session:
            environment = session.get(Environment, project.environment_id)
        return default_definition(kind, layers_for(self.blueprint_dir, self.workspace_dir, environment, project))

    def create_card(self, project_id: int, request: NewCard) -> CardDetail:
        opened = self.opened(project_id)
        definition = self.definition_for(opened.project, request.kind)

        def build(head: str | None) -> Planned[int]:
            with self.session_factory() as session:
                count = session.scalar(
                    select(func.count()).select_from(BoardCardRow).where(BoardCardRow.project_id == project_id)
                )
                state = session.get(BoardState, project_id)
                next_id = state.next_card_id if state is not None else 1
            if (count or 0) >= MAX_CARDS:
                raise BoardFull(f"A board holds at most {MAX_CARDS} cards.")
            on_branch = [entry.path for entry in opened.repository.list_files(head) if entry.path.startswith("cards/")]
            highest = max((int(path.removeprefix("cards/").removesuffix(".md")) for path in on_branch), default=0)
            card = new_card(max(next_id, highest + 1), request, definition, self.clock())
            return Planned((Put(card_path(card.id), render_card(card)),), {"added": 1}, card.id)

        card_id = self.write(opened, build)
        return self.detail(opened, card_id)

    def get_card(self, project_id: int, card_id: int) -> CardDetail:
        return self.detail(self.opened(project_id), card_id)

    def detail(self, opened: Opened, card_id: int) -> CardDetail:
        project_id = opened.project.id
        with self.session_factory() as session:
            row = session.scalar(
                select(BoardCardRow).where(BoardCardRow.project_id == project_id, BoardCardRow.card_id == card_id)
            )
            if row is None:
                raise CardNotFound(card_id)
            related = Related.read(session, project_id, [row])
            linked = conversations_of(session, project_id, card_id)
            conversations = tuple(LinkedConversation(*found) for found in linked)
        view = card_view(row, related)
        content = opened.repository.read(card_path(card_id))
        parsed = parse_file_card(content) if content is not None else None
        card = parsed if isinstance(parsed, Card) else None
        broken_text = parsed.text if isinstance(parsed, BrokenCard) else None
        items = tuple(parse_checklist(card.definition_of_done)) if card is not None else ()
        location = self.storage.describe(opened.project).location
        return CardDetail(view, card, broken_text, items, conversations, location)

    def update_card(
        self, project_id: int, card_id: int, patch: Mapping[str, object], expected_updated: datetime | None = None
    ) -> CardDetail:
        opened = self.opened(project_id)
        with self.session_factory() as session:
            known = set(session.scalars(select(BoardCardRow.card_id).where(BoardCardRow.project_id == project_id)))

        def change(card: Card) -> Card:
            self.check_unchanged(card, expected_updated)
            return patched(card, patch, known, self.clock())

        self.guarded(opened, card_id, lambda: self.write_card(opened, card_id, change, {"changed": 1}))
        return self.detail(opened, card_id)

    @staticmethod
    def check_unchanged(card: Card, expected_updated: datetime | None) -> None:
        if expected_updated is None:
            return
        expected = expected_updated if expected_updated.tzinfo else expected_updated.replace(tzinfo=UTC)
        if expected.replace(microsecond=0) != card.updated:
            raise CardChanged(card.id)

    def guarded(self, opened: Opened, card_id: int, action: Callable[[], None]) -> None:
        """Run a card write; a CardChanged carries the card as it is now."""
        try:
            action()
        except CardChanged as error:
            raise CardChanged(card_id, self.detail(opened, card_id)) from error

    def move_card(
        self,
        project_id: int,
        card_id: int,
        column: Column,
        actor: Actor = "user",
        expected_updated: datetime | None = None,
    ) -> CardDetail:
        opened = self.opened(project_id)

        def change(card: Card) -> Card | None:
            self.check_unchanged(card, expected_updated)
            outcome = step(self.facts(project_id, card), "move", actor, column)
            if isinstance(outcome, Refusal):
                raise MoveRefused(outcome.code, outcome.reason)
            return None if outcome.column == card.column else moved(card, outcome.column, self.clock())

        self.guarded(opened, card_id, lambda: self.write_card(opened, card_id, change, {"moved": 1}))
        return self.detail(opened, card_id)

    def apply_event(
        self,
        project_id: int,
        card_id: int,
        event: CardEvent,
        actor: Actor,
        target: Column | None = None,
        reason: str = "",
    ) -> CardDetail | None:
        """A stage event on the card (a drop answers None). A send back also cancels the card's plan."""
        if event == "drop":
            self.drop_card(project_id, card_id, actor)
            return None
        opened = self.opened(project_id)
        plan_to_cancel: list[int] = []

        def change(card: Card) -> Card | None:
            outcome = step(self.facts(project_id, card), event, actor, target)
            if isinstance(outcome, Refusal):
                raise MoveRefused(outcome.code, outcome.reason)
            if event == "send_back":
                if card.links.plan_id is not None:
                    plan_to_cancel[:] = [card.links.plan_id]
                # The plan is made again, so the card no longer points at the old one (a done plan stays done).
                return with_plan(sent_back(card, reason, self.clock()), None)
            return None if outcome.column == card.column else moved(card, outcome.column, self.clock())

        self.write_card(opened, card_id, change, {"moved": 1})
        for plan_id in plan_to_cancel:
            self.cancel_plan(plan_id)
        return self.detail(opened, card_id)

    def cancel_plan(self, plan_id: int) -> None:
        """The plan of a card sent back is made again, so the old one is cancelled when its status allows it."""
        with self.session_factory() as session:
            store = PlanStore(session)
            record = store.get(plan_id)
            if record is not None and next_status(record.status, "replan") == "cancelled":
                store.set_status(record.id, record.status, "cancelled")
                session.commit()

    def drop_card(self, project_id: int, card_id: int, actor: Actor = "user") -> None:
        """Delete the card's file (its history keeps it). The id is never given out again."""
        opened = self.opened(project_id)

        def build(head: str | None) -> Planned[None]:
            card = self.read_card(opened.repository, card_id, head)
            outcome = step(self.facts(project_id, card), "drop", actor)
            if isinstance(outcome, Refusal):
                if outcome.code == "build_running":
                    raise CardBuilding()
                raise MoveRefused(outcome.code, outcome.reason)
            return Planned((Delete(card_path(card_id)),), {"dropped": 1}, None)

        self.write(opened, build)
        with self.session_factory() as session:
            forget_card(session, project_id, card_id)
            session.commit()

    def answer_question(self, project_id: int, card_id: int, index: int, text: str) -> CardDetail:
        opened = self.opened(project_id)
        self.write_card(opened, card_id, lambda card: answered(card, index, text, self.clock()), {"changed": 1})
        return self.detail(opened, card_id)

    # --- links ---------------------------------------------------------------------------------------------------

    def link_conversation(self, project_id: int, card_id: int, conversation_id: int, role: ConversationRole) -> None:
        opened = self.opened(project_id)
        self.write_card(opened, card_id, lambda card: with_conversation(card, conversation_id), {"changed": 1})
        with self.session_factory() as session:
            record_conversation(session, project_id, card_id, conversation_id, role)
            session.commit()

    def link_plan(self, project_id: int, card_id: int, plan_id: int) -> None:
        opened = self.opened(project_id)
        self.write_card(opened, card_id, lambda card: with_plan(card, plan_id), {"changed": 1})
        with self.session_factory() as session:
            record = session.get(PlanRecord, plan_id)
            if record is not None and record.project_id == project_id:
                record.card_id = card_id
                session.commit()

    def reconcile_plans(self, project_id: int) -> int:
        """Bring cards up to date with plans that changed while the board was off: a card's newest plan is linked
        on the card. No card moves. Returns how many cards were written."""
        opened = self.opened(project_id)
        with self.session_factory() as session:
            query = select(BoardCardRow.card_id, BoardCardRow.plan_id).where(
                BoardCardRow.project_id == project_id, BoardCardRow.broken.is_(False)
            )
            linked = {card_id: plan_id for card_id, plan_id in session.execute(query)}
            newest: dict[int, int] = {}
            for plan_id, card_id in session.execute(
                select(PlanRecord.id, PlanRecord.card_id)
                .where(
                    PlanRecord.project_id == project_id,
                    PlanRecord.card_id.is_not(None),
                    PlanRecord.status != "cancelled",
                )
                .order_by(PlanRecord.id)
            ):
                if card_id is not None and card_id in linked:
                    newest[card_id] = plan_id
        stale = {card_id: plan_id for card_id, plan_id in newest.items() if linked.get(card_id) != plan_id}
        if not stale:
            return 0

        def build(head: str | None) -> Planned[int]:
            changes: list[Change] = []
            for card_id, plan_id in sorted(stale.items()):
                try:
                    card = self.read_card(opened.repository, card_id, head)
                except (CardNotFound, CardBroken):
                    continue  # a card dropped or broken since the index was read is left alone
                changes.append(Put(card_path(card_id), render_card(with_plan(card, plan_id))))
            return Planned(tuple(changes), {"changed": len(changes)}, len(changes))

        return self.write(opened, build)

    # --- versions and push ---------------------------------------------------------------------------------------

    def put_version(self, project_id: int, name: str, changes: Mapping[str, object]) -> VersionView:
        checked_version_name(name)
        opened = self.opened(project_id)
        path = version_path(name)

        def build(head: str | None) -> Planned[None]:
            content = opened.repository.read(path, head) if head is not None else None
            existing: Version | None = None
            if content is not None:
                parsed = parse_version(content.text, path) if content.text is not None else None
                if not isinstance(parsed, Version):
                    raise InvalidVersion("The version's file cannot be read; fix it on the board's branch first.")
                existing = parsed
            else:
                versions = [entry for entry in opened.repository.list_files(head) if entry.path.startswith("versions/")]
                if len(versions) >= MAX_VERSIONS:
                    raise BoardFull(f"A board holds at most {MAX_VERSIONS} versions.")
            version = put_version(existing, name, changes, self.clock())
            return Planned((Put(path, render_version(version)),), {"versions": 1}, None)

        self.write(opened, build)
        return self.version(project_id, name)

    def version(self, project_id: int, name: str) -> VersionView:
        view = self.overview(project_id, None, reconcile=False)
        found = next((version for version in view.versions if version.name == name), None)
        if found is None:
            raise InvalidVersion("There is no such version.")
        return found

    def push(self, project_id: int, acknowledge_public: bool, github_token: Callable[[Project], str | None]) -> str:
        """Push the board's branch to the project's remote; returns the pushed commit. The token is read only once
        the push is allowed."""
        opened = self.opened(project_id)
        with self.session_factory() as session:
            if not board_push_allowed(session, opened.project.environment_id, project_id):
                raise PushNotAllowed()
        head = opened.repository.head()
        if head is None:
            raise BoardNotStarted("The board has no cards yet, so there is nothing to push.")
        branch = opened.repository.ref.removeprefix("refs/heads/")
        self.pusher.push(opened.repository.repository, branch, head, github_token(opened.project), acknowledge_public)
        with self.session_factory() as session:
            state = session.get(BoardState, project_id)
            if state is not None:
                state.last_pushed_commit = head
                session.commit()
        return head

