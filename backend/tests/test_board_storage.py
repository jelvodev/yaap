"""A board in a separate repository, end to end against a local bare "remote": starting it, the first push, a pull of
an outside commit and the index after it, divergence, offline use and the backup. The URL rules and the token are in
test_board_remote.py."""

import json
import os
import shutil
import subprocess
import zipfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.board.card import Card, parse_card, render_card
from app.board.models import BoardCardRow
from app.board.remote import RemoteBoard, board_repository_path
from app.board.storage import PullSchedule
from app.core.backup import backups_dir, create_backup
from app.git import service as git_service
from tests.board_service_support import add_card, board_service, board_url, make_project, project_folder, git as project_git

IDENTITY = {
    "GIT_AUTHOR_NAME": "Someone",
    "GIT_AUTHOR_EMAIL": "someone@example.com",
    "GIT_COMMITTER_NAME": "Someone",
    "GIT_COMMITTER_EMAIL": "someone@example.com",
}


def run(*arguments: str, cwd: Path) -> str:
    done = subprocess.run(
        ["git", *arguments], cwd=cwd, capture_output=True, text=True, check=True, env={**os.environ, **IDENTITY}
    )
    return done.stdout.strip()


@pytest.fixture
def remote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty bare repository to keep a board in; local file remotes are let through for these tests only."""
    monkeypatch.setattr(git_service, "remote_operation_variables", lambda: {"GIT_ALLOW_PROTOCOL": "https:file"})
    path = tmp_path / "remote.git"
    run("init", "--bare", "-b", "main", str(path), cwd=tmp_path)
    return path


def remote_head(remote: Path) -> str:
    return run("rev-parse", "main", cwd=remote)


def outside_commit(remote: Path, work: Path, files: dict[str, str], message: str = "Outside edit") -> str:
    """Someone else commits to the board's repository from their own clone."""
    clone = work / f"outside-{len(list(work.glob('outside-*')))}"
    run("clone", "-q", str(remote), str(clone), cwd=work)
    for name, text in files.items():
        target = clone / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
    run("add", "-A", cwd=clone)
    run("commit", "-q", "-m", message, cwd=clone)
    run("push", "-q", "origin", "HEAD:main", cwd=clone)
    return run("rev-parse", "HEAD", cwd=clone)


def card_text(card_id: int, title: str) -> str:
    now = datetime(2026, 11, 1, tzinfo=UTC)
    return render_card(Card(id=card_id, title=title, column="idea", created=now, updated=now))


def start_in_repository(client, remote: Path, name: str = "Site") -> dict:
    """A project whose board lives in `remote`; the app's board storage lets local test remotes through and pulls
    at once instead of in a thread."""
    service = board_service(client)
    repository = replace(
        service.storage.repository,
        url_check=lambda url: url,
        is_public=lambda url, token: False,
        schedule=PullSchedule(start=lambda work: work()),
    )
    object.__setattr__(service, "storage", replace(service.storage, repository=repository))
    project = make_project(client, name)
    response = client.post(board_url(project["id"]), json={"storage": "repository", "repository_url": str(remote)})
    assert response.status_code == 201, response.text
    return project


def cards_of(client, project: dict) -> dict[int, str]:
    columns = client.get(board_url(project["id"])).json()["columns"]
    return {card["id"]: card["title"] for column in columns.values() for card in column["cards"]}


def error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["detail"]["code"] == code
    return response.json()["detail"]


def test_a_board_started_in_a_separate_repository_is_cloned_and_remembered(client, remote, tmp_path):
    project = start_in_repository(client, remote)

    board = client.get(board_url(project["id"])).json()

    assert (board["state"], board["storage"], board["head"]) == ("ready", "repository", None)
    assert board["location"].startswith("Repository ")
    assert RemoteBoard(board_repository_path(tmp_path, project["id"])).exists()
    settings = {item["key"]: item["value"] for item in client.get(f"/api/v1/projects/{project['id']}/settings").json()}
    assert settings["board_storage"] == "repository" and settings["board_repository_url"] == str(remote)


def test_the_first_card_is_written_in_the_clone_and_pushed_to_the_repository(client, remote, tmp_path):
    project = start_in_repository(client, remote)
    card = add_card(client, project["id"], "First")
    head = client.get(board_url(project["id"])).json()["head"]

    assert "yaap-board" not in project_git(project_folder(client, project), "branch", "--list")
    assert client.get(board_url(project["id"])).json()["push"] == {"allowed": True, "pushed": False}
    pushed = client.post(board_url(project["id"], "/push"), json={})

    assert pushed.status_code == 200 and pushed.json() == {"pushed_commit": head}
    assert remote_head(remote) == head
    assert run("show", "main:cards/1.md", cwd=remote).count("First") == 1 and card["id"] == 1
    assert client.get(board_url(project["id"])).json()["push"]["pushed"] is True


def test_an_outside_commit_is_pulled_and_the_index_follows(client, remote, tmp_path):
    project = start_in_repository(client, remote)
    add_card(client, project["id"], "First")
    client.post(board_url(project["id"], "/push"), json={})
    outside_commit(remote, tmp_path, {"cards/1.md": card_text(1, "First, edited elsewhere"), "cards/2.md": card_text(2, "Second")})

    pulled = client.post(board_url(project["id"], "/pull"))

    assert pulled.status_code == 200 and pulled.json()["head"] == remote_head(remote)
    assert cards_of(client, project) == {1: "First, edited elsewhere", 2: "Second"}
    with board_service(client).session_factory() as session:
        rows = {row.card_id: row.title for row in session.query(BoardCardRow).filter_by(project_id=project["id"])}
    assert rows == {1: "First, edited elsewhere", 2: "Second"}
    assert add_card(client, project["id"], "Third")["id"] == 3


def test_a_pull_with_nothing_new_changes_nothing(client, remote, tmp_path):
    project = start_in_repository(client, remote)
    add_card(client, project["id"], "First")
    client.post(board_url(project["id"], "/push"), json={})
    head = client.get(board_url(project["id"])).json()["head"]

    assert client.post(board_url(project["id"], "/pull")).json() == {"head": head}


def test_a_pull_is_refused_when_both_sides_moved_and_nothing_is_changed(client, remote, tmp_path):
    project = start_in_repository(client, remote)
    add_card(client, project["id"], "First")
    client.post(board_url(project["id"], "/push"), json={})
    add_card(client, project["id"], "Only here")
    local_head = client.get(board_url(project["id"])).json()["head"]
    outside_commit(remote, tmp_path, {"cards/2.md": card_text(2, "Only there")})

    error(client.post(board_url(project["id"], "/pull")), 409, "board_diverged")

    assert client.get(board_url(project["id"])).json()["head"] == local_head
    assert cards_of(client, project) == {1: "First", 2: "Only here"}


def test_a_push_to_a_repository_that_moved_says_to_pull_first(client, remote, tmp_path):
    project = start_in_repository(client, remote)
    add_card(client, project["id"], "First")
    client.post(board_url(project["id"], "/push"), json={})
    add_card(client, project["id"], "Second")
    outside = outside_commit(remote, tmp_path, {"cards/3.md": card_text(3, "Outside")})

    detail = error(client.post(board_url(project["id"], "/push"), json={}), 502, "push_failed")

    assert "Pull first" in detail["message"]
    assert remote_head(remote) == outside


def test_a_public_or_unknown_repository_needs_confirmation_before_the_board_is_pushed(client, remote):
    project = start_in_repository(client, remote)
    add_card(client, project["id"], "First")
    repository = replace(board_service(client).storage.repository, is_public=lambda url, token: None)
    object.__setattr__(board_service(client), "storage", replace(board_service(client).storage, repository=repository))

    error(client.post(board_url(project["id"], "/push"), json={}), 409, "public_remote")
    assert not run("for-each-ref", "refs/heads", cwd=remote)
    assert client.post(board_url(project["id"], "/push"), json={"acknowledge_public": True}).status_code == 200


def test_the_board_works_offline_and_catches_up_when_the_repository_is_back(client, remote, tmp_path):
    project = start_in_repository(client, remote)
    add_card(client, project["id"], "First")
    client.post(board_url(project["id"], "/push"), json={})
    gone = tmp_path / "remote-away.git"
    shutil.move(str(remote), str(gone))

    assert cards_of(client, project) == {1: "First"}
    assert add_card(client, project["id"], "Written offline")["id"] == 2
    error(client.post(board_url(project["id"], "/pull")), 502, "pull_failed")
    error(client.post(board_url(project["id"], "/push"), json={}), 502, "push_failed")
    assert cards_of(client, project) == {1: "First", 2: "Written offline"}

    shutil.move(str(gone), str(remote))
    assert client.post(board_url(project["id"], "/pull")).status_code == 200
    assert client.post(board_url(project["id"], "/push"), json={}).status_code == 200
    assert run("show", "main:cards/2.md", cwd=remote).count("Written offline") == 1


def test_a_board_whose_clone_is_missing_is_unavailable_until_it_is_pulled_again(client, remote, tmp_path):
    project = start_in_repository(client, remote)
    add_card(client, project["id"], "First")
    client.post(board_url(project["id"], "/push"), json={})
    shutil.rmtree(
        board_repository_path(tmp_path, project["id"]), onexc=lambda function, path, _: (os.chmod(path, 0o700), function(path))
    )

    unavailable = client.get(board_url(project["id"])).json()
    assert unavailable["state"] in ("unavailable", "ready")

    assert client.post(board_url(project["id"], "/pull")).status_code == 200
    assert cards_of(client, project) == {1: "First"}


def test_a_board_pull_on_the_projects_own_branch_has_no_repository_to_pull(client):
    project = make_project(client)
    client.post(board_url(project["id"]), json={"storage": "branch", "create_repository": True})

    error(client.post(board_url(project["id"], "/pull")), 409, "no_remote")


def test_the_projects_own_remote_cannot_be_used_for_its_board(client, remote):
    service = board_service(client)
    repository = replace(service.storage.repository, url_check=lambda url: url)
    object.__setattr__(service, "storage", replace(service.storage, repository=repository))
    project = make_project(client)
    client.post(board_url(project["id"]), json={"storage": "branch", "create_repository": True})
    project_git(project_folder(client, project), "remote", "add", "origin", "https://github.com/owner/site.git")
    fresh = make_project(client, "Other")
    project_git(project_folder(client, fresh), "init", "-q")
    project_git(project_folder(client, fresh), "remote", "add", "origin", "https://github.com/owner/other.git")

    response = client.post(
        board_url(fresh["id"]), json={"storage": "repository", "repository_url": "https://github.com/owner/other.git"}
    )

    error(response, 422, "invalid_repository_url")
    assert client.get(board_url(fresh["id"])).json()["state"] == "not_started"


def test_a_repository_can_be_checked_before_the_board_starts_there(client, remote):
    project = start_in_repository(client, remote)
    other = make_project(client, "Other")

    reachable = client.post(board_url(other["id"], "/check-remote"), json={"repository_url": str(remote)})
    missing = client.post(board_url(other["id"], "/check-remote"), json={"repository_url": str(remote) + "-nowhere"})

    assert reachable.json() == {"ok": True, "private": True, "message": None}
    assert missing.json()["ok"] is False and missing.json()["message"]
    assert project["id"] != other["id"]


def test_a_board_is_pulled_at_most_once_a_minute_in_the_background():
    now = [1000.0]
    pulls: list[int] = []
    schedule = PullSchedule(start=lambda work: work(), clock=lambda: now[0])

    schedule.maybe(1, lambda: pulls.append(1))
    now[0] += 30
    schedule.maybe(1, lambda: pulls.append(1))
    schedule.maybe(2, lambda: pulls.append(2))
    now[0] += 31
    schedule.maybe(1, lambda: pulls.append(1))

    assert pulls == [1, 2, 1]


def test_a_failing_background_pull_does_not_stop_reading_the_board():
    def offline() -> None:
        from app.board.errors import PullFailed

        raise PullFailed()

    schedule = PullSchedule(start=lambda work: work())

    schedule.maybe(1, offline)  # must not raise


def test_the_backup_includes_the_boards_folder_and_a_restored_clone_is_usable(client, remote, tmp_path):
    project = start_in_repository(client, remote)
    add_card(client, project["id"], "First")
    head = client.get(board_url(project["id"])).json()["head"]

    info = create_backup(tmp_path)

    with zipfile.ZipFile(backups_dir(tmp_path) / info.name) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
        assert f"boards/{project['id']}.git/HEAD" in names
        assert manifest["files"]["boards"] > 0
        restored = tmp_path / "restored"
        archive.extractall(restored)
    clone = RemoteBoard(restored / "boards" / f"{project['id']}.git")
    assert clone.exists() and clone.rev("refs/heads/main") == head
