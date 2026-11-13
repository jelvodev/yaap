"""How YAAP runs git, so that nothing a repository holds can make git run code with YAAP's rights.

A command in the runner (or a project sandbox) can write a project's `.git/config`, `.git/hooks` and
`.gitattributes`, while YAAP's own git (status, diff, commit, fetch, push, branches, checkpoints, the Blueprint) runs
on that folder in YAAP's container. Two layers keep repository-controlled programs out:

- Every git YAAP starts gets `git_variables()`: no system config, an empty global config, no prompts, and fixed
  settings at command scope (`GIT_CONFIG_COUNT`, which outranks every config file) that switch off hooks, the
  fsmonitor, pagers, editors, askpass and credential helpers, a configured ssh command, signing, implicit bare
  repositories and submodule recursion; transports are limited to file, http(s) and ssh. YAAP's own settings
  (the GitHub token for one fetch or push) are passed the same way, never stored.
- Before git runs in a repository, `check_repository()` reads the repository's own config files (also those of its
  submodules) and refuses to run git there when they set a key outside `HONOURED`: filter, diff and merge drivers,
  textconv, includes, upload-pack and receive-pack, gpg programs, `core.worktree` and anything else not listed. A
  `.gitattributes` can only name drivers, which then have no command. The `.git` file of a linked worktree and
  `objects/info/alternates` must stay inside the project folder.

The check and git's own reading of the config are two steps: a process that keeps rewriting `.git/config` could slip a
filter driver in between them. Everything the fixed settings cover holds regardless.
"""

import hashlib
import os
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from app.core.subprocess_env import safe_subprocess_env

ConfigEntry = tuple[str, str]

# Every hook event githooks(5) lists.
HOOK_EVENTS = (
    "applypatch-msg", "pre-applypatch", "post-applypatch", "pre-commit", "pre-merge-commit", "prepare-commit-msg",
    "commit-msg", "post-commit", "pre-rebase", "post-checkout", "post-merge", "pre-push", "pre-receive", "update",
    "proc-receive", "post-receive", "post-update", "reference-transaction", "push-to-checkout", "pre-auto-gc",
    "post-rewrite", "sendemail-validate", "fsmonitor-watchman", "p4-changelist", "p4-prepare-changelist",
    "p4-post-changelist", "p4-pre-submit", "post-index-change",
)  # fmt: skip

# Settings git takes at command scope for every command YAAP runs; nothing in a repository can override them.
FIXED_CONFIG: tuple[ConfigEntry, ...] = (
    ("core.hooksPath", os.devnull),
    ("core.fsmonitor", "false"),
    ("core.pager", "cat"),
    ("core.editor", "false"),
    ("sequence.editor", "false"),
    ("core.sshCommand", "ssh"),
    ("core.askPass", ""),
    # An empty value clears every helper configured before it, also one from the repository.
    ("credential.helper", ""),
    ("safe.bareRepository", "explicit"),
    ("submodule.recurse", "false"),
    ("fetch.recurseSubmodules", "false"),
    ("push.recurseSubmodules", "no"),
    ("diff.ignoreSubmodules", "all"),
    ("status.submoduleSummary", "false"),
    ("commit.gpgSign", "false"),
    ("tag.gpgSign", "false"),
    ("push.gpgSign", "false"),
    ("log.showSignature", "false"),
    ("merge.verifySignatures", "false"),
    ("gpg.format", "openpgp"),
    ("gpg.program", "gpg"),
    ("gpg.openpgp.program", "gpg"),
    # Only consulted with alternates (refused below); a command that prints nothing in case one appears in a race.
    ("core.alternateRefsCommand", "true"),
    # Hooks defined in config (`hook.<name>.command`, newer git) are switched off per event; older git ignores this.
    *((f"hook.{event}.enabled", "false") for event in HOOK_EVENTS),
)

# The global config is YAAP's own: empty, or on Windows the line ending default Git for Windows keeps in the
# system config that YAAP skips. Never the user's ~/.gitconfig or /etc/gitconfig.
GLOBAL_CONFIG = str(Path(__file__).with_name("windows.gitconfig")) if sys.platform == "win32" else os.devnull

# Variables of every git YAAP starts. GIT_ALLOW_PROTOCOL replaces any protocol.* setting, so `ext::` stays closed;
# git:// is left out too, so core.gitProxy has nothing to run for.
FIXED_VARIABLES: dict[str, str] = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": GLOBAL_CONFIG,
    "GIT_TERMINAL_PROMPT": "0",
    # Git Credential Manager ignores GIT_TERMINAL_PROMPT and can open a sign-in window; it must fail too.
    "GCM_INTERACTIVE": "never",
    "GIT_PAGER": "cat",
    "GIT_EDITOR": "false",
    "GIT_SEQUENCE_EDITOR": "false",
    # Set but empty: git then asks no program for a password, not even SSH_ASKPASS.
    "GIT_ASKPASS": "",
    "GIT_SSH_COMMAND": "ssh",
    "GIT_ALLOW_PROTOCOL": "file:http:https:ssh",
}

# Command-line options that outrank remote.<name>.uploadpack and receivepack for fetch, pull, clone and push.
UPLOAD_PACK = "--upload-pack=git-upload-pack"
RECEIVE_PACK = "--receive-pack=git-receive-pack"

# Keys of a repository's config that YAAP honours: section -> names, for keys without a subsection, and
# SUBSECTION_KEYS for keys with one (`remote.<name>.url`). "*" allows every name. Everything else is refused.
HONOURED: dict[str, frozenset[str]] = {
    "core": frozenset(
        {
            "repositoryformatversion", "filemode", "bare", "logallrefupdates", "ignorecase", "precomposeunicode",
            "protecthfs", "protectntfs", "symlinks", "autocrlf", "eol", "safecrlf", "checkroundtripencoding",
            "quotepath", "abbrev", "compression", "loosecompression", "bigfilethreshold", "checkstat", "trustctime",
            "untrackedcache", "splitindex", "preloadindex", "fscache", "longpaths", "hidedotfiles", "whitespace",
            "commentchar", "commentstring", "sparsecheckout", "sparsecheckoutcone", "multipackindex", "commitgraph",
            "deltabasecachelimit", "packedgitlimit", "packedgitwindowsize", "warnambiguousrefs", "fsync",
            "fsyncmethod", "fsyncobjectfiles",
        }
    ),  # fmt: skip
    "extensions": frozenset({"objectformat", "worktreeconfig", "refstorage", "preciousobjects", "noop"}),
    "user": frozenset({"name", "email", "signingkey"}),
    "init": frozenset({"defaultbranch"}),
    "protocol": frozenset({"version"}),
    "diff": frozenset(
        {
            "algorithm", "renames", "renamelimit", "mnemonicprefix", "noprefix", "context", "interhunkcontext",
            "indentheuristic", "colormoved", "colormovedws", "submodule", "ignoresubmodules", "statgraphwidth",
            "wserrorhighlight", "suppressblankempty", "relative", "dirstat", "autorefreshindex",
        }
    ),  # fmt: skip
    "merge": frozenset(
        {
            "ff", "conflictstyle", "log", "renames", "renamelimit", "directoryrenames", "autostash", "stat",
            "verbosity", "defaulttoupstream", "branchdesc", "renormalize",
        }
    ),  # fmt: skip
    **{
        section: frozenset({"*"})
        for section in (
            "advice", "alias", "blame", "branch", "checkout", "color", "column", "commit", "fetch", "feature", "gc",
            "grep", "gui", "help", "i18n", "index", "lfs", "log", "maintenance", "pack", "pull", "push", "rebase",
            "rerere", "status", "tag", "worktree",
        )
    },  # fmt: skip
}
SUBSECTION_KEYS: dict[str, frozenset[str]] = {
    "remote": frozenset(
        {
            "url", "pushurl", "fetch", "push", "prune", "prunetags", "tagopt", "mirror", "skipdefaultupdate",
            "skipfetchall", "followremotehead", "gh-resolved",
        }
    ),  # fmt: skip
    "branch": frozenset({"*"}),
    "submodule": frozenset({"url", "active", "branch", "ignore", "shallow", "fetchrecursesubmodules"}),
    "color": frozenset({"*"}),
    "lfs": frozenset({"*"}),
    "maintenance": frozenset({"*"}),
}
CONFIG_TIMEOUT_SECONDS = 30
SHOWN_KEYS = 5


class UnsafeRepository(Exception):
    """YAAP refuses to run git in a repository; the message is safe to show and says what to change."""


def honoured(key: str) -> bool:
    """Whether YAAP runs git in a repository whose own config sets `key` (as `git config --list` prints it)."""
    section, _, rest = key.partition(".")
    subsection, _, name = rest.rpartition(".")
    section, name = section.lower(), name.lower()
    allowed = SUBSECTION_KEYS.get(section) if subsection else HONOURED.get(section)
    return allowed is not None and ("*" in allowed or name in allowed)


def refused_keys(keys: Iterable[str]) -> list[str]:
    """The keys YAAP does not honour, each once, in the order found."""
    return list(dict.fromkeys(key for key in keys if not honoured(key)))


def git_variables(folder: Path | None = None, config: Sequence[ConfigEntry] = ()) -> dict[str, str]:
    """The git-specific environment for a git run by YAAP (or by a program YAAP starts in a project folder): the
    fixed variables and settings, then `config`, which may carry a credential and therefore never goes on the command
    line. With `folder`, git does not look above it for a repository."""
    variables = dict(FIXED_VARIABLES)
    if folder is not None:
        variables["GIT_CEILING_DIRECTORIES"] = str(folder.resolve().parent)
    entries = [*FIXED_CONFIG, *config]
    variables["GIT_CONFIG_COUNT"] = str(len(entries))
    for index, (key, value) in enumerate(entries):
        variables[f"GIT_CONFIG_KEY_{index}"] = key
        variables[f"GIT_CONFIG_VALUE_{index}"] = value
    return variables


def git_command(arguments: Sequence[str], config: Sequence[str] = ()) -> list[str]:
    """`git --no-pager -c <config>... <arguments>`; `config` holds YAAP's own harmless per-command settings."""
    command = ["git", "--no-pager"]
    for entry in config:
        command += ["-c", entry]
    return command + list(arguments)


# --- Checking a repository -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GitDirs:
    git_dir: Path
    common_dir: Path


def inside(path: Path, folder: Path) -> bool:
    return path == folder or path.is_relative_to(folder)


def bare_layout(has_head: bool, has_objects: bool, has_refs: bool, has_dot_git: bool) -> bool:
    """Whether a folder is a bare repository by git's own test (HEAD, objects/ and refs/), and not a work tree."""
    return has_head and has_objects and has_refs and not has_dot_git


def is_bare_layout(folder: Path) -> bool:
    return bare_layout(
        (folder / "HEAD").is_file(),
        (folder / "objects").is_dir(),
        (folder / "refs").is_dir(),
        (folder / ".git").exists(),
    )


def locate_git_dirs(folder: Path) -> GitDirs | None:
    """The repository git would use in `folder` with YAAP's settings (no discovery above it, no implicit bare
    repository), or None when there is none. Refuses one whose files lie outside the folder. A bare repository at
    the folder itself counts too: git uses one only when YAAP names it (`GIT_DIR`), and then reads its config."""
    root = folder.resolve()
    dot_git = folder / ".git"
    if dot_git.is_file():
        try:
            line = dot_git.read_text(encoding="utf-8", errors="replace").strip()
        except OSError as error:
            raise UnsafeRepository(f"YAAP could not read {dot_git}: {error.strerror}.") from error
        if not line.startswith("gitdir:"):
            return None
        git_dir = (folder / line.removeprefix("gitdir:").strip()).resolve()
    elif dot_git.is_dir():
        git_dir = dot_git.resolve()
    elif is_bare_layout(folder):
        git_dir = root
    else:
        return None
    common_dir = git_dir
    common_file = git_dir / "commondir"
    if common_file.is_file():
        common_dir = (git_dir / common_file.read_text(encoding="utf-8", errors="replace").strip()).resolve()
    for path in (git_dir, common_dir):
        if not inside(path, root):
            raise UnsafeRepository(
                f"YAAP does not run git in {folder.name}: its .git points to {path}, outside the project folder. "
                "Make it an ordinary repository with its own .git folder."
            )
    return GitDirs(git_dir, common_dir)


def config_files(dirs: GitDirs) -> list[Path]:
    """The repository's own config files: the shared one, per-worktree ones, and those of its submodules."""
    candidates = [dirs.common_dir / "config", dirs.common_dir / "config.worktree", dirs.git_dir / "config.worktree"]
    modules = dirs.common_dir / "modules"
    if modules.is_dir():
        candidates += [path for path in sorted(modules.rglob("config*")) if (path.parent / "HEAD").is_file()]
    return [path for path in dict.fromkeys(candidates) if path.name in ("config", "config.worktree") and path.is_file()]


def alternates_outside(dirs: GitDirs, root: Path) -> list[str]:
    """Object stores the repository borrows from outside the project folder (`objects/info/alternates`)."""
    alternates = dirs.common_dir / "objects" / "info" / "alternates"
    if not alternates.is_file():
        return []
    objects = dirs.common_dir / "objects"
    lines = alternates.read_text(encoding="utf-8", errors="replace").splitlines()
    entries = [line.strip() for line in lines if line.strip() and not line.startswith("#")]
    return [entry for entry in entries if not inside((objects / entry).resolve(), root)]


def config_keys(path: Path, folder: Path) -> list[str]:
    """The keys set in one config file, read by git itself (so its syntax is understood exactly), without includes."""
    try:
        completed = subprocess.run(
            git_command(["config", "--file", str(path), "--no-includes", "--null", "--list"]),
            cwd=folder,
            env=safe_subprocess_env(git_variables(folder)),
            capture_output=True,
            timeout=CONFIG_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise UnsafeRepository(f"YAAP could not read {path.name} of {folder.name}: {error}.") from error
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise UnsafeRepository(
            f"YAAP could not read {path.name} of {folder.name}, so it does not run git there: {detail}"
        )
    entries = completed.stdout.decode("utf-8", errors="replace").split("\0")
    return [entry.split("\n", 1)[0] for entry in entries if entry]


def refusal_message(folder: Path, found: list[tuple[str, Path]]) -> str:
    shown = ", ".join(f"{key} (in {path})" for key, path in found[:SHOWN_KEYS])
    more = f" and {len(found) - SHOWN_KEYS} more" if len(found) > SHOWN_KEYS else ""
    example = found[0][0]
    return (
        f"YAAP does not run git in {folder.name}: its git configuration sets {shown}{more}, which git could use to "
        "run a program with YAAP's rights. YAAP honours only plain settings (remotes, branches, user, core options). "
        f"Remove it from the repository, for example with `git config --unset-all {example}`, and try again."
    )


_checked: dict[tuple[Path, tuple[tuple[str, str], ...]], str | None] = {}
CACHE_LIMIT = 512


def file_digests(paths: Iterable[Path]) -> tuple[tuple[str, str], ...]:
    """The content of each file as a hash: a command can reset a file's modification time, never its content."""
    return tuple((str(path), hashlib.sha256(path.read_bytes()).hexdigest()) for path in paths)


def check_repository(folder: Path) -> None:
    """Refuse (UnsafeRepository) when git in `folder` would read settings YAAP does not honour. A folder without a
    repository passes: git, with YAAP's settings, does not look above it. The result is kept per content of the
    files."""
    dirs = locate_git_dirs(folder)
    if dirs is None:
        return
    root = folder.resolve()
    files = config_files(dirs)
    alternates = dirs.common_dir / "objects" / "info" / "alternates"
    key = (root, file_digests([*files, *([alternates] if alternates.is_file() else [])]))
    if key not in _checked:
        if len(_checked) >= CACHE_LIMIT:
            _checked.clear()
        _checked[key] = _problem(folder, dirs, files, root)
    problem = _checked[key]
    if problem is not None:
        raise UnsafeRepository(problem)


def _problem(folder: Path, dirs: GitDirs, files: list[Path], root: Path) -> str | None:
    outside = alternates_outside(dirs, root)
    if outside:
        return (
            f"YAAP does not run git in {folder.name}: it borrows objects from {outside[0]}, outside the project folder "
            "(.git/objects/info/alternates). Run `git repack -a -d` and remove that file, then try again."
        )
    found = [
        (refused, path.relative_to(root) if inside(path, root) else path)
        for path in files
        for refused in refused_keys(config_keys(path, folder))
    ]
    return refusal_message(folder, found) if found else None
