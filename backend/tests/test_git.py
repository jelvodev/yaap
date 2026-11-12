import base64
import subprocess
from pathlib import Path

import pytest

from app.git import remote as git_remote, service
from app.git.service import (
    GitError,
    commit_all,
    repository_diff,
    repository_status,
    run_git,
)
from app.git.remote import auth_config, fetch, pull, push, validate_remote_url
from app.runner.service import RUNNERS


def git(*arguments: str, cwd: Path) -> str:
    return subprocess.run(["git", *arguments], cwd=cwd, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repositories(tmp_path, monkeypatch):
    """A bare 'remote' and two clones of it, as if on two machines."""
    monkeypatch.setattr(RUNNERS, "workspace_dir", tmp_path / "data" / "workspace")
    remote = tmp_path / "remote.git"
    git("init", "--bare", "-b", "main", str(remote), cwd=tmp_path)
    first, second = tmp_path / "first", tmp_path / "second"
    for clone in (first, second):
        git("clone", str(remote), str(clone), cwd=tmp_path)
    return remote, first, second


def allow_local_fixture_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let integration tests exercise local bare remotes without widening production transport policy."""
    monkeypatch.setattr(git_remote, "validate_remote_url", lambda url: url)
    monkeypatch.setattr(service, "remote_operation_variables", lambda: {"GIT_ALLOW_PROTOCOL": "https:file"})


def test_status_lists_changes_by_state(tmp_path):
    folder = tmp_path / "repo"
    git("init", "-b", "main", str(folder), cwd=tmp_path)
    (folder / "a.txt").write_text("one\n")
    status = repository_status(folder)
    assert status.branch == "main" and status.has_commits is False
    assert [(change.path, change.state) for change in status.changes] == [("a.txt", "untracked")]

    commit_all(folder, "Add a")
    (folder / "a.txt").write_text("two\n")
    (folder / "b file.txt").write_text("new\n")
    states = {change.path: change.state for change in repository_status(folder).changes}
    assert states == {"a.txt": "modified", "b file.txt": "untracked"}


def test_diff_includes_new_files_without_touching_the_index(tmp_path):
    folder = tmp_path / "repo"
    git("init", "-b", "main", str(folder), cwd=tmp_path)
    (folder / "a.txt").write_text("one\n")
    commit_all(folder, "Add a")
    (folder / "a.txt").write_text("two\n")
    (folder / "new.txt").write_text("brand new\n")

    diff = repository_diff(folder)
    assert "-one" in diff and "+two" in diff
    assert "+brand new" in diff
    assert git("diff", "--cached", "--name-only", cwd=folder) == ""


def test_commit_needs_changes_and_a_message(tmp_path):
    folder = tmp_path / "repo"
    git("init", "-b", "main", str(folder), cwd=tmp_path)
    with pytest.raises(GitError, match="message"):
        commit_all(folder, "  ")
    (folder / "a.txt").write_text("x")
    commit_all(folder, "First", author=("Alex", "alex@example.com"))
    assert git("log", "--format=%an <%ae> %s", cwd=folder).strip() == "Alex <alex@example.com> First"
    with pytest.raises(GitError, match="nothing to commit"):
        commit_all(folder, "Again")


def test_push_pull_and_divergence(repositories, monkeypatch):
    allow_local_fixture_remote(monkeypatch)
    _, first, second = repositories
    (first / "readme.md").write_text("hello\n")
    commit_all(first, "Add readme")
    push(first, None)

    fetch(second, None)
    assert repository_status(second).behind == 1
    pull(second, None)
    assert (second / "readme.md").read_text() == "hello\n"

    (first / "readme.md").write_text("from first\n")
    commit_all(first, "First edit")
    push(first, None)
    (second / "other.md").write_text("from second\n")
    commit_all(second, "Second edit")
    with pytest.raises(GitError, match="diverged"):
        pull(second, None)
    # Nothing changed in the local branch.
    assert git("log", "-1", "--format=%s", cwd=second).strip() == "Second edit"


def test_remote_urls_never_carry_credentials():
    assert validate_remote_url("https://github.com/example/yaap.git")
    for bad in (
        "https://token@github.com/example/yaap.git",
        "git@github.com:example/yaap.git",
        "http://github.com/x/y",
    ):
        with pytest.raises(GitError):
            validate_remote_url(bad)


def test_token_is_only_sent_to_github():
    ((key, header),) = auth_config("https://github.com/example/yaap.git", "secret-token")
    assert key == "http.https://github.com/.extraheader"
    assert header.startswith("AUTHORIZATION: basic ")
    assert base64.b64decode(header.split()[-1]).decode() == "x-access-token:secret-token"
    assert auth_config("https://gitlab.com/example/yaap.git", "secret-token") == ()
    assert auth_config("https://github.com/example/yaap.git", None) == ()


def test_git_api_lists_blueprint_and_projects_of_one_environment(client):
    client.post("/api/v1/environments/1/projects", json={"name": "Site"})
    client.post("/api/v1/environments/2/projects", json={"name": "Work tool"})

    repositories = client.get("/api/v1/environments/1/git/repositories").json()
    assert [repository["label"] for repository in repositories] == [
        "Blueprint · Global",
        "Blueprint · Private",
        "Site",
    ]
    work_project = client.get("/api/v1/environments/2/git/repositories").json()[-1]
    assert client.get(f"/api/v1/environments/1/git/repositories/{work_project['id']}").status_code == 404


def test_git_api_commit_and_remote(client, tmp_path):
    project = client.post("/api/v1/environments/1/projects", json={"name": "Site"}).json()
    folder = Path(tmp_path) / "workspace" / project["folder"]
    (folder / "index.html").write_text("<h1>Hi</h1>\n")
    client.put("/api/v1/settings/git_author_name", json={"value": "Alex"})
    client.put("/api/v1/settings/git_author_email", json={"value": "alex@example.com"})
    repository_id = f"project-{project['id']}"

    diff = client.get(f"/api/v1/environments/1/git/repositories/{repository_id}/diff").json()["diff"]
    assert "+<h1>Hi</h1>" in diff
    committed = client.post(
        f"/api/v1/environments/1/git/repositories/{repository_id}/commit", json={"message": "Add page"}
    )
    assert committed.status_code == 200 and committed.json()["changes"] == []
    assert git("log", "--format=%an", cwd=folder).strip() == "Alex"

    refused = client.put(
        f"/api/v1/environments/1/git/repositories/{repository_id}/remote", json={"url": "https://me:tok@github.com/x/y"}
    )
    assert refused.status_code == 409
    remote = client.put(
        f"/api/v1/environments/1/git/repositories/{repository_id}/remote",
        json={"url": "https://github.com/example/site.git"},
    )
    assert remote.json()["remote_url"] == "https://github.com/example/site.git"
    assert "github.com/example/site.git" in run_git(["config", "--get", "remote.origin.url"], folder).stdout


def test_clone_refuses_a_url_with_credentials(client):
    response = client.post(
        "/api/v1/environments/1/projects", json={"name": "Clone", "repository_url": "https://x:y@github.com/a/b"}
    )
    assert response.status_code == 409
    assert client.get("/api/v1/environments/1/projects").json() == []


def test_periodic_fetch_brings_in_changes_made_elsewhere(client, tmp_path, monkeypatch):
    allow_local_fixture_remote(monkeypatch)
    from app.git.periodic import fetch_all
    from app.git.service import repository_status

    project = client.post("/api/v1/environments/1/projects", json={"name": "Shared"}).json()
    folder = Path(tmp_path) / "workspace" / project["folder"]
    remote = tmp_path / "shared.git"
    git("init", "--bare", "-b", "main", str(remote), cwd=tmp_path)
    git("remote", "add", "origin", str(remote), cwd=folder)
    (folder / "readme.md").write_text("one\n")
    commit_all(folder, "First")
    git("push", "-u", "origin", "HEAD:main", cwd=folder)
    git("branch", "--set-upstream-to=origin/main", cwd=folder)

    elsewhere = tmp_path / "elsewhere"
    git("clone", str(remote), str(elsewhere), cwd=tmp_path)
    (elsewhere / "readme.md").write_text("two\n")
    commit_all(elsewhere, "Second")
    git("push", cwd=elsewhere)
    assert repository_status(folder).behind == 0

    state = client.app.state
    assert fetch_all(state.session_factory, state.cipher, state.blueprint_dir, state.workspace_dir) == 1
    assert repository_status(folder).behind == 1
    assert (folder / "readme.md").read_text() == "one\n"


def test_log_lists_commits_per_path_and_shows_one_commit(tmp_path):
    from app.git.service import commit_diff, repository_log

    folder = tmp_path / "repo"
    git("init", "-b", "main", str(folder), cwd=tmp_path)
    assert repository_log(folder) == []
    (folder / "a.txt").write_text("one\n")
    commit_all(folder, "Add a")
    (folder / "b.txt").write_text("bee\n")
    commit_all(folder, "Add b")
    (folder / "a.txt").write_text("two\n")
    commit_all(folder, "Change a")

    assert [entry.subject for entry in repository_log(folder)] == ["Change a", "Add b", "Add a"]
    history = repository_log(folder, "a.txt")
    assert [entry.subject for entry in history] == ["Change a", "Add a"]
    assert history[0].author == "YAAP" and len(history[0].short_hash) >= 7
    diff = commit_diff(folder, history[0].hash, "a.txt")
    assert "-one" in diff and "+two" in diff
    with pytest.raises(GitError):
        commit_diff(folder, "HEAD; rm -rf /")


def test_git_api_log_and_commit(client):
    client.put("/api/v1/blueprint/file", params={"path": "instructions.md"}, json={"content": "Be kind.\n"})
    log = client.get("/api/v1/environments/1/git/repositories/blueprint-global/log", params={"path": "instructions.md"})
    assert log.status_code == 200
    entries = log.json()
    assert entries[0]["subject"].startswith("Update instructions.md")
    shown = client.get(f"/api/v1/environments/1/git/repositories/blueprint-global/commits/{entries[0]['hash']}")
    assert "+Be kind." in shown.json()["diff"]
