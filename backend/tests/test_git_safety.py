"""YAAP's own git runs nothing a repository names: hooks, the fsmonitor, filter and diff drivers,
includes, pagers and upload-pack commands written into a project by a command in the runner never run with YAAP's
rights, while status, diff, commit, checkpoints, fetch, push and branches keep working."""

import os
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from app.checkpoints.shadow import ShadowRepository
from app.git import remote as git_remote, safety, service
from app.git.safety import (
    FIXED_CONFIG,
    UnsafeRepository,
    check_repository,
    git_command,
    git_variables,
    honoured,
    refused_keys,
)
from app.git.service import (
    GitError,
    commit_all,
    repository_diff,
    repository_status,
)
from app.git.branches import create_branch, list_branches, switch_branch
from app.git.remote import fetch, pull, push
from app.providers.base import AgentRequest
from app.providers.claude_code_provider import ClaudeCodeProvider, TurnInput, claude_code_environment


def host_git(*arguments: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Plain git as an attacker's command would run it, with an identity and nothing of YAAP's."""
    return subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def payload(marker: Path) -> str:
    """A shell command that leaves the marker behind; git runs configured commands through sh."""
    return f'echo pwned >> "{marker.as_posix()}"'


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    """A project with one commit, pushed to a local bare remote; and the marker file a payload would write."""
    remote = tmp_path / "remote.git"
    host_git("init", "--bare", "-b", "main", str(remote), cwd=tmp_path)
    folder = tmp_path / "site"
    host_git("clone", str(remote), str(folder), cwd=tmp_path)
    (folder / "index.txt").write_text("hello\n", encoding="utf-8")
    host_git("add", ".", cwd=folder)
    host_git("commit", "-m", "First", cwd=folder)
    host_git("push", "-u", "origin", "main", cwd=folder)
    # Local bare remotes keep these integration tests offline; transport policy is tested separately.
    monkeypatch.setattr(service, "remote_operation_variables", lambda: {})
    validate_url = git_remote.validate_remote_url
    monkeypatch.setattr(
        git_remote,
        "validate_remote_url",
        lambda url: url if url == str(remote) else validate_url(url),
    )
    return folder, remote, tmp_path / "marker"


def set_config(folder: Path, key: str, value: str) -> None:
    subprocess.run(["git", "config", "--file", str(folder / ".git" / "config"), key, value], check=True)


def write_hook(hooks: Path, name: str, marker: Path) -> None:
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / name
    hook.write_text(f"#!/bin/sh\n{payload(marker)}\n", encoding="utf-8", newline="\n")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


HOOKS = ("pre-commit", "commit-msg", "post-commit", "post-checkout", "pre-push", "reference-transaction", "post-merge")


def plant_hooks(folder: Path, marker: Path) -> None:
    for name in HOOKS:
        write_hook(folder / ".git" / "hooks", name, marker)


def plant_hooks_path(folder: Path, marker: Path) -> None:
    for name in HOOKS:
        write_hook(folder / "tools" / "hooks", name, marker)
    set_config(folder, "core.hooksPath", "tools/hooks")


def plant_fsmonitor(folder: Path, marker: Path) -> None:
    set_config(folder, "core.fsmonitor", payload(marker))


def plant_filter(folder: Path, marker: Path) -> None:
    (folder / ".gitattributes").write_text("* filter=evil\n", encoding="utf-8")
    set_config(folder, "filter.evil.clean", f"{payload(marker)}; cat")
    set_config(folder, "filter.evil.smudge", f"{payload(marker)}; cat")


def plant_textconv(folder: Path, marker: Path) -> None:
    (folder / ".gitattributes").write_text("*.txt diff=evil\n", encoding="utf-8")
    set_config(folder, "diff.evil.textconv", f"{payload(marker)}; cat")


def plant_include(folder: Path, marker: Path) -> None:
    included = folder / "evil.cfg"
    included.write_text(f'[core]\n\tfsmonitor = "{payload(marker)}"\n', encoding="utf-8")
    set_config(folder, "include.path", str(included))


def plant_pager(folder: Path, marker: Path) -> None:
    set_config(folder, "core.pager", payload(marker))
    set_config(folder, "pager.status", payload(marker))


def plant_upload_pack(folder: Path, marker: Path) -> None:
    set_config(folder, "remote.origin.uploadpack", f"{payload(marker)}; git-upload-pack")


Plant = Callable[[Path, Path], None]

# Each repository-config vector with the key YAAP names and, where plain git can be made to run it here, how.
CONFIG_VECTORS: list[tuple[Plant, str, list[str] | None]] = [
    (plant_hooks_path, "core.hookspath", ["commit", "--allow-empty", "-m", "x"]),
    (plant_fsmonitor, "core.fsmonitor", ["status"]),
    (plant_filter, "filter.evil.clean", ["add", "--renormalize", "."]),
    (plant_textconv, "diff.evil.textconv", ["diff", "HEAD"]),
    (plant_include, "include.path", ["status"]),
    (plant_pager, "core.pager", None),  # a pager runs only on a terminal
    (plant_upload_pack, "remote.origin.uploadpack", ["fetch", "origin"]),
]


def change_file(folder: Path) -> None:
    (folder / "index.txt").write_text("changed\n", encoding="utf-8")


def take_checkpoint(folder: Path, tmp_path: Path) -> None:
    shadow = ShadowRepository(tmp_path / "checkpoints" / "1.git", folder)
    before = shadow.snapshot_tree()
    shadow.keep(1, shadow.commit(before, "before"))
    (folder / "new.txt").write_text("new\n", encoding="utf-8")
    after = shadow.snapshot_tree()
    assert "new.txt" in shadow.diff(before, after)
    shadow.restore(before, after)
    assert not (folder / "new.txt").exists()


GIT_OPERATIONS: list[tuple[str, Callable[[Path], object]]] = [
    ("status", repository_status),
    ("diff", lambda folder: (change_file(folder), repository_diff(folder))),
    ("commit", lambda folder: (change_file(folder), commit_all(folder, "Change"))),
    ("fetch", lambda folder: fetch(folder, None)),
    ("push", lambda folder: push(folder, None)),
    ("branch", lambda folder: (create_branch(folder, "feature/x"), switch_branch(folder, "main"))),
    ("branches", list_branches),
]


@pytest.mark.parametrize(
    ("plant", "key", "trigger"),
    CONFIG_VECTORS,
    ids=[vector[0].__name__.removeprefix("plant_") for vector in CONFIG_VECTORS],
)
def test_yaap_refuses_git_in_a_repository_whose_config_names_a_program(project, tmp_path, plant, key, trigger):
    folder, _, marker = project
    plant(folder, marker)
    if trigger is not None:
        # The repository is a real attack: plain git runs the payload.
        change_file(folder)
        host_git(*trigger, cwd=folder)
        assert marker.exists(), f"plain git did not run the {key} payload"
        (folder / "index.txt").write_text("hello\n", encoding="utf-8")
        marker.unlink()

    for name, operation in GIT_OPERATIONS:
        with pytest.raises(GitError) as refused:
            operation(folder)
        assert key in str(refused.value).lower() and "git config --unset-all" in str(refused.value), name
    # Checkpoints use YAAP's own repository; the project's settings and .gitattributes name nothing it runs.
    take_checkpoint(folder, tmp_path)
    assert not marker.exists()


def test_hooks_never_run_and_every_operation_still_works(project, tmp_path):
    folder, remote, marker = project
    plant_hooks(folder, marker)
    host_git("commit", "--allow-empty", "-m", "control", cwd=folder)
    assert marker.exists(), "plain git did not run the hook"
    marker.unlink()

    assert repository_status(folder).branch == "main"
    change_file(folder)
    assert "changed" in repository_diff(folder)
    commit_all(folder, "Change")
    push(folder, None)
    fetch(folder, None)
    pull(folder, None)
    create_branch(folder, "feature/x")
    switch_branch(folder, "main")
    assert {branch.name for branch in list_branches(folder)} == {"main", "feature/x"}
    take_checkpoint(folder, tmp_path)
    assert host_git("log", "--format=%s", "-1", "main", cwd=remote).stdout.strip() == "Change"
    assert not marker.exists()


def plant_config_hook(folder: Path, marker: Path) -> None:
    """A hook defined in config, which newer git runs for the events it names."""
    set_config(folder, "hook.evil.command", payload(marker))
    for event in ("pre-commit", "post-commit", "post-checkout", "pre-push", "reference-transaction"):
        subprocess.run(["git", "config", "--file", str(folder / ".git" / "config"), "--add", "hook.evil.event", event])


def plant_receive_pack(folder: Path, marker: Path) -> None:
    set_config(folder, "remote.origin.receivepack", f"{payload(marker)}; git-receive-pack")


SETTINGS_WITHOUT_CHECK = [
    plant_hooks_path,
    plant_config_hook,
    plant_fsmonitor,
    plant_pager,
    plant_upload_pack,
    plant_receive_pack,
]


@pytest.mark.parametrize(
    "plant", SETTINGS_WITHOUT_CHECK, ids=[plant.__name__.removeprefix("plant_") for plant in SETTINGS_WITHOUT_CHECK]
)
def test_fixed_settings_hold_even_without_the_check(project, tmp_path, monkeypatch, plant):
    """Settings at command scope and on the command line outrank the repository's own, so these stay off even if
    a process slipped them in after the check."""
    folder, _, marker = project
    plant(folder, marker)
    monkeypatch.setattr(service, "check_repository", lambda folder: None)
    repository_status(folder)
    change_file(folder)
    repository_diff(folder)
    commit_all(folder, "Change")
    push(folder, None)
    fetch(folder, None)
    create_branch(folder, "feature/x")
    switch_branch(folder, "main")
    assert not marker.exists()


def test_a_git_folder_outside_the_project_is_refused(project, tmp_path):
    folder, _, _ = project
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "site").mkdir()
    (elsewhere / "site" / ".git").write_text(f"gitdir: {(folder / '.git').as_posix()}\n", encoding="utf-8")
    with pytest.raises(GitError, match="outside the project folder"):
        repository_status(elsewhere / "site")


def test_borrowed_objects_from_outside_the_project_are_refused(project, tmp_path):
    folder, remote, _ = project
    alternates = folder / ".git" / "objects" / "info" / "alternates"
    alternates.parent.mkdir(parents=True, exist_ok=True)
    alternates.write_text(f"{(remote / 'objects').as_posix()}\n", encoding="utf-8")
    with pytest.raises(GitError, match="borrows objects"):
        repository_status(folder)


def test_git_does_not_look_above_the_project_for_a_repository(tmp_path):
    """A .git that a command writes into the environment's folder is never used for a project without one."""
    outer = tmp_path / "environment"
    host_git("init", "-b", "main", str(outer), cwd=tmp_path)
    marker = tmp_path / "marker"
    set_config(outer, "core.fsmonitor", payload(marker))
    inner = outer / "project"
    inner.mkdir()
    with pytest.raises(GitError, match="not a git repository"):
        repository_status(inner)
    assert not marker.exists()


def test_a_submodule_config_is_checked_too(project):
    folder, _, _ = project
    module = folder / ".git" / "modules" / "lib"
    module.mkdir(parents=True)
    (module / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (module / "config").write_text('[filter "evil"]\n\tclean = touch /tmp/x\n', encoding="utf-8")
    with pytest.raises(UnsafeRepository, match="filter.evil.clean"):
        check_repository(folder)


def test_a_new_config_is_checked_again(project):
    folder, _, _ = project
    check_repository(folder)
    set_config(folder, "core.fsmonitor", "true")
    with pytest.raises(UnsafeRepository, match="core.fsmonitor"):
        check_repository(folder)


@pytest.mark.parametrize("url", ["../other.git", "file:///tmp/other.git", "ext::sh", "ssh://host/repo.git"])
@pytest.mark.parametrize("operation", [fetch, pull, push])
def test_remote_operations_refuse_non_https_urls(project, url, operation):
    folder, _, _ = project
    set_config(folder, "remote.origin.url", url)
    with pytest.raises(GitError, match="https URL"):
        operation(folder, None)


def test_push_refuses_a_non_https_pushurl(project):
    folder, _, _ = project
    set_config(folder, "remote.origin.pushurl", "file:///tmp/other.git")
    with pytest.raises(GitError, match="https URL"):
        push(folder, None)


def test_remote_operations_are_restricted_to_https_protocol():
    assert service.remote_operation_variables() == {"GIT_ALLOW_PROTOCOL": "https"}


def test_a_config_changed_behind_an_old_modification_time_is_checked_again(project):
    """The check is kept per content, not per time and size, which a command can set back."""
    folder, _, _ = project
    config = folder / ".git" / "config"
    check_repository(folder)
    before = config.stat()
    text = config.read_bytes().decode("utf-8")
    plain = "logallrefupdates = true"
    evil = text.replace(plain, "fsmonitor = " + "x" * (len(plain) - len("fsmonitor = ")), 1)
    assert evil != text and len(evil) == len(text)
    config.write_bytes(evil.encode("utf-8"))
    os.utime(config, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(UnsafeRepository, match="core.fsmonitor"):
        check_repository(folder)


@pytest.mark.parametrize(
    ("key", "allowed"),
    [
        ("core.repositoryformatversion", True),
        ("core.autocrlf", True),
        ("remote.origin.url", True),
        ("remote.my.fork.fetch", True),
        ("branch.feature/x.merge", True),
        ("branch.main.vscode-merge-base", True),
        ("user.email", True),
        ("extensions.worktreeconfig", True),
        ("pull.rebase", True),
        ("submodule.lib.url", True),
        ("lfs.repositoryformatversion", True),
        ("diff.algorithm", True),
        ("merge.conflictstyle", True),
        ("core.hookspath", False),
        ("core.fsmonitor", False),
        ("core.pager", False),
        ("core.editor", False),
        ("core.sshcommand", False),
        ("core.askpass", False),
        ("core.gitproxy", False),
        ("core.worktree", False),
        ("core.attributesfile", False),
        ("sequence.editor", False),
        ("credential.helper", False),
        ("credential.https://github.com.helper", False),
        ("diff.external", False),
        ("diff.evil.textconv", False),
        ("diff.evil.command", False),
        ("filter.lfs.process", False),
        ("filter.evil.clean", False),
        ("merge.evil.driver", False),
        ("include.path", False),
        ("includeif.gitdir:/x/.path", False),
        ("remote.origin.uploadpack", False),
        ("remote.origin.receivepack", False),
        ("remote.origin.vcs", False),
        ("url.ext::sh.insteadof", False),
        ("gpg.program", False),
        ("gpg.ssh.program", False),
        ("protocol.ext.allow", False),
        ("http.cookiefile", False),
        ("pager.status", False),
        ("hook.pre-commit.command", False),
        ("submodule.lib.update", False),
        ("uploadpack.packobjectshook", False),
        ("difftool.evil.cmd", False),
        ("extensions.partialclone", False),
        ("trailer.sign.command", False),
    ],
)
def test_only_plain_settings_are_honoured(key, allowed):
    assert honoured(key) is allowed


def test_refused_keys_are_listed_once_in_order():
    keys = ["core.bare", "filter.a.clean", "core.fsmonitor", "filter.a.clean", "remote.origin.url"]
    assert refused_keys(keys) == ["filter.a.clean", "core.fsmonitor"]


def test_every_git_gets_the_fixed_settings_and_a_private_setting_only_in_its_environment(tmp_path):
    variables = git_variables(tmp_path / "site", [("http.https://github.com/.extraheader", "AUTHORIZATION: basic x")])
    assert variables["GIT_CONFIG_NOSYSTEM"] == "1"
    assert variables["GIT_CONFIG_GLOBAL"] == safety.GLOBAL_CONFIG
    assert variables["GIT_CEILING_DIRECTORIES"] == str(tmp_path.resolve())
    entries = {
        variables[f"GIT_CONFIG_KEY_{index}"]: variables[f"GIT_CONFIG_VALUE_{index}"]
        for index in range(int(variables["GIT_CONFIG_COUNT"]))
    }
    assert entries["core.hooksPath"] == os.devnull and entries["core.fsmonitor"] == "false"
    assert entries["http.https://github.com/.extraheader"] == "AUTHORIZATION: basic x"
    assert len(entries) == len(FIXED_CONFIG) + 1
    command = git_command(["status"], ["core.quotepath=false"])
    assert command == ["git", "--no-pager", "-c", "core.quotepath=false", "status"]


def test_claude_code_runs_git_with_the_same_settings_and_without_project_settings(project):
    folder, _, _ = project
    environment = claude_code_environment(None, folder)
    assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
    assert environment["GIT_CEILING_DIRECTORIES"] == str(folder.resolve().parent)
    assert "core.fsmonitor" in environment.values()
    request = AgentRequest(model="claude-opus-5-5", effort="high", system=[], messages=[], working_folder=folder)
    arguments = ClaudeCodeProvider(["claude"]).build_arguments(request, TurnInput(None, []), folder / "prompt.md")
    assert arguments[arguments.index("--setting-sources") + 1] == "user"



@pytest.mark.parametrize(
    ("has_head", "has_objects", "has_refs", "has_dot_git", "bare"),
    [
        (True, True, True, False, True),
        (True, True, True, True, False),  # a work tree with its own .git is checked through that .git
        (False, True, True, False, False),
        (True, False, True, False, False),
        (True, True, False, False, False),
        (False, False, False, False, False),
    ],
)
def test_a_bare_repository_is_recognised_by_gits_own_layout(has_head, has_objects, has_refs, has_dot_git, bare):
    assert safety.bare_layout(has_head, has_objects, has_refs, has_dot_git) is bare


def bare_repository(tmp_path: Path) -> Path:
    folder = tmp_path / "boards" / "1.git"
    folder.parent.mkdir(parents=True)
    host_git("init", "--quiet", "--bare", str(folder), cwd=folder.parent)
    return folder


@pytest.mark.parametrize(
    ("key", "value", "accepted"),
    [
        (None, None, True),
        ("remote.origin.url", "https://github.com/owner/board.git", True),
        ("remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*", True),
        ("filter.evil.clean", "touch x", False),
        ("core.worktree", "/tmp", False),
        ("core.sshcommand", "touch x", False),
        ("include.path", "/tmp/other", False),
        ("remote.origin.uploadpack", "touch x", False),
        ("credential.helper", "store", False),
    ],
)
def test_a_bare_repository_named_by_yaap_is_checked_like_any_other(tmp_path, key, value, accepted):
    folder = bare_repository(tmp_path)
    if key is not None:
        host_git("config", key, value, cwd=folder)
    if accepted:
        check_repository(folder)
        service.run_git(["rev-parse", "--is-bare-repository"], folder, {"GIT_DIR": str(folder)})
    else:
        with pytest.raises(UnsafeRepository, match=key):
            check_repository(folder)
        with pytest.raises(GitError):
            service.run_git(["rev-parse", "--is-bare-repository"], folder, {"GIT_DIR": str(folder)})


def test_a_bare_repository_borrowing_objects_from_outside_is_refused(tmp_path):
    folder = bare_repository(tmp_path)
    (folder / "objects" / "info").mkdir(parents=True, exist_ok=True)
    (folder / "objects" / "info" / "alternates").write_text(str(tmp_path / "elsewhere"), encoding="utf-8")
    with pytest.raises(UnsafeRepository, match="borrows objects"):
        check_repository(folder)
