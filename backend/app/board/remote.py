"""A board kept in a separate repository: a bare clone at `<data>/boards/<project id>.git`.

The clone lies outside every runner mount and only YAAP writes it, so, like the git transport, it is where the GitHub
token is used: as a setting in git's environment (`private_config`), never on a command line, in a stored config or
in a log. Git is told the repository explicitly (`GIT_DIR`; YAAP's git uses a bare repository only when named), and
`check_repository` reads the clone's own config before and after every command, as for any repository.

The board lives on the repository's default branch. A pull fetches into `refs/remotes/origin/*` and only
fast-forwards that branch; when both sides moved, nothing changes (`BoardDiverged`). A push sends one commit to that
one branch, never forced."""

import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.board.errors import BoardDiverged, BoardUnavailable, InvalidRepositoryUrl, NoRemote, PullFailed, PushFailed
from app.board.paths import check_ref
from app.board.repository import SCRATCH_PREFIX, BoardRepository, regular_members, remove_scratch
from app.board.settings import same_remote
from app.git.bundles import safe_branch_name
from app.git.common import DEFAULT_BRANCH, GIT_TIMEOUT_SECONDS, GitError, GitResult
from app.git.remote import auth_config, validate_remote_url
from app.git.safety import RECEIVE_PACK, UPLOAD_PACK, is_bare_layout
from app.git.service import folder_of, run_git
from app.git.transport import CLONE_TIMEOUT, NETWORK_TIMEOUT, remove_transport

BOARDS_FOLDER = "boards"
TRACKING_PREFIX = "refs/remotes/origin/"
FETCH_REFSPEC = "+refs/heads/*:refs/remotes/origin/*"
# What comes from the remote is data; git checks every object it receives.
CHECK_OBJECTS = ("transfer.fsckObjects=true",)
REJECTED = ("rejected", "fetch first", "non-fast-forward")

PullStep = Literal["nothing", "take", "ahead", "diverged"]
UrlCheck = Callable[[str], str]


def board_repository_path(data_dir: Path, project_id: int) -> Path:
    return data_dir / BOARDS_FOLDER / f"{project_id}.git"


def pull_step(local: str | None, remote: str | None, local_in_remote: bool, remote_in_local: bool) -> PullStep:
    """What a pull does with the board's branch: nothing (no remote branch, or the same commit), take the remote's
    commit (no local branch yet, or a fast-forward), nothing because only the board here moved (`ahead`, a push is
    due), or refuse because both moved."""
    if remote is None or local == remote:
        return "nothing"
    if local is None or local_in_remote:
        return "take"
    if remote_in_local:
        return "ahead"
    return "diverged"


def checked_board_url(url: str | None, project_remote: str | None, url_check: UrlCheck = validate_remote_url) -> str:
    """The URL of a board's repository, when it is one YAAP may use: set, a plain https URL without credentials, and
    not the project's own remote."""
    if not url:
        raise NoRemote("This board has no repository URL yet (the board_repository_url setting).")
    try:
        checked = url_check(url)
    except GitError as error:
        raise InvalidRepositoryUrl() from error
    if same_remote(checked, project_remote):
        raise InvalidRepositoryUrl(
            "That is the project's own repository. Use a separate private repository, so the board is not published "
            "with the project."
        )
    return checked


@dataclass(frozen=True)
class RemoteBoard:
    """The bare clone of a board's repository."""

    path: Path

    def exists(self) -> bool:
        return is_bare_layout(self.path)

    def git(
        self,
        arguments: Sequence[str],
        *,
        authorization: Sequence[tuple[str, str]] = (),
        config: Sequence[str] = (),
        timeout: int = GIT_TIMEOUT_SECONDS,
        accept: Sequence[int] = (0,),
    ) -> GitResult:
        return run_git(
            arguments,
            self.path,
            {"GIT_DIR": str(self.path)},
            config=config,
            timeout=timeout,
            accept_exit_codes=accept,
            private_config=authorization,
        )

    def rev(self, ref: str) -> str | None:
        return self.git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], accept=(0, 1)).stdout.strip() or None

    def default_ref(self) -> str:
        """The branch the board is on: the repository's default branch, as the clone recorded it."""
        ref = self.git(["symbolic-ref", "--quiet", "HEAD"], accept=(0, 1)).stdout.strip()
        if not ref.startswith("refs/heads/") or not safe_branch_name(ref.removeprefix("refs/heads/")):
            raise BoardUnavailable("The board's repository has no default branch YAAP can use.")
        return check_ref(ref)

    def ensure_clone(self, url: str, token: str | None) -> None:
        """Clone the repository when there is no clone yet. The clone is made under another name and renamed when
        complete, so a failed clone leaves nothing behind."""
        if self.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        staging = self.path.with_name(f".{self.path.name}-{secrets.token_hex(6)}")
        try:
            run_git(
                ["clone", "--bare", "--no-tags", UPLOAD_PACK, url, str(staging)],
                self.path.parent,
                config=CHECK_OBJECTS,
                timeout=CLONE_TIMEOUT,
                private_config=auth_config(url, token),
            )
            RemoteBoard(staging).settle()
            staging.rename(self.path)
        except GitError as error:
            raise PullFailed(f"The board's repository could not be cloned: {error}") from error
        except OSError as error:
            raise PullFailed("The board's repository could not be stored.") from error
        finally:
            remove_transport(staging)

    def settle(self) -> None:
        """After a clone: an empty repository gets YAAP's default branch, and the default branch's remote-tracking
        ref is set, so a later pull and the squash rule know what the remote has."""
        heads = self.git(["for-each-ref", "--count=1", "--format=%(refname)", "refs/heads/"]).stdout.strip()
        if not heads:
            self.git(["symbolic-ref", "HEAD", f"refs/heads/{DEFAULT_BRANCH}"])
            return
        ref = self.default_ref()
        commit = self.rev(ref)
        if commit is not None:
            self.git(["update-ref", tracking_ref(ref), commit])

    def fetch(self, url: str, token: str | None) -> None:
        try:
            self.git(
                ["fetch", UPLOAD_PACK, "--prune", "--no-tags", url, FETCH_REFSPEC],
                authorization=auth_config(url, token),
                config=CHECK_OBJECTS,
                timeout=NETWORK_TIMEOUT,
            )
        except GitError as error:
            raise PullFailed(f"The board's repository could not be fetched: {error}") from error

    def contains(self, ancestor: str, commit: str) -> bool:
        return self.git(["merge-base", "--is-ancestor", ancestor, commit], accept=(0, 1)).exit_code == 0

    def pull(self, url: str, token: str | None) -> str | None:
        """Fetch, then fast-forward the board's branch; returns the board's head afterwards."""
        ref = self.default_ref()
        self.fetch(url, token)
        local, remote = self.rev(ref), self.rev(tracking_ref(ref))
        both = local is not None and remote is not None
        step = pull_step(
            local,
            remote,
            both and self.contains(str(local), str(remote)),
            both and self.contains(str(remote), str(local)),
        )
        if step == "diverged":
            raise BoardDiverged()
        if step == "take" and remote is not None:
            # Compare-and-swap: a board change written meanwhile makes git refuse, and nothing is lost.
            update = self.git(["update-ref", "-m", "pull", ref, remote, local or ""], accept=(0, 1, 128))
            if update.exit_code != 0:
                raise PullFailed("The board changed while it was pulled. Pull again.")
        return self.rev(ref)

    def push(self, url: str, token: str | None, commit: str) -> None:
        """One commit to the board's branch of the repository, never forced."""
        ref = self.default_ref()
        try:
            self.git(
                ["push", RECEIVE_PACK, url, f"{commit}:{ref}"],
                authorization=auth_config(url, token),
                timeout=NETWORK_TIMEOUT,
            )
        except GitError as error:
            if any(word in str(error) for word in REJECTED):
                raise PushFailed(
                    "The board's repository has changes the board here does not have. Pull first, then push."
                ) from error
            raise PushFailed(f"The board could not be pushed: {error}") from error
        self.git(["update-ref", tracking_ref(ref), commit])

    def check_access(self, url: str, token: str | None) -> None:
        """Whether the repository can be read with the token; raises PullFailed with git's reason."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            run_git(
                ["ls-remote", "--heads", UPLOAD_PACK, url],
                self.path.parent,
                timeout=NETWORK_TIMEOUT,
                private_config=auth_config(url, token),
            )
        except GitError as error:
            raise PullFailed(f"The repository could not be reached: {error}") from error


def tracking_ref(ref: str) -> str:
    return TRACKING_PREFIX + ref.removeprefix("refs/heads/")


@dataclass
class BareBoardRepository(BoardRepository):
    """The board on a branch of a bare repository YAAP owns. Git is told the repository (`GIT_DIR`), the scratch
    index and blobs live in that repository, and file contents come from `git archive --output` into the scratch
    folder. A bare repository has no work tree, so its branch is never checked out."""

    def git(
        self,
        arguments: Sequence[str],
        environment: dict[str, str] | None = None,
        accept: Sequence[int] = (0,),
    ) -> GitResult:
        return super().git(arguments, {**(environment or {}), "GIT_DIR": str(self.git_dir)}, accept)

    @property
    def git_dir(self) -> Path:
        return folder_of(self.repository)

    def is_checked_out(self) -> bool:
        return False

    def scratch_folder(self) -> tuple[Path, str]:
        if self.git_dir.is_symlink() or not self.git_dir.is_dir():
            raise BoardUnavailable("The board's repository is not a plain folder, so the board is not written in it.")
        folder = self.git_dir / f"{SCRATCH_PREFIX}{secrets.token_hex(8)}"
        try:
            folder.mkdir()
        except OSError as error:
            raise BoardUnavailable(detail=str(error)) from error
        return folder, str(folder)

    def archive_files(self, commit: str, paths: Sequence[str]) -> dict[str, bytes]:
        scratch, _ = self.scratch_folder()
        try:
            target = scratch / "files.tar"
            # The tree, not the commit: then no attribute can substitute commit details into the files.
            self.git(["archive", "--format=tar", f"--output={target}", f"{commit}^{{tree}}", "--", *paths])
            return regular_members(target.read_bytes())
        except OSError as error:
            raise BoardUnavailable(detail=str(error)) from error
        finally:
            remove_scratch(scratch)
