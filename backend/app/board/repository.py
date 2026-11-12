"""Reading and writing the board's files on a git ref without a checkout.

The writer follows `app.learning.branches.write_branch`: blobs with `hash-object -w --no-filters`, a scratch index
(`GIT_INDEX_FILE`) in the repository's `.git`, `write-tree`, `commit-tree` and `update-ref`. The work tree, the real
index and the checked-out branch are never touched. The first commit has no parent (an orphan branch), and every
update of the ref is a compare-and-swap against the head the change was based on. Board commits carry the fixed
identity `YAAP board <board@yaap.invalid>`, never the user's.

The repository is a `GitPlace | Path` as everywhere else, so `run_git` decides whether git runs in YAAP or in the
runner. A process in the runner can write this ref too, so what is read here is data: only regular files on the
allow list (`app.board.paths`) are read, larger blobs are refused before their content is read."""

import io
import secrets
import shutil
import tarfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from app.board.errors import BoardCheckedOut, BoardUnavailable, HeadMoved, InvalidCard, PathRefused
from app.board.paths import DEFAULT_REF, MAX_FILE_BYTES, check_commit, check_path, check_ref, classify
from app.board.squash import BOARD_AUTHOR, BOARD_AUTHOR_EMAIL, BOARD_AUTHOR_NAME
from app.git.service import GitError, GitResult, Repository, archive, folder_of, run_git

BOARD_IDENTITY = {
    "GIT_AUTHOR_NAME": BOARD_AUTHOR_NAME,
    "GIT_AUTHOR_EMAIL": BOARD_AUTHOR_EMAIL,
    "GIT_COMMITTER_NAME": BOARD_AUTHOR_NAME,
    "GIT_COMMITTER_EMAIL": BOARD_AUTHOR_EMAIL,
}
REGULAR_FILE = "100644"
# Paths per `git archive` or `update-index` call, so a command line stays well below the platforms' limits.
PATHS_PER_CALL = 200
SCRATCH_PREFIX = "yaap-board-"

ReadError = Literal["too_large", "not_text", "unreadable"]


@dataclass(frozen=True)
class Put:
    """Write content (text with `\\n` line endings) at a board path."""

    path: str
    content: str


@dataclass(frozen=True)
class Delete:
    path: str


Change = Put | Delete


@dataclass(frozen=True)
class TreeFile:
    """A regular board file in a commit: its path, blob and size in bytes."""

    path: str
    blob: str
    size: int


@dataclass(frozen=True)
class FileContent:
    """A board file's text, or why it was not read (`text` is then None)."""

    path: str
    blob: str
    text: str | None
    error: ReadError | None = None


@dataclass(frozen=True)
class CommitInfo:
    commit: str
    parents: tuple[str, ...]
    author_name: str
    author_email: str
    authored_at: datetime
    message: str

    @property
    def author(self) -> str:
        return f"{self.author_name} <{self.author_email}>"


@dataclass(frozen=True)
class Written:
    """The board's new head, and whether it replaced the old head instead of following it."""

    commit: str
    squashed: bool


@dataclass
class BoardRepository:
    """The board on `ref` in a repository. Methods raise BoardError subclasses only."""

    repository: Repository
    ref: str = DEFAULT_REF
    _ref_checked: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        check_ref(self.ref)

    # --- git ---------------------------------------------------------------------------------------------------

    def git(
        self,
        arguments: Sequence[str],
        environment: dict[str, str] | None = None,
        accept: Sequence[int] = (0,),
    ) -> GitResult:
        try:
            return run_git(arguments, self.repository, environment, accept_exit_codes=accept)
        except GitError as error:
            raise BoardUnavailable(detail=str(error)) from error

    def checked_ref(self) -> str:
        """The ref, after git itself agreed it is a valid ref name (once per repository object)."""
        if not self._ref_checked:
            if self.git(["check-ref-format", self.ref], accept=(0, 1)).exit_code != 0:
                raise PathRefused("The board's branch must be a branch under refs/heads/.")
            self._ref_checked = True
        return self.ref

    # --- reading -----------------------------------------------------------------------------------------------

    def head(self) -> str | None:
        """The commit the board's branch is at; None before the first board commit."""
        found = self.git(["rev-parse", "--verify", "--quiet", f"{self.checked_ref()}^{{commit}}"], accept=(0, 1))
        return found.stdout.strip() or None

    def has_commit(self, commit: str) -> bool:
        found = self.git(["cat-file", "-e", f"{check_commit(commit)}^{{commit}}"], accept=(0, 1, 128))
        return found.exit_code == 0

    def resolve(self, commit: str | None) -> str | None:
        return self.head() if commit is None else check_commit(commit)

    def list_files(self, commit: str | None = None) -> tuple[TreeFile, ...]:
        """The board files of a commit (the head by default): regular files on the allow list only, so links,
        executables and other files are ignored."""
        at = self.resolve(commit)
        if at is None:
            return ()
        listing = self.git(["ls-tree", "-r", "-l", "-z", "--full-tree", at]).stdout
        return tuple(parse_tree_listing(listing))

    def read(self, path: str, commit: str | None = None) -> FileContent | None:
        """One board file; None when the commit has no regular file there."""
        return self.read_many([path], commit).get(path)

    def read_many(self, paths: Sequence[str], commit: str | None = None) -> dict[str, FileContent]:
        """Board files of a commit (the head by default) by path; paths without a regular file are left out. A blob
        over the size cap is reported as too large without reading it. The contents come in one `git archive` of
        the commit's tree per batch of paths, as raw bytes."""
        wanted = {check_path(path).path for path in paths}
        at = self.resolve(commit)
        if at is None or not wanted:
            return {}
        files = {entry.path: entry for entry in self.list_files(at) if entry.path in wanted}
        found = {
            path: FileContent(path, entry.blob, None, "too_large")
            for path, entry in files.items()
            if entry.size > MAX_FILE_BYTES
        }
        readable = sorted(path for path in files if path not in found)
        for start in range(0, len(readable), PATHS_PER_CALL):
            batch = readable[start : start + PATHS_PER_CALL]
            contents = self.archive_files(at, batch)
            for path in batch:
                found[path] = file_content(files[path], contents.get(path))
        return found

    def archive_files(self, commit: str, paths: Sequence[str]) -> dict[str, bytes]:
        # The tree, not the commit: then no attribute can substitute commit details into the files.
        try:
            data = archive(self.repository, f"{commit}^{{tree}}", paths)
        except GitError as error:
            raise BoardUnavailable(detail=str(error)) from error
        return regular_members(data)

    def changed_paths(self, old: str | None, new: str) -> tuple[str, ...]:
        """The board paths that differ between two commits (all board files of `new` when `old` is None), with
        added, changed, deleted and changed-mode files alike."""
        check_commit(new)
        if old is None:
            return tuple(entry.path for entry in self.list_files(new))
        output = self.git(["diff-tree", "-r", "--no-renames", "--name-only", "-z", check_commit(old), new]).stdout
        return tuple(path for path in output.split("\0") if path and classify(path) is not None)

    def commit_info(self, commit: str) -> CommitInfo:
        layout = "%H%x00%P%x00%an%x00%ae%x00%at%x00%B"
        output = self.git(["show", "-s", "--no-show-signature", f"--format={layout}", check_commit(commit)]).stdout
        parts = output.split("\0", 5)
        if len(parts) != 6:
            raise BoardUnavailable(detail="unexpected commit format")
        sha, parents, name, email, seconds, message = parts
        return CommitInfo(
            sha.strip(),
            tuple(parents.split()),
            name,
            email,
            datetime.fromtimestamp(int(seconds), UTC),
            message.strip(),
        )

    def on_any_remote(self, commit: str) -> bool:
        """Whether any remote-tracking ref contains the commit."""
        output = self.git(
            ["for-each-ref", "--contains", check_commit(commit), "--format=%(refname)", "refs/remotes/"]
        ).stdout
        return output.strip() != ""

    def is_checked_out(self) -> bool:
        """Whether the board's branch is the checked-out branch of the repository or one of its worktrees."""
        output = self.git(["for-each-ref", "--format=%(worktreepath)", self.checked_ref()]).stdout
        return output.strip() != ""

    # --- writing -----------------------------------------------------------------------------------------------

    def commit_changes(
        self,
        changes: Sequence[Change],
        message: str,
        expected_head: str | None,
        squash: bool = False,
    ) -> Written:
        """Commit changes onto the board's branch, based on `expected_head` (None: the branch must not exist yet).
        Raises HeadMoved when the branch is elsewhere. With `squash`, the new commit replaces the head instead of
        following it, but only when the head is the board's own and no remote-tracking ref has it; otherwise it
        follows the head as usual. Nothing outside the object store and the ref changes."""
        check_changes(changes)
        if not message.strip():
            raise InvalidCard("A board commit needs a message.")
        expected = None if expected_head is None else check_commit(expected_head)
        ref = self.checked_ref()
        if self.is_checked_out():
            raise BoardCheckedOut()
        current = self.head()
        if current != expected:
            raise HeadMoved(current)
        base, parent, squashed = current, current, False
        if squash and current is not None:
            info = self.commit_info(current)
            if info.author == BOARD_AUTHOR and len(info.parents) <= 1 and not self.on_any_remote(current):
                parent, squashed = (info.parents[0] if info.parents else None), True
        tree = self.write_tree(base, changes)
        if not squashed and current is not None and tree == self.tree_of(current):
            return Written(current, False)
        parents = ["-p", parent] if parent is not None else []
        commit = self.git(
            ["commit-tree", "--no-gpg-sign", tree, *parents, "-m", message.strip()], BOARD_IDENTITY
        ).stdout.strip()
        # Compare-and-swap: the empty old value makes git refuse when the branch exists already.
        update = self.git(["update-ref", "-m", "board", ref, commit, current or ""], accept=(0, 1, 128))
        if update.exit_code != 0:
            now = self.head()
            if now != current:
                raise HeadMoved(now)
            raise BoardUnavailable(detail=(update.stderr or update.stdout).strip())
        return Written(commit, squashed)

    def tree_of(self, commit: str) -> str:
        return self.git(["rev-parse", "--verify", f"{check_commit(commit)}^{{tree}}"]).stdout.strip()

    def write_tree(self, base: str | None, changes: Sequence[Change]) -> str:
        """The tree of `base` with changes applied, built in a scratch index; the real index is never used."""
        scratch, scratch_name = self.scratch_folder()
        try:
            index = {"GIT_INDEX_FILE": f"{scratch_name}/index"}
            if base is not None:
                self.git(["read-tree", base], index)
            puts = [change for change in changes if isinstance(change, Put)]
            blobs = self.write_blobs(scratch, scratch_name, puts)
            entries = [f"{REGULAR_FILE},{blob},{put.path}" for put, blob in zip(puts, blobs, strict=True)]
            for start in range(0, len(entries), PATHS_PER_CALL):
                batch = entries[start : start + PATHS_PER_CALL]
                cacheinfo = [argument for entry in batch for argument in ("--cacheinfo", entry)]
                self.git(["update-index", "--add", "--replace", *cacheinfo], index)
            deletes = [change.path for change in changes if isinstance(change, Delete)]
            for start in range(0, len(deletes), PATHS_PER_CALL):
                self.git(["update-index", "--force-remove", "--", *deletes[start : start + PATHS_PER_CALL]], index)
            return self.git(["write-tree"], index).stdout.strip()
        finally:
            remove_scratch(scratch)

    def write_blobs(self, scratch: Path, scratch_name: str, puts: Sequence[Put]) -> list[str]:
        if not puts:
            return []
        names: list[str] = []
        for number, put in enumerate(puts):
            # Exclusive creation does not follow a link left at the path.
            with (scratch / f"blob-{number}").open("xb") as handle:
                handle.write(put.content.replace("\r\n", "\n").encode("utf-8"))
            names.append(f"{scratch_name}/blob-{number}")
        blobs: list[str] = []
        for start in range(0, len(names), PATHS_PER_CALL):
            output = self.git(["hash-object", "-w", "--no-filters", "--", *names[start : start + PATHS_PER_CALL]])
            blobs.extend(output.stdout.split())
        if len(blobs) != len(puts):
            raise BoardUnavailable(detail="hash-object returned an unexpected number of blobs")
        return blobs

    def scratch_folder(self) -> tuple[Path, str]:
        """A new folder in the repository's `.git` for the scratch index and blobs: YAAP's path and the path git
        sees relative to the repository. A `.git` that is not a plain folder could lead the writes elsewhere, so
        it is refused (as `write_branch` does)."""
        git_folder = folder_of(self.repository) / ".git"
        if git_folder.is_symlink() or not git_folder.is_dir():
            raise BoardUnavailable("The project's .git is not a plain folder, so the board is not written in it.")
        name = f"{SCRATCH_PREFIX}{secrets.token_hex(8)}"
        try:
            (git_folder / name).mkdir()
        except OSError as error:
            raise BoardUnavailable(detail=str(error)) from error
        return git_folder / name, f".git/{name}"


def check_changes(changes: Sequence[Change]) -> None:
    """Every change on the allow list, each path once, each content within the size cap."""
    seen: set[str] = set()
    for change in changes:
        check_path(change.path)
        if change.path in seen:
            raise PathRefused("A board file is changed twice in one commit.")
        seen.add(change.path)
        if isinstance(change, Put) and len(change.content.encode("utf-8")) > MAX_FILE_BYTES:
            raise InvalidCard(f"A board file is larger than {MAX_FILE_BYTES} bytes.")
    if not changes:
        raise InvalidCard("A board commit needs at least one change.")


def parse_tree_listing(listing: str) -> list[TreeFile]:
    """The regular board files of `ls-tree -r -l -z` output; everything else (links, executables, submodules,
    other paths) is left out."""
    files: list[TreeFile] = []
    for record in listing.split("\0"):
        meta, tab, path = record.partition("\t")
        if not tab:
            continue
        fields = meta.split()
        if len(fields) != 4 or fields[0] != REGULAR_FILE or fields[1] != "blob" or not fields[3].isdigit():
            continue
        if classify(path) is not None:
            files.append(TreeFile(path, fields[2], int(fields[3])))
    return files


def regular_members(data: bytes) -> dict[str, bytes]:
    """The regular files of a tar archive by name; links and other entries are left out."""
    found: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as bundle:
        for member in bundle.getmembers():
            if not member.isreg() or member.size > MAX_FILE_BYTES:
                continue
            extracted = bundle.extractfile(member)
            if extracted is not None:
                found[member.name] = extracted.read()
    return found


def file_content(entry: TreeFile, data: bytes | None) -> FileContent:
    if data is None:
        return FileContent(entry.path, entry.blob, None, "unreadable")
    try:
        return FileContent(entry.path, entry.blob, data.decode("utf-8"))
    except UnicodeDecodeError:
        return FileContent(entry.path, entry.blob, None, "not_text")


def remove_scratch(scratch: Path) -> None:
    # rmtree does not follow links; a folder that stays behind holds only objects git has already stored.
    shutil.rmtree(scratch, ignore_errors=True)
