"""The baseline eval project: building it as a git repository.

The fixture lives in the repository under `evals/baseline/`: `project/` is the start state of the Stockroom project
(Python and TypeScript, with its eval cases in `project/.blueprint/evals/`), `history/` holds patches that become
the commits after the start, `solutions/` a reference solution per case, and `mcp/` the issue tracker MCP server one
case needs. Eval cases name their start state by commit hash, so the build is deterministic: fixed author, committer
and dates, LF line endings, no executable bits, and git run with YAAP's own settings (no user configuration). The
same files always give the same hashes, which the case files name and a test checks.

Commits: the start (the project without its eval cases), one commit per history patch, then the eval cases.

`evals/savings/` is a second set in the same layout on *Freight*, a small Python project with a long pytest suite
and a long module, for cases where context savings can act (long test output, repeated reads). Its runner needs
pytest.
"""

from dataclasses import dataclass
from pathlib import Path

from app.git.service import GitError, Repository, folder_of, run_git

EVALS_DIR = Path(__file__).resolve().parents[3] / "evals"
BASELINE_DIR = EVALS_DIR / "baseline"
SAVINGS_DIR = EVALS_DIR / "savings"
CASES_FOLDER = Path(".blueprint") / "evals"
SKIPPED_PARTS = frozenset({"__pycache__", "node_modules", ".git"})
SKIPPED_SUFFIXES = (".pyc", ".tmp")
AUTHOR = ("Stockroom Team", "team@stockroom.example")
FIRST_COMMIT_TIME = 1_793_613_600  # 2026-11-02 10:00 UTC; each later commit is an hour later.
GIT_CONFIG = ("core.autocrlf=false", "core.eol=lf", "core.filemode=false", "commit.gpgsign=false")
START_MESSAGE = "Stockroom 0.4.0"
# The first commit's message per eval set, by the set's folder name.
START_MESSAGES = {"baseline": START_MESSAGE, "savings": "Freight 1.0.0"}
CASES_MESSAGE = "Add the baseline eval cases"
PATCH_FILE = ".yaap-baseline.patch"


class BaselineError(Exception):
    """The baseline project cannot be built; the message says why."""


@dataclass(frozen=True)
class BaselineCommits:
    """The commits of a built baseline project: the start, one per history patch, and the eval cases."""

    start: str
    history: tuple[str, ...]
    cases: str


def project_files(source: Path, include_cases: bool) -> list[Path]:
    """The fixture's files relative to `source`, sorted, without caches; the eval cases only when asked."""
    found = []
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if not path.is_file() or SKIPPED_PARTS & set(relative.parts) or path.name.endswith(SKIPPED_SUFFIXES):
            continue
        if relative.parent == CASES_FOLDER and not include_cases:
            continue
        found.append(relative)
    return found


def lf_bytes(path: Path) -> bytes:
    """A fixture file with LF line endings, whatever the checkout of YAAP's repository did to it."""
    return path.read_bytes().replace(b"\r\n", b"\n")


def copy_files(source: Path, target: Path, files: list[Path]) -> None:
    for relative in files:
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(lf_bytes(source / relative))


def check_target(target: Repository) -> None:
    """The target is new, empty, or an empty repository such as YAAP makes for a new project."""
    folder = folder_of(target)
    if not folder.exists():
        return
    if not folder.is_dir():
        raise BaselineError(f"{folder} is not a folder.")
    entries = [entry.name for entry in folder.iterdir()]
    if any(name != ".git" for name in entries):
        raise BaselineError(f"{folder} is not empty; use an empty folder or a new, empty YAAP project.")
    if entries and run_git(["rev-parse", "--verify", "--quiet", "HEAD"], target, accept_exit_codes=(0, 1)).stdout:
        raise BaselineError(f"{folder} already has commits; use a new, empty YAAP project.")


def commit_all(target: Repository, message: str, index: int, allow_empty: bool = False) -> str:
    """Stage everything and commit it at the index-th fixed time; returns the commit hash."""
    moment = f"@{FIRST_COMMIT_TIME + index * 3600} +0000"
    identity = {
        "GIT_AUTHOR_NAME": AUTHOR[0],
        "GIT_AUTHOR_EMAIL": AUTHOR[1],
        "GIT_COMMITTER_NAME": AUTHOR[0],
        "GIT_COMMITTER_EMAIL": AUTHOR[1],
        "GIT_AUTHOR_DATE": moment,
        "GIT_COMMITTER_DATE": moment,
    }
    run_git(["add", "--all", "--chmod=-x", "."], target, config=GIT_CONFIG)
    empty = ["--allow-empty"] if allow_empty else []
    run_git(["commit", "--quiet", "--no-verify", *empty, "-m", message], target, identity, config=GIT_CONFIG)
    return run_git(["rev-parse", "HEAD"], target).stdout.strip()


def apply_patch(target: Repository, patch: Path) -> None:
    """Apply a patch from the fixture (LF line endings) to the target's working tree. The patch is written into the
    target's own folder, because git may run in the runner, which sees that folder and not YAAP's temporary files;
    it is removed again before anything is staged."""
    scratch = folder_of(target) / PATCH_FILE
    try:
        scratch.write_bytes(lf_bytes(patch))
        run_git(["apply", "--whitespace=nowarn", PATCH_FILE], target, config=GIT_CONFIG)
    except GitError as error:
        raise BaselineError(f"{patch.name} does not apply: {error}") from error
    finally:
        scratch.unlink(missing_ok=True)


def patch_message(patch: Path) -> str:
    """A history patch starts with its commit message on the first line."""
    first = lf_bytes(patch).decode("utf-8").split("\n", 1)[0].strip()
    if not first or first.startswith("diff --git"):
        raise BaselineError(f"{patch.name} must start with its commit message.")
    return first


def build_project(target: Repository, source: Path = BASELINE_DIR) -> BaselineCommits:
    """Build the baseline project as a git repository in `target` (new, empty, or a new YAAP project's folder).
    A project folder in the workspace is given as its `GitPlace`, so git runs where the folder's git belongs; call it
    from a worker thread then."""
    project = source / "project"
    if not project.is_dir():
        raise BaselineError(f"The baseline fixture is missing: {project}.")
    check_target(target)
    folder = folder_of(target)
    folder.mkdir(parents=True, exist_ok=True)
    try:
        run_git(["init", "--quiet", "-b", "main"], target, config=GIT_CONFIG)
        for entry in GIT_CONFIG:
            key, value = entry.split("=", 1)
            run_git(["config", key, value], target)
        copy_files(project, folder, project_files(project, include_cases=False))
        start = commit_all(target, START_MESSAGES.get(source.name, START_MESSAGE), 0)
        history = []
        for index, patch in enumerate(sorted((source / "history").glob("*.patch")), start=1):
            apply_patch(target, patch)
            history.append(commit_all(target, patch_message(patch), index))
        cases = [path for path in project_files(project, include_cases=True) if path.parent == CASES_FOLDER]
        copy_files(project, folder, cases)
        last = commit_all(target, CASES_MESSAGE, len(history) + 1, allow_empty=True)
    except GitError as error:
        raise BaselineError(f"git failed while building the baseline project: {error}") from error
    return BaselineCommits(start, tuple(history), last)


def copy_mcp_server(target: Path, source: Path = BASELINE_DIR) -> list[Path]:
    """Copy the issue tracker MCP server (the server and its data) to `target`; returns the copied files."""
    target.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in ("tracker_server.py", "issues.json"):
        destination = target / name
        destination.write_bytes(lf_bytes(source / "mcp" / name))
        copied.append(destination)
    return copied
