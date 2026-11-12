"""Where a project's board files are kept. `BranchStorage` keeps them on the orphan branch `yaap-board` of the
project's own repository, written with git's plumbing so the work tree, the index and the checked-out branch never
change. Git for a project in the workspace runs where `git_place` says (the runner when it is on)."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.board.errors import BoardUnavailable
from app.board.models import StorageKind
from app.board.paths import DEFAULT_REF
from app.board.repository import BoardRepository
from app.git.common import GitError
from app.git.place import GitPlace, git_place
from app.git.service import init_repository
from app.projects.service import ProjectError, project_path
from app.storage.models import Project

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
