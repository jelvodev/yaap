"""Which files the repository map looks at, and the key its cache uses.

The files are listed through the run's own file access and command executor, so the map works in the container, in
the runner, in a sandbox and on a computer over SSH alike. In a git repository `git ls-files` lists the tracked and
untracked files that .gitignore does not exclude, and the key is the HEAD commit plus whether the working tree has
changes; elsewhere the folder is walked and the key is a hash of the file list.
"""

import hashlib
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.agent.file_tools import SKIPPED_FOLDERS as TOOL_SKIPPED_FOLDERS
from app.core.subprocess_env import safe_subprocess_env
from app.targets.base import CommandExecutor, TargetFiles

# Vendored, generated and tool folders the map leaves out, on top of what the file tools skip. Every folder whose
# name starts with a dot is skipped as well (.git, .blueprint with its eval cases, .evals workspaces, caches).
SKIPPED_FOLDERS = frozenset(
    {
        *TOOL_SKIPPED_FOLDERS,
        "venv",
        "vendor",
        "third_party",
        "site-packages",
        "coverage",
        "htmlcov",
        "target",
        "bower_components",
    }
)
# Names the walk prunes right away; the dot rule is applied to the walked paths afterwards.
WALK_SKIP = {*SKIPPED_FOLDERS, ".git", ".blueprint", ".evals", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox"}
MAX_FILES = 20_000
MAX_WALK_ENTRIES = 5_000
COMMAND_SECONDS = 5.0
GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "PAGER": "cat", "GIT_PAGER": "cat"}
GIT_HEAD = "git rev-parse --show-prefix --verify --quiet HEAD"
GIT_STATUS = "git --no-optional-locks status --porcelain"
GIT_FILES = "git -c core.quotepath=off ls-files --cached --others --exclude-standard"


@dataclass(frozen=True)
class Revision:
    """What the map of a folder depends on: its cache key and a short description for the map's header."""

    key: str
    label: str
    # The commit for a git repository; None for a folder that is walked.
    commit: str | None = None
    # The walked file list of a folder outside git, so it is not walked twice.
    files: tuple[str, ...] | None = None
    # A clean checkout of a commit has the same files wherever it is, so its map is shared between folders (an eval
    # case's workspace and the project itself).
    shared: bool = False


def is_skipped(path: str) -> bool:
    """Whether a file lies in a vendored, generated or hidden folder."""
    folders = PurePosixPath(path).parts[:-1]
    return any(name.startswith(".") or name in SKIPPED_FOLDERS or name.endswith(".egg-info") for name in folders)


def kept(paths: list[str]) -> tuple[str, ...]:
    return tuple(sorted({path for path in paths if path and not is_skipped(path)})[:MAX_FILES])


def file_list_key(paths: tuple[str, ...]) -> str:
    return hashlib.sha256("\n".join(paths).encode("utf-8")).hexdigest()


async def git_output(executor: CommandExecutor, command: str) -> str | None:
    """The command's output, or None when it fails (not a git repository, no commit yet, git missing)."""
    result = await executor.run(command, {**safe_subprocess_env(), **GIT_ENV}, timeout=COMMAND_SECONDS)
    if result.timed_out or result.exit_code != 0:
        return None
    return result.output


def head_commit(output: str | None) -> str | None:
    """The commit from `git rev-parse --show-prefix HEAD`, when the folder is the top of its own repository with at
    least one commit. A folder inside another repository (a prefix) is treated as no repository: its map follows its
    own files, not the commits of the folder around it."""
    lines = (output or "").splitlines()
    if len(lines) < 2 or lines[0].strip():
        return None
    commit = lines[1].strip()
    is_hash = len(commit) in (40, 64) and all(character in "0123456789abcdef" for character in commit)
    return commit if is_hash else None


async def revision(files: TargetFiles, executor: CommandExecutor) -> Revision:
    """The folder's revision: its git commit and dirty flag, or else the hash of its walked file list."""
    commit = head_commit(await git_output(executor, GIT_HEAD))
    if commit is not None:
        status = await git_output(executor, GIT_STATUS)
        dirty = bool(status and status.strip())
        label = f"commit {commit[:7]}" + (" with uncommitted changes" if dirty else "")
        return Revision(f"git:{commit}:{'dirty' if dirty else 'clean'}", label, commit, shared=not dirty)
    walked = await files.walk(".", True, set(WALK_SKIP), MAX_WALK_ENTRIES)
    paths = kept([entry.path for entry in walked if not entry.is_dir])
    return Revision(f"files:{file_list_key(paths)}", f"{len(paths)} files, not a git repository", None, paths)


async def project_files(executor: CommandExecutor, found: Revision) -> tuple[str, ...]:
    """The files of the map: from `git ls-files` for a commit, else the list the revision walked."""
    if found.files is not None:
        return found.files
    output = await git_output(executor, GIT_FILES)
    if output is None:
        return ()
    # A path git still quotes (a newline or quote in its name) is left out rather than guessed.
    return kept([line for line in output.splitlines() if not line.startswith('"')])
