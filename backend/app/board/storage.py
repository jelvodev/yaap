"""Where a project's board files are kept. `BranchStorage` keeps them on the orphan branch `yaap-board` of the
project's own repository, written with git's plumbing so the work tree, the index and the checked-out branch never
change. Git for a project in the workspace runs where `git_place` says (the runner when it is on).
`RepositoryStorage` keeps them in a separate private repository, through a bare clone only YAAP writes
(`app.board.remote`). `BoardStorages` picks the one a project's board was started with."""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from app.board.errors import BoardError, BoardNotStarted, BoardOff, BoardUnavailable, NoRemote, PublicRemote
from app.board.models import BoardState, StorageKind
from app.board.paths import DEFAULT_REF
from app.board.publish import BoardPusher, PublicCheck, github_public, may_push
from app.board.remote import BareBoardRepository, RemoteBoard, UrlCheck, board_repository_path, checked_board_url
from app.board.repository import BoardRepository
from app.board.settings import BOARD_REPOSITORY_URL_KEY, BOARD_STORAGE_KEY
from app.board.switch import board_enabled
from app.core.settings import SETTING_DEFINITIONS, resolve_setting, store_setting
from app.credentials.crypto import CredentialCipher, CredentialDecryptionError
from app.credentials.store import read_credential_value
from app.git.common import GitError
from app.git.place import GitPlace, git_place
from app.git.remote import validate_remote_url
from app.git.service import Repository, init_repository, remote_url
from app.projects.service import ProjectError, project_path
from app.storage.models import Project

logger = logging.getLogger(__name__)

PlaceOf = Callable[[int, Path], GitPlace]


@dataclass(frozen=True)
class StorageInfo:
    """Where the board is, in words that are safe to show."""

    kind: StorageKind
    ref: str
    location: str


class BoardStorage(Protocol):
    kind: StorageKind

    def open(self, project: Project) -> BoardRepository:
        """The project's board repository; raises BoardUnavailable when git cannot be used for it."""
        ...

    def describe(self, project: Project) -> StorageInfo: ...

    def has_repository(self, project: Project) -> bool: ...

    def create_repository(self, project: Project) -> None: ...


@dataclass(frozen=True)
class BranchStorage:
    """The board on `refs/heads/yaap-board` of the project's own repository."""

    workspace_dir: Path
    place_of: PlaceOf = git_place
    ref: str = DEFAULT_REF
    kind: StorageKind = "branch"

    def folder(self, project: Project) -> Path:
        try:
            return project_path(self.workspace_dir, project)
        except ProjectError as error:
            raise BoardUnavailable(str(error)) from error

    def place(self, project: Project) -> GitPlace:
        try:
            return self.place_of(project.environment_id, self.folder(project))
        except GitError as error:
            raise BoardUnavailable("Git cannot be used for this project right now.", detail=str(error)) from error

    def open(self, project: Project) -> BoardRepository:
        return BoardRepository(self.place(project), self.ref)

    def describe(self, project: Project) -> StorageInfo:
        branch = self.ref.removeprefix("refs/heads/")
        return StorageInfo(self.kind, self.ref, f"Branch {branch} of the project's repository")

    def has_repository(self, project: Project) -> bool:
        git_folder = self.folder(project) / ".git"
        return git_folder.is_dir() and not git_folder.is_symlink()

    def create_repository(self, project: Project) -> None:
        """An empty repository in the project folder; nothing of the project is committed."""
        try:
            init_repository(self.place(project))
        except GitError as error:
            raise BoardUnavailable("The project's repository could not be made.", detail=str(error)) from error


# --- a separate repository -------------------------------------------------------------------------------------------

ProjectText = Callable[[Project], str | None]
# How long after a pull the board is read without pulling again.
PULL_INTERVAL_SECONDS = 60.0


def pull_due(last: float | None, now: float, interval: float = PULL_INTERVAL_SECONDS) -> bool:
    return last is None or now - last >= interval


def start_thread(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="board-pull", daemon=True).start()


@dataclass
class PullSchedule:
    """At most one automatic pull a minute per board, in the background, so reading never waits for the network.
    One lock per board keeps pulls of the same clone apart."""

    start: Callable[[Callable[[], None]], None] = start_thread
    clock: Callable[[], float] = time.monotonic
    last: dict[int, float] = field(default_factory=dict)
    locks: dict[int, threading.Lock] = field(default_factory=dict)
    guard: threading.Lock = field(default_factory=threading.Lock)

    def lock(self, project_id: int) -> threading.Lock:
        """The board's lock; taking it for a pull also counts as the last pull."""
        with self.guard:
            self.last[project_id] = self.clock()
            return self.locks.setdefault(project_id, threading.Lock())

    def maybe(self, project_id: int, pull: Callable[[], object]) -> None:
        with self.guard:
            if not pull_due(self.last.get(project_id), self.clock()):
                return
            self.last[project_id] = self.clock()

        def run() -> None:
            lock = self.lock(project_id)
            if not lock.acquire(blocking=False):
                return
            try:
                pull()
            except BoardError as error:  # offline or refused: the board is read as it is, Pull says why
                logger.info("Automatic pull of the board of project %s skipped: %s", project_id, error.code)
            except Exception:  # a background thread must log instead of dying silently
                logger.exception("Automatic pull of the board of project %s failed.", project_id)
            finally:
                lock.release()

        self.start(run)


@dataclass(frozen=True)
class RemoteCheck:
    ok: bool
    # Whether the repository is private; None when YAAP cannot tell.
    private: bool | None
    message: str | None = None


@dataclass(frozen=True)
class RepositoryStorage:
    """The board on the default branch of a separate repository, through a bare clone in YAAP's data folder."""

    boards_dir: Path
    url_of: ProjectText
    token_of: ProjectText
    # The project's own remote, which the board's repository must not be.
    project_remote: ProjectText
    schedule: PullSchedule = field(default_factory=PullSchedule)
    url_check: UrlCheck = validate_remote_url
    is_public: PublicCheck = github_public
    kind: StorageKind = "repository"

    def remote(self, project: Project) -> RemoteBoard:
        return RemoteBoard(board_repository_path(self.boards_dir, project.id))

    def url(self, project: Project, url: str | None = None) -> str:
        return checked_board_url(url or self.url_of(project), self.project_remote(project), self.url_check)

    def open(self, project: Project) -> BoardRepository:
        """The clone's board; a pull is started in the background when one is due. Without a clone yet (a restore
        without it, or a first clone that failed) the background pull makes it, and the board is unavailable now."""
        remote = self.remote(project)
        self.schedule.maybe(project.id, lambda: self.pull_quietly(project))
        if not remote.exists():
            raise BoardUnavailable("The board's repository is not here yet. Use Pull to fetch it.")
        try:
            return BareBoardRepository(remote.path, remote.default_ref())
        except GitError as error:
            raise BoardUnavailable("The board's repository cannot be read.", detail=str(error)) from error

    def describe(self, project: Project) -> StorageInfo:
        url = self.url_of(project)
        return StorageInfo(self.kind, "", f"Repository {url}" if url else "A separate repository")

    def has_repository(self, project: Project) -> bool:
        return self.remote(project).exists()

    def create_repository(self, project: Project) -> None:
        self.pull(project)

    def pull(self, project: Project, url: str | None = None) -> str | None:
        """Clone when needed, fetch and fast-forward; returns the board's head. Raises PullFailed, BoardDiverged,
        NoRemote or InvalidRepositoryUrl."""
        checked = self.url(project, url)
        token = self.token_of(project)
        remote = self.remote(project)
        with self.schedule.lock(project.id):
            remote.ensure_clone(checked, token)
            return remote.pull(checked, token)

    def pull_quietly(self, project: Project) -> None:
        """The automatic pull. The URL was checked against the project's remote when the board started, so the
        project's runner is not woken for that every minute; the lock is already held."""
        checked = checked_board_url(self.url_of(project), None, self.url_check)
        token = self.token_of(project)
        remote = self.remote(project)
        remote.ensure_clone(checked, token)
        remote.pull(checked, token)

    def push(self, project: Project, commit: str, token: str | None, acknowledge_public: bool) -> None:
        checked = self.url(project)
        if not may_push(self.is_public(checked, token), acknowledge_public):
            raise PublicRemote("The board's repository may be public: pushing publishes every card. Confirm to push.")
        with self.schedule.lock(project.id):
            self.remote(project).push(checked, token, commit)

    def check(self, project: Project, url: str) -> RemoteCheck:
        """Whether YAAP can reach the repository with the project's token, and whether it is private."""
        try:
            checked = self.url(project, url)
        except BoardError as error:
            return RemoteCheck(False, None, str(error))
        token = self.token_of(project)
        try:
            self.remote(project).check_access(checked, token)
        except BoardError as error:
            return RemoteCheck(False, None, str(error))
        public = self.is_public(checked, token)
        return RemoteCheck(True, None if public is None else not public)


@dataclass(frozen=True)
class BoardStorages:
    """The storage of each project's board: the one it was started with (the branch before it starts)."""

    session_factory: sessionmaker[Session]
    branch: BranchStorage
    repository: RepositoryStorage

    def kind_of(self, project: Project) -> StorageKind:
        with self.session_factory() as session:
            state = session.get(BoardState, project.id)
            return state.storage if state is not None else "branch"

    def get(self, kind: StorageKind) -> BranchStorage | RepositoryStorage:
        return self.repository if kind == "repository" else self.branch

    def of(self, project: Project) -> BranchStorage | RepositoryStorage:
        return self.get(self.kind_of(project))

    def open(self, project: Project) -> BoardRepository:
        return self.of(project).open(project)

    def describe(self, project: Project) -> StorageInfo:
        return self.of(project).describe(project)

    def has_repository(self, project: Project) -> bool:
        return self.of(project).has_repository(project)

    def create_repository(self, project: Project) -> None:
        self.of(project).create_repository(project)

    def link_repository(self, project: Project, url: str | None) -> None:
        """Before a board starts in a separate repository: check the URL, clone it, and keep the choice in the
        project's settings."""
        checked = self.repository.url(project, url)
        self.repository.pull(project, checked)
        environment_id = project.environment_id
        with self.session_factory() as session:
            store_setting(session, SETTING_DEFINITIONS[BOARD_STORAGE_KEY], environment_id, "repository", project.id)
            store_setting(session, SETTING_DEFINITIONS[BOARD_REPOSITORY_URL_KEY], environment_id, checked, project.id)

    def pull(self, project: Project) -> str | None:
        """Pull a started board in a separate repository; a board on the project's branch has none."""
        with self.session_factory() as session:
            if not board_enabled(session, project.environment_id, project.id).enabled:
                raise BoardOff()
            state = session.get(BoardState, project.id)
            if state is None:
                raise BoardNotStarted()
            kind = state.storage
        if kind != "repository":
            raise NoRemote("This board is on the project's own branch; it comes and goes with the project's pull.")
        return self.repository.pull(project)

    def push(
        self,
        project: Project,
        repository: Repository,
        branch: str,
        commit: str,
        token: str | None,
        acknowledge_public: bool,
        pusher: BoardPusher,
    ) -> None:
        """Push the board where it is kept: its repository's default branch, or the branch to the project's remote."""
        if self.kind_of(project) == "repository":
            self.repository.push(project, commit, token, acknowledge_public)
        else:
            pusher.push(repository, branch, commit, token, acknowledge_public)


def board_storages(
    session_factory: sessionmaker[Session], cipher: CredentialCipher, workspace_dir: Path, data_dir: Path
) -> BoardStorages:
    """The storages as YAAP runs them: the project's GitHub token and settings read from the database."""
    branch = BranchStorage(workspace_dir)

    def url_of(project: Project) -> str | None:
        with session_factory() as session:
            definition = SETTING_DEFINITIONS[BOARD_REPOSITORY_URL_KEY]
            value = resolve_setting(session, definition, project.environment_id, project.id).value
        return value if isinstance(value, str) and value else None

    def token_of(project: Project) -> str | None:
        with session_factory() as session:
            try:
                return read_credential_value(
                    session, cipher, project.environment_id, "github_token", project.id, f"Board: {project.name}"
                )
            except CredentialDecryptionError:
                return None

    def project_remote(project: Project) -> str | None:
        try:
            return remote_url(branch.place(project))
        except BoardUnavailable:
            return None

    repository = RepositoryStorage(data_dir, url_of, token_of, project_remote)
    return BoardStorages(session_factory, branch, repository)
