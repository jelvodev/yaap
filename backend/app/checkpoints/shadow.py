"""A shadow git repository per project that holds YAAP's checkpoints.

The repository lives in YAAP's data folder, `checkpoints/<project id>.git`, with the project folder as its work tree,
so the project's own history is never touched. Git skips the project's own `.git` folder and follows its
`.gitignore`; rebuildable folders and files over MAX_FILE_BYTES are left out as well. Each checkpoint is a commit
without parent under `refs/checkpoints/<id>`, so dropping old checkpoints frees their objects.
"""

import stat
import subprocess
from pathlib import Path, PurePosixPath

from app.core.subprocess_env import safe_subprocess_env
from app.git.safety import git_command, git_variables
from app.targets.safe_root import RootReplacedError, SafeRoot, SafeRootError

MAX_FILE_BYTES = 5 * 1024 * 1024
DEFAULT_EXCLUDES = (
    "node_modules/",
    ".venv/",
    "venv/",
    "__pycache__/",
    ".mypy_cache/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".next/",
    ".cache/",
)
IDENTITY = {
    "GIT_AUTHOR_NAME": "YAAP",
    "GIT_AUTHOR_EMAIL": "yaap@localhost",
    "GIT_COMMITTER_NAME": "YAAP",
    "GIT_COMMITTER_EMAIL": "yaap@localhost",
}


class CheckpointError(Exception):
    """A checkpoint could not be taken or restored; the message is safe to show."""


class ShadowRepository:
    def __init__(self, git_dir: Path, work_tree: Path) -> None:
        self.git_dir = git_dir
        self.work_tree = work_tree

    def environment(self) -> dict[str, str]:
        # The repository is YAAP's own, but the work tree is the project's: git's safe settings keep programs that
        # the project's files could name (through .gitattributes) from running.
        environment = safe_subprocess_env(
            {
                **git_variables(),
                **IDENTITY,
                "GIT_DIR": str(self.git_dir),
                "GIT_WORK_TREE": str(self.work_tree),
            }
        )
        environment.pop("GIT_INDEX_FILE", None)
        return environment

    def archive(self, tree: str) -> bytes:
        """A checkpoint's files as a tar archive, read from the shadow repository; the project folder is not
        touched (an eval case's workspace is made from it)."""
        if not (self.git_dir / "HEAD").is_file():
            raise CheckpointError("The project has no checkpoints.")
        result = subprocess.run(
            git_command(("archive", "--format=tar", tree), ("core.autocrlf=false",)),
            cwd=self.git_dir,
            env=self.environment(),
            capture_output=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise CheckpointError(f"git archive failed: {result.stderr.decode('utf-8', errors='replace')[:500]}")
        return result.stdout

    def git(self, *args: str, check: bool = True, input_text: str | None = None) -> str:
        result = subprocess.run(
            git_command(args, ("core.autocrlf=false", "core.quotepath=false")),
            cwd=self.work_tree,
            env=self.environment(),
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        if check and result.returncode != 0:
            raise CheckpointError(f"git {args[0]} failed: {(result.stderr or result.stdout).strip()[:500]}")
        return result.stdout

    def ensure(self) -> None:
        if (self.git_dir / "HEAD").is_file():
            return
        self.git_dir.mkdir(parents=True, exist_ok=True)
        self.git("init", "--quiet")
        exclude = self.git_dir / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text("\n".join(DEFAULT_EXCLUDES) + "\n", encoding="utf-8")

    def snapshot_tree(self) -> str:
        """Stage the work tree as it is now and return its tree id."""
        if not self.work_tree.is_dir():
            raise CheckpointError("The project folder does not exist.")
        self.ensure()
        self.git("add", "--all", "--", ".")
        staged = self.git("diff", "--cached", "--name-only", "-z", "--diff-filter=AM").split("\0")
        too_large = self.larger_than_limit([path for path in staged if path])
        if too_large:
            self.git("rm", "--cached", "--quiet", "--pathspec-from-file=-", "--pathspec-file-nul", input_text="\0".join(too_large))
            with (self.git_dir / "info" / "exclude").open("a", encoding="utf-8") as exclude:
                exclude.writelines(f"/{path}\n" for path in too_large)
        return self.git("write-tree").strip()

    def larger_than_limit(self, paths: list[str]) -> list[str]:
        """The regular files among paths over MAX_FILE_BYTES, measured below the pinned work tree (a link is not
        followed to a file elsewhere)."""
        found: list[str] = []
        try:
            with SafeRoot(self.work_tree) as root:
                for path in paths:
                    try:
                        info = root.stat(path)
                    except RootReplacedError:
                        raise
                    except SafeRootError:
                        continue
                    if info is not None and stat.S_ISREG(info.st_mode) and info.st_size > MAX_FILE_BYTES:
                        found.append(path)
        except OSError as error:
            raise CheckpointError("The project folder could not be read.") from error
        return found

    def commit(self, tree: str, message: str) -> str:
        return self.git("commit-tree", tree, "-m", message).strip()

    def keep(self, checkpoint_id: int, commit: str) -> None:
        self.git("update-ref", f"refs/checkpoints/{checkpoint_id}", commit)

    def drop(self, checkpoint_ids: list[int]) -> None:
        if not checkpoint_ids or not (self.git_dir / "HEAD").is_file():
            return
        commands = "".join(f"delete refs/checkpoints/{checkpoint_id}\n" for checkpoint_id in checkpoint_ids)
        self.git("update-ref", "--stdin", input_text=commands, check=False)
        self.git("gc", "--auto", "--quiet", check=False)

    def diff(self, old: str, new: str) -> str:
        return self.git("diff", "--no-color", "--no-ext-diff", "--no-textconv", old, new)

    def changed_paths(self, old: str, new: str) -> list[str]:
        return [path for path in self.git("diff", "--name-only", "-z", old, new).split("\0") if path]

    def restore(self, target_tree: str, current_tree: str) -> None:
        """Make the work tree match target_tree: write its files, and delete files added since."""
        added = [
            path
            for path in self.git("diff", "--name-only", "-z", "--diff-filter=A", target_tree, current_tree).split("\0")
            if path
        ]
        self.git("read-tree", target_tree)
        self.git("checkout-index", "--all", "--force")
        try:
            with SafeRoot(self.work_tree) as root:
                for path in added:
                    remove_added(root, path)
        except RootReplacedError as error:
            raise CheckpointError("The project folder was replaced during the restore.") from error
        except OSError as error:
            raise CheckpointError(f"The restore could not remove added files: {error}") from error


def remove_added(root: SafeRoot, path: str) -> None:
    """Remove a file added since the checkpoint and the folders it leaves empty, all below the pinned work tree. A
    path that leads out of the project through a link is left alone."""
    try:
        info = root.stat(path)
        if info is not None and not stat.S_ISDIR(info.st_mode):
            root.remove(path)
        parent = PurePosixPath(path).parent.as_posix()
        if parent != ".":
            root.remove_empty_dirs(parent)
    except RootReplacedError:
        raise
    except SafeRootError:
        return
