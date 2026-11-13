"""The security rules of a board in a separate repository: which URLs may hold a board, and that the GitHub token
reaches git only through its environment, never a command line, an error or a log."""

import base64
import logging
import subprocess
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.board.errors import BoardError, InvalidRepositoryUrl, NoRemote
from app.board.remote import RemoteBoard, checked_board_url, pull_step
from app.board.settings import plain_https, push_offered, same_remote, validate_repository_url
from app.board.storage import PullSchedule, RepositoryStorage, pull_due
from app.git import service as git_service

TOKEN = "ghp_secretvalue123"
URL = "https://github.com/owner/board.git"


@pytest.mark.parametrize(
    ("url", "plain"),
    [
        ("https://github.com/owner/board.git", True),
        ("https://git.example.com:8443/team/board", True),
        ("http://github.com/owner/board.git", False),
        ("https://user:secret@github.com/owner/board.git", False),
        ("https://token@github.com/owner/board.git", False),
        ("git@github.com:owner/board.git", False),
        ("ssh://git@github.com/owner/board.git", False),
        ("file:///tmp/board.git", False),
        ("/tmp/board.git", False),
        ("https://github.com/", False),
        ("https://github.com/owner/board .git", False),
        ("", False),
    ],
)
def test_only_a_plain_https_url_can_hold_a_board(url, plain):
    assert plain_https(url) is plain
    # The validator does not use the provider registry.
    accepted = validate_repository_url(url, None) is None  # type: ignore[arg-type]  # no registry needed
    assert accepted is (plain or url == "")


@pytest.mark.parametrize(
    ("url", "project_remote", "same"),
    [
        ("https://github.com/owner/project.git", "https://github.com/owner/project.git", True),
        ("https://github.com/Owner/Project", "https://github.com/owner/project.git", True),
        ("https://github.com/owner/project/", "git@github.com:owner/project.git", True),
        ("https://github.com/owner/project.git", "ssh://git@github.com/owner/project", True),
        ("https://github.com/owner/project-board.git", "https://github.com/owner/project.git", False),
        ("https://github.com/other/project.git", "https://github.com/owner/project.git", False),
        ("https://gitlab.com/owner/project.git", "https://github.com/owner/project.git", False),
        ("https://github.com/owner/project.git", None, False),
    ],
)
def test_the_projects_own_remote_cannot_hold_its_board(url, project_remote, same):
    assert same_remote(url, project_remote) is same


@pytest.mark.parametrize(
    ("url", "project_remote", "refusal"),
    [
        (URL, None, None),
        (URL, "https://github.com/owner/project.git", None),
        (URL, "git@github.com:owner/board.git", InvalidRepositoryUrl),
        ("https://x:y@github.com/owner/board.git", None, InvalidRepositoryUrl),
        ("http://github.com/owner/board.git", None, InvalidRepositoryUrl),
        (None, None, NoRemote),
        ("", None, NoRemote),
    ],
)
def test_a_board_url_is_checked_where_it_is_used(url, project_remote, refusal):
    if refusal is None:
        assert checked_board_url(url, project_remote) == url
    else:
        with pytest.raises(refusal):
            checked_board_url(url, project_remote)


@pytest.mark.parametrize(
    ("local", "remote", "local_in_remote", "remote_in_local", "step"),
    [
        (None, None, False, False, "nothing"),
        ("a", None, False, False, "nothing"),
        ("a", "a", True, True, "nothing"),
        (None, "b", False, False, "take"),
        ("a", "b", True, False, "take"),
        ("b", "a", False, True, "ahead"),
        ("a", "b", False, False, "diverged"),
    ],
)
def test_a_pull_only_fast_forwards(local, remote, local_in_remote, remote_in_local, step):
    assert pull_step(local, remote, local_in_remote, remote_in_local) == step


@pytest.mark.parametrize(
    ("storage", "board_push", "offered"),
    [("branch", False, False), ("branch", True, True), ("repository", False, True), ("repository", True, True)],
)
def test_push_is_offered_for_a_separate_repository_and_for_the_branch_only_when_switched_on(
    storage, board_push, offered
):
    assert push_offered(storage, board_push) is offered


@pytest.mark.parametrize(("last", "now", "due"), [(None, 0.0, True), (100.0, 159.0, False), (100.0, 160.0, True)])
def test_a_board_is_pulled_at_most_once_a_minute(last, now, due):
    assert pull_due(last, now) is due


def fake_git(calls: list[tuple[list[str], dict[str, str]]]):
    """Git that knows the board's branch and fails every network command, as when offline."""

    def run(command: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        environment = kwargs.get("env")
        calls.append((list(command), dict(environment) if isinstance(environment, dict) else {}))
        if "symbolic-ref" in command:
            return subprocess.CompletedProcess(list(command), 0, "refs/heads/main\n", "")
        return subprocess.CompletedProcess(list(command), 128, "", "fatal: unable to access the repository")

    return run


def test_the_token_reaches_git_only_through_its_environment_and_never_a_command_line_or_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    calls: list[tuple[list[str], dict[str, str]]] = []
    monkeypatch.setattr(git_service.subprocess, "run", fake_git(calls))
    caplog.set_level(logging.DEBUG)
    remote = RemoteBoard(tmp_path / "boards" / "1.git")
    errors: list[str] = []
    for action in (
        lambda: remote.ensure_clone(URL, TOKEN),
        lambda: remote.fetch(URL, TOKEN),
        lambda: remote.push(URL, TOKEN, "a" * 40),
        lambda: remote.check_access(URL, TOKEN),
    ):
        with pytest.raises(BoardError) as raised:
            action()
        errors.append(str(raised.value))
    storage = RepositoryStorage(
        tmp_path,
        url_of=lambda project: URL,
        token_of=lambda project: TOKEN,
        project_remote=lambda project: None,
        schedule=PullSchedule(start=lambda work: work()),
    )
    with pytest.raises(BoardError):
        storage.open(SimpleNamespace(id=1, environment_id=1, name="p"))  # type: ignore[arg-type]  # a stand-in project

    header = base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
    network = [environment for command, environment in calls if {"clone", "fetch", "push", "ls-remote"} & set(command)]
    assert len(network) >= 5
    for command, _ in calls:
        assert not any(TOKEN in part or header in part for part in command)
    for environment in network:
        assert any(header in value for key, value in environment.items() if key.startswith("GIT_CONFIG_VALUE_"))
    assert not any(TOKEN in error or header in error for error in errors)
    assert TOKEN not in caplog.text and header not in caplog.text
