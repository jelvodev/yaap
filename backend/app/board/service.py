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

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.blueprint.levels import layers_for
from app.board.card import BrokenCard, Card, Column, Kind, render_card
from app.board.card_detail import card_detail, existing_version, free_card_id
from app.board.defaults import default_definition
from app.board.errors import (
    BoardConflict,
    BoardError,
    BoardExists,
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
from app.board.flow import Actor, CardEvent, CardFacts, Refusal, step
from app.board.index import BoardIndexer, parse_file_card
from app.board.links import forget_card, record_conversation, with_conversation, with_plan
from app.board.models import BoardCardRow, BoardState, ConversationRole, StorageKind
from app.board.operations import (
    NewCard,
    answered,
    checked_version_name,
    moved,
    new_card,
    patched,
    put_version,
    sent_back,
)
from app.board.overview import board_view, version_files
from app.board.paths import card_path, version_path
from app.board.plan_links import cancel_plan, card_facts, record_plan_card, stale_plan_links
from app.board.planning import Opened, Planned, utc_now
from app.board.publish import BoardPusher
from app.board.repository import BoardRepository, Change, CommitInfo, Delete, Put
from app.board.settings import push_offered
from app.board.squash import Tally, board_message, should_squash, squash_message
from app.board.storage import BoardStorages
from app.board.switch import board_enabled, board_push_allowed
from app.board.versions import render_version
from app.board.views import BoardView, CardDetail, VersionView
from app.storage.models import Environment, Project

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BoardService:
    session_factory: sessionmaker[Session]
    storage: BoardStorages
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
        return card_facts(self.session_factory, project_id, card)

    def write_card(self, opened: Opened, card_id: int, change: Callable[[Card], Card | None], tally: Tally) -> None:
        """Read the card at the head, change it and write it; `change` returning None writes nothing."""

        def build(head: str | None) -> Planned[None]:
            card = change(self.read_card(opened.repository, card_id, head))
            changes: tuple[Change, ...] = () if card is None else (Put(card_path(card_id), render_card(card)),)
            return Planned(changes, tally, None)

        self.write(opened, build)

    # --- the board -----------------------------------------------------------------------------------------------

    def enable(
        self,
        project_id: int,
        storage: StorageKind = "branch",
        create_repository: bool = False,
        repository_url: str | None = None,
    ) -> BoardView:
        """Start the project's board. The branch itself is made with the first card, so nothing is committed now; a
        separate repository is cloned first."""
        project = self.project(project_id)
        chosen = self.storage.get(storage)
        with self.session_factory() as session:
            if not board_enabled(session, project.environment_id, project.id).enabled:
                raise BoardOff()
            if session.get(BoardState, project.id) is not None:
                raise BoardExists()
        if storage == "repository":
            self.storage.link_repository(project, repository_url)
        elif not chosen.has_repository(project):
            if not create_repository:
                raise NoRepository()
            chosen.create_repository(project)
        repository = chosen.open(project)
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
            board_push = board_push_allowed(session, project.environment_id, project.id)
            push_allowed = push_offered(state.storage if state is not None else "branch", board_push)
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
            versions = version_files(repository, head)
        except BoardUnavailable as error:
            return BoardView(True, switch.source, "unavailable", info.kind, info.location, message=str(error))
        if reconcile and since is None and head is not None:
            try:
                self.reconcile_plans(project_id)
            except BoardError as error:  # the board is shown as it is; the next opening tries again
                logger.warning("Board of project %s not reconciled with its plans: %s", project_id, error.code)
        return board_view(
            self.session_factory,
            project.id, switch.source, info.kind, info.location, head, versions, push_allowed, last_pushed
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
            next_id = free_card_id(self.session_factory, opened.repository, project_id, head)
            card = new_card(next_id, request, definition, self.clock())
            return Planned((Put(card_path(card.id), render_card(card)),), {"added": 1}, card.id)

        card_id = self.write(opened, build)
        return self.detail(opened, card_id)

    def get_card(self, project_id: int, card_id: int) -> CardDetail:
        return self.detail(self.opened(project_id), card_id)

    def detail(self, opened: Opened, card_id: int) -> CardDetail:
        return card_detail(self.session_factory, self.storage, opened, card_id)

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
            cancel_plan(self.session_factory, plan_id)
        return self.detail(opened, card_id)

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
        record_plan_card(self.session_factory, project_id, card_id, plan_id)

    def reconcile_plans(self, project_id: int) -> int:
        """Bring cards up to date with plans that changed while the board was off: a card's newest plan is linked
        on the card. No card moves. Returns how many cards were written."""
        opened = self.opened(project_id)
        stale = stale_plan_links(self.session_factory, project_id)
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
            existing = existing_version(opened.repository, path, head)
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
        """Push the board's branch where it is kept; returns the pushed commit. The token is read only once the push
        is allowed."""
        opened = self.opened(project_id)
        with self.session_factory() as session:
            board_push = board_push_allowed(session, opened.project.environment_id, project_id)
        if not push_offered(self.storage.kind_of(opened.project), board_push):
            raise PushNotAllowed()
        head = opened.repository.head()
        if head is None:
            raise BoardNotStarted("The board has no cards yet, so there is nothing to push.")
        branch = opened.repository.ref.removeprefix("refs/heads/")
        token = github_token(opened.project)
        place = opened.repository.repository
        self.storage.push(opened.project, place, branch, head, token, acknowledge_public, self.pusher)
        with self.session_factory() as session:
            state = session.get(BoardState, project_id)
            if state is not None:
                state.last_pushed_commit = head
                session.commit()
        return head

