"""The GitHub token is only ever used in the transport repository, and commits travel as bundles.

The remote is a local bare repository: the tests let the https-only checks accept it (as test_git.py does) and give
every network step a stand-in token, so a process that carries it can be told apart from one that does not.
"""

import asyncio
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Literal, get_args

import anyio
import pytest

from app.git import bundles, service
from app.git import remote as git_remote
from app.git.bundles import RefUpdate, check_push_bundle, safe_branch_name, tracking_updates
from app.git.common import GitError
from app.git.place import GitPlace
from app.git.transport import STEP_PLACES, TransportStep, carries_token, network_step, transport_path
from app.runner.service import RUNNERS
from app.targets.base import CapturedResult

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40
TOKEN_VALUE = "AUTHORIZATION: basic c2VjcmV0"
TOKEN_CONFIG = (("http.extraheader", TOKEN_VALUE),)
TRACKING = "refs/remotes/origin/"


# --- Decisions (pure) ----------------------------------------------------------------------------------------------


def test_every_step_has_a_place() -> None:
    assert set(STEP_PLACES) == set(get_args(TransportStep))


@pytest.mark.parametrize(
    ("step", "place", "token"),
    [
        ("remote_fetch", "transport", True),
        ("remote_push", "transport", True),
        ("remote_clone", "transport", True),
        ("transport_init", "transport", False),
        ("transport_read", "transport", False),
        ("transport_bundle", "transport", False),
        ("transport_unbundle", "transport", False),
        ("transport_update_ref", "transport", False),
        ("work_init", "work", False),
        ("work_read", "work", False),
        ("work_bundle", "work", False),
        ("work_unbundle", "work", False),
        ("work_update_tracking", "work", False),
        ("work_set_upstream", "work", False),
        ("work_merge", "work", False),
        ("work_checkout", "work", False),
    ],
)
def test_a_step_runs_where_the_table_says_and_only_remote_steps_carry_the_token(
    step: TransportStep, place: Literal["transport", "work"], token: bool
) -> None:
    assert network_step(step) == place
    assert carries_token(step) is token


def test_every_step_that_carries_the_token_runs_in_the_transport() -> None:
    steps: tuple[TransportStep, ...] = get_args(TransportStep)
    assert [step for step in steps if carries_token(step) and network_step(step) != "transport"] == []


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("main", True),
        ("feature/login", True),
        ("release-1.2", True),
        ("", False),
        ("HEAD", False),
        ("-delete", False),
        ("a..b", False),
        ("a b", False),
        ("a~1", False),
        ("a:b", False),
        ("a\\b", False),
        ("a@{1}", False),
        ("topic.lock", False),
        ("a/.hidden", False),
        ("a//b", False),
        ("trailing/", False),
        ("dot.", False),
        ("new\nline", False),
    ],
)
def test_branch_names_are_checked_before_they_become_refs(name: str, expected: bool) -> None:
    assert safe_branch_name(name) is expected


@pytest.mark.parametrize(
    ("bundle", "current", "expected"),
    [
        ([(SHA_A, "refs/heads/main")], {}, (RefUpdate(TRACKING + "main", SHA_A),)),
        ([(SHA_A, "refs/heads/main")], {TRACKING + "main": SHA_A}, ()),
        ([(SHA_B, "refs/heads/main")], {TRACKING + "main": SHA_A}, (RefUpdate(TRACKING + "main", SHA_B),)),
        ([], {TRACKING + "gone": SHA_A}, (RefUpdate(TRACKING + "gone", None),)),
        ([], {TRACKING + "HEAD": SHA_A}, ()),
        ([(SHA_A, "HEAD")], {}, ()),
        ([(SHA_A, "refs/tags/v1")], {}, ()),
        ([(SHA_A, "refs/heads/a b")], {}, ()),
        ([(SHA_A, "refs/heads/-x")], {}, ()),
        ([("not-a-sha", "refs/heads/main")], {}, ()),
        ([(SHA_A, "refs/heads/b"), (SHA_B, "refs/heads/a")], {}, (
            RefUpdate(TRACKING + "a", SHA_B),
            RefUpdate(TRACKING + "b", SHA_A),
        )),
        (
            [(SHA_A, "refs/heads/keep")],
            {TRACKING + "keep": SHA_A, TRACKING + "old": SHA_B, TRACKING + "bad name": SHA_C},
            (RefUpdate(TRACKING + "old", None),),
        ),
    ],
)
def test_tracking_refs_mirror_the_bundle_and_never_touch_origin_head(
    bundle: list[tuple[str, str]], current: dict[str, str], expected: tuple[RefUpdate, ...]
) -> None:
    assert tracking_updates(bundle, current) == expected


@pytest.mark.parametrize(
    ("refs", "valid"),
    [
        ([(SHA_A, "refs/heads/main")], True),
        ([(SHA_B, "refs/heads/main")], False),
        ([(SHA_A, "refs/heads/other")], False),
        ([], False),
        ([(SHA_A, "refs/heads/main"), (SHA_A, "refs/heads/other")], False),
        ([(SHA_A, "refs/heads/main"), (SHA_A, "HEAD")], False),
    ],
)
def test_a_push_bundle_holds_exactly_the_branch_and_commit_asked_for(
    refs: list[tuple[str, str]], valid: bool
) -> None:
    if valid:
        check_push_bundle(refs, "main", SHA_A)
    else:
        with pytest.raises(GitError):
            check_push_bundle(refs, "main", SHA_A)


def test_the_transport_is_beside_the_workspace_and_named_by_the_folder(tmp_path: Path) -> None:
    runners = RUNNERS.__class__(workspace_dir=tmp_path / "data" / "workspace")
    first = transport_path(tmp_path / "a", runners)
    assert first.parent == tmp_path / "data" / "git-transport"
    assert first.name.endswith(".git") and len(first.stem) == 32
    assert transport_path(tmp_path / "b", runners) != first
    with pytest.raises(GitError):
        transport_path(tmp_path / "a", RUNNERS.__class__())


# --- The flows, with real git and a local remote -------------------------------------------------------------------


def git(*arguments: str, cwd: Path) -> str:
    identity = ["-c", "user.name=T", "-c", "user.email=t@example.com"]
    return subprocess.run(
        ["git", *identity, *arguments], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def commit_file(folder: Path, name: str, content: str) -> str:
    (folder / name).write_text(content, encoding="utf-8")
    git("add", name, cwd=folder)
    git("commit", "-m", f"Add {name}", cwd=folder)
    return git("rev-parse", "HEAD", cwd=folder)


class Processes:
    """Every process YAAP starts, with where it ran and whether it carried the token."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], Path | None, dict[str, str]]] = []

    def with_token(self) -> list[tuple[list[str], Path | None, dict[str, str]]]:
        return [call for call in self.calls if any(TOKEN_VALUE == value for value in call[2].values())]


@pytest.fixture
def processes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Processes:
    """Local repositories as remotes, a stand-in token and a record of every process."""
    monkeypatch.setattr(git_remote, "validate_remote_url", lambda url: url)
    monkeypatch.setattr(git_remote, "auth_config", lambda url, token: TOKEN_CONFIG)
    monkeypatch.setattr(service, "remote_operation_variables", lambda: {"GIT_ALLOW_PROTOCOL": "https:file"})
    monkeypatch.setattr(RUNNERS, "workspace_dir", tmp_path / "data" / "workspace")
    recorded = Processes()
    real_run = subprocess.run

    def record(command: list[str], **keywords: object) -> subprocess.CompletedProcess[str]:
        env = keywords.get("env")
        variables = dict(env) if isinstance(env, dict) else {}
        recorded.calls.append((list(command), keywords.get("cwd"), variables))  # type: ignore[arg-type]  # test double
        return real_run(command, **keywords)  # type: ignore[call-overload]  # forwards the caller's keywords

    monkeypatch.setattr(service.subprocess, "run", record)
    return recorded


def assert_token_only_in_the_transport(processes: Processes, tmp_path: Path, *work_folders: Path) -> None:
    root = tmp_path / "data" / "git-transport"
    carrying = processes.with_token()
    assert carrying, "no command carried the token"
    for command, cwd, env in carrying:
        assert cwd is not None and (cwd == root or root in Path(cwd).parents), (command, cwd)
        assert env.get("GIT_DIR", str(root)).startswith(str(root)), command
        for folder in work_folders:
            assert str(folder) not in " ".join(command)
            assert Path(cwd) != folder


def make_remote(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    git("init", "--bare", "-b", "main", str(remote), cwd=tmp_path)
    return remote


def make_work(tmp_path: Path, remote: Path, name: str = "work") -> Path:
    work = tmp_path / name
    git("init", "-b", "main", str(work), cwd=tmp_path)
    git("remote", "add", "origin", str(remote), cwd=work)
    return work


def seed_remote(tmp_path: Path, remote: Path) -> str:
    seed = tmp_path / "seed"
    git("clone", str(remote), str(seed), cwd=tmp_path)
    git("checkout", "-b", "main", cwd=seed)
    sha = commit_file(seed, "readme.md", "one\n")
    git("push", "origin", "main", cwd=seed)
    git("checkout", "-b", "other", cwd=seed)
    commit_file(seed, "other.md", "other\n")
    git("push", "origin", "other", cwd=seed)
    return sha


def test_fetch_brings_every_branch_to_the_work_repository_and_drops_deleted_ones(
    tmp_path: Path, processes: Processes
) -> None:
    remote = make_remote(tmp_path)
    main_sha = seed_remote(tmp_path, remote)
    work = make_work(tmp_path, remote)

    git_remote.fetch(work, "token")

    assert git("rev-parse", "origin/main", cwd=work) == main_sha
    assert git("for-each-ref", "--format=%(refname)", "refs/remotes/origin", cwd=work).split() == [
        "refs/remotes/origin/main",
        "refs/remotes/origin/other",
    ]
    assert_token_only_in_the_transport(processes, tmp_path, work)
    git("branch", "-D", "other", cwd=remote)
    git_remote.fetch(work, "token")
    assert git("for-each-ref", "--format=%(refname)", "refs/remotes/origin", cwd=work).split() == [
        "refs/remotes/origin/main"
    ]
    assert not (work / ".git" / "refs" / "remotes" / "origin" / "other").exists()


def test_push_sends_the_branch_as_a_bundle_and_sets_the_upstream(tmp_path: Path, processes: Processes) -> None:
    remote = make_remote(tmp_path)
    work = make_work(tmp_path, remote)
    first = commit_file(work, "a.txt", "one\n")

    git_remote.push(work, "token")

    assert git("rev-parse", "main", cwd=remote) == first
    assert git("rev-parse", "origin/main", cwd=work) == first
    assert git("rev-parse", "--abbrev-ref", "main@{upstream}", cwd=work) == "origin/main"
    second = commit_file(work, "b.txt", "two\n")
    git_remote.push(work, "token")  # an incremental bundle, as the transport already has the first commit
    assert git("rev-parse", "main", cwd=remote) == second
    assert_token_only_in_the_transport(processes, tmp_path, work)


def test_push_is_refused_when_the_remote_moved_on(tmp_path: Path, processes: Processes) -> None:
    remote = make_remote(tmp_path)
    seed_remote(tmp_path, remote)
    work = make_work(tmp_path, remote)
    git_remote.fetch(work, None)
    git("checkout", "-b", "main", "--track", "origin/main", cwd=work)
    commit_file(work, "local.txt", "local\n")
    elsewhere = tmp_path / "elsewhere"
    git("clone", str(remote), str(elsewhere), cwd=tmp_path)
    commit_file(elsewhere, "remote.txt", "remote\n")
    git("push", "origin", "main", cwd=elsewhere)

    with pytest.raises(GitError, match="push failed"):
        git_remote.push(work, "token")


def test_push_needs_commits_and_a_branch(tmp_path: Path, processes: Processes) -> None:
    work = make_work(tmp_path, make_remote(tmp_path))
    with pytest.raises(GitError, match="no commits"):
        git_remote.push(work, None)


def test_pull_fast_forwards_and_reports_divergence(tmp_path: Path, processes: Processes) -> None:
    remote = make_remote(tmp_path)
    seed_remote(tmp_path, remote)
    work = make_work(tmp_path, remote)
    git_remote.fetch(work, None)
    git("checkout", "-b", "main", "--track", "origin/main", cwd=work)
    elsewhere = tmp_path / "elsewhere"
    git("clone", str(remote), str(elsewhere), cwd=tmp_path)
    commit_file(elsewhere, "new.txt", "new\n")
    git("push", "origin", "main", cwd=elsewhere)

    git_remote.pull(work, None)
    assert (work / "new.txt").read_text(encoding="utf-8") == "new\n"

    commit_file(work, "mine.txt", "mine\n")
    commit_file(elsewhere, "theirs.txt", "theirs\n")
    git("push", "origin", "main", cwd=elsewhere)
    with pytest.raises(GitError, match="diverged"):
        git_remote.pull(work, None)


def test_clone_builds_the_work_repository_from_the_transport_and_checks_out_the_default_branch(
    tmp_path: Path, processes: Processes
) -> None:
    remote = make_remote(tmp_path)
    seed_remote(tmp_path, remote)
    target = tmp_path / "workspace-site"

    git_remote.clone_repository(str(remote), target, "token")

    assert (target / "readme.md").read_text(encoding="utf-8") == "one\n"
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=target) == "main"
    assert git("rev-parse", "--abbrev-ref", "main@{upstream}", cwd=target) == "origin/main"
    assert git("remote", "get-url", "origin", cwd=target) == str(remote)
    assert git("rev-parse", "origin/other", cwd=target)
    assert_token_only_in_the_transport(processes, tmp_path, target)


def test_clone_of_an_empty_remote_leaves_an_empty_repository_with_origin(
    tmp_path: Path, processes: Processes
) -> None:
    remote = make_remote(tmp_path)
    target = tmp_path / "empty"

    git_remote.clone_repository(str(remote), target, None)

    assert git("remote", "get-url", "origin", cwd=target) == str(remote)
    assert git("for-each-ref", cwd=target) == ""


def test_a_failed_clone_leaves_no_folder_behind(tmp_path: Path, processes: Processes) -> None:
    target = tmp_path / "missing"
    with pytest.raises(GitError):
        git_remote.clone_repository(str(tmp_path / "does-not-exist.git"), target, None)
    assert not target.exists()


def test_a_periodic_fetch_wakes_the_work_repository_only_when_the_remote_changed(
    tmp_path: Path, processes: Processes
) -> None:
    remote = make_remote(tmp_path)
    seed_remote(tmp_path, remote)
    work = make_work(tmp_path, remote)
    delivered: list[Path] = []

    def place_of() -> GitPlace:
        delivered.append(work)
        return GitPlace(work)

    assert git_remote.fetch_changes(work, place_of, "token") is True
    assert git_remote.fetch_changes(work, place_of, "token") is False
    commit = tmp_path / "seed"
    commit_file(commit, "more.md", "more\n")
    git("push", "origin", "other", cwd=commit)
    assert git_remote.fetch_changes(work, place_of, "token") is True
    assert delivered == [work, work]
    assert git("rev-parse", "origin/other", cwd=work) == git("rev-parse", "other", cwd=remote)


# --- A work repository in a runner ---------------------------------------------------------------------------------


class RunnerStandIn:
    """Answers git commands about a work repository in a runner; bundles of the work repository are real."""

    def __init__(self, work: Path, environment_dir: Path, answers: dict[str, bytes]) -> None:
        self.work = work
        self.environment_dir = environment_dir
        self.answers = answers
        self.commands: list[str] = []
        self.incoming: list[bytes] = []

    async def capture(self, command: str, timeout: float) -> CapturedResult:
        self.commands.append(command)
        words = shlex.split(command)
        start = words.index("bundle") if "bundle" in words else -1
        if start >= 0 and words[start + 1 :][:2] == ["create", "-"]:
            done = subprocess.run(["git", *words[start:]], cwd=self.work, capture_output=True, check=False)
            return CapturedResult(done.returncode, done.stdout, done.stderr)
        if start >= 0 and words[start + 1] == "unbundle":
            path = self.environment_dir / Path(words[-1]).relative_to("/workspace")
            self.incoming.append(path.read_bytes())
        for needle, answer in self.answers.items():
            if needle in command:
                return CapturedResult(0, answer, b"")
        return CapturedResult(0, b"", b"")


def in_worker(function: Callable[[], object]) -> object:
    async def call() -> object:
        return await anyio.to_thread.run_sync(function)

    return asyncio.run(call())


def test_a_fetch_for_a_runner_hands_the_bundle_in_a_file_and_updates_tracking_refs(
    tmp_path: Path, processes: Processes
) -> None:
    remote = make_remote(tmp_path)
    main_sha = seed_remote(tmp_path, remote)
    work = tmp_path / "data" / "workspace" / "env" / "site"
    work.mkdir(parents=True)
    environment_dir = tmp_path / "data" / "workspace" / "env"
    runner = RunnerStandIn(
        work,
        environment_dir,
        {
            "remote get-url": f"{remote}\n".encode(),
            "bundle unbundle": f"{main_sha} refs/heads/main\n{'d' * 40} refs/heads/other\n".encode(),
        },
    )
    place = GitPlace(work, runner, environment_dir)  # type: ignore[arg-type]  # minimal executor double

    in_worker(lambda: git_remote.fetch(place, "token"))

    unbundle = next(command for command in runner.commands if "bundle unbundle" in command)
    assert "/workspace/.yaap-incoming/" in unbundle and unbundle.endswith(".bundle")
    assert runner.incoming and runner.incoming[0].startswith(b"# v2 git bundle")
    assert list((environment_dir / ".yaap-incoming").iterdir()) == []
    updates = [command for command in runner.commands if "update-ref" in command]
    assert any(f"refs/remotes/origin/main {main_sha}" in command for command in updates)
    assert not any("AUTHORIZATION" in command or " fetch " in command for command in runner.commands)
    assert_token_only_in_the_transport(processes, tmp_path, work)


def test_a_push_for_a_runner_takes_the_bundle_from_the_runner_and_pushes_from_the_transport(
    tmp_path: Path, processes: Processes
) -> None:
    remote = make_remote(tmp_path)
    work = make_work(tmp_path, remote, "data/workspace/env/site")
    sha = commit_file(work, "a.txt", "one\n")
    runner = RunnerStandIn(
        work,
        tmp_path / "data" / "workspace" / "env",
        {
            "remote get-url": f"{remote}\n".encode(),
            "status --porcelain": f"# branch.oid {sha}\n# branch.head main\n".encode(),
            "rev-parse --verify refs/heads/main": f"{sha}\n".encode(),
        },
    )
    place = GitPlace(work, runner, tmp_path / "data" / "workspace" / "env")  # type: ignore[arg-type]  # double

    in_worker(lambda: git_remote.push(place, "token"))

    assert git("rev-parse", "main", cwd=remote) == sha
    assert any("bundle create -" in command for command in runner.commands)
    assert any("--set-upstream-to=origin/main" in command for command in runner.commands)
    assert not any("AUTHORIZATION" in command or " push " in command for command in runner.commands)
    assert_token_only_in_the_transport(processes, tmp_path, work)


def test_a_bundle_cannot_reach_a_runner_place_without_its_environment_folder(tmp_path: Path) -> None:
    place = GitPlace(tmp_path, RunnerStandIn(tmp_path, tmp_path, {}))  # type: ignore[arg-type]  # minimal double
    with pytest.raises(GitError, match="runner folder"), bundles.bundle_on_place(place, b"data"):
        pass
