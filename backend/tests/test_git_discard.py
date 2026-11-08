import subprocess
from pathlib import Path

import pytest

from app.git.discard import DiscardPlan, discard_changes, plan_discard
from app.git.service import FileChange, GitError, commit_all, repository_status


def git(*arguments: str, cwd: Path) -> str:
    return subprocess.run(["git", *arguments], cwd=cwd, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """A repository with a commit, then: a.txt modified, b.txt staged, new.txt untracked, an ignored file, and a file
    next to the repository."""
    folder = tmp_path / "repo"
    git("init", "-b", "main", str(folder), cwd=tmp_path)
    (folder / ".gitignore").write_text("*.log\n")
    (folder / "a.txt").write_text("one\n")
    (folder / "b.txt").write_text("bee\n")
    commit_all(folder, "First")
    (folder / "a.txt").write_text("two\n")
    (folder / "b.txt").write_text("staged bee\n")
    git("add", "b.txt", cwd=folder)
    (folder / "new.txt").write_text("brand new\n")
    (folder / "debug.log").write_text("ignored\n")
    (tmp_path / "outside.txt").write_text("not in the repository\n")
    return folder


def changed(folder: Path) -> set[str]:
    return {change.path for change in repository_status(folder).changes}


# name, path, the changed files left, the error
CASES = [
    ("all", None, set(), None),
    ("one tracked file", "a.txt", {"b.txt", "new.txt"}, None),
    ("one untracked file", "new.txt", {"a.txt", "b.txt"}, None),
    ("a staged change", "b.txt", {"a.txt", "new.txt"}, None),
    ("a path not in the status", ".gitignore", {"a.txt", "b.txt", "new.txt"}, "not one of the changed files"),
    ("a path outside the repository", "../outside.txt", {"a.txt", "b.txt", "new.txt"}, "not one of the changed"),
]


@pytest.mark.parametrize(("name", "path", "left", "error"), CASES, ids=[case[0] for case in CASES])
def test_discard_restores_tracked_files_and_removes_untracked_ones(repository, name, path, left, error):
    if error:
        with pytest.raises(GitError, match=error):
            discard_changes(repository, path)
    else:
        discard_changes(repository, path)
    assert changed(repository) == left
    assert (repository / "debug.log").read_text() == "ignored\n"
    assert (repository.parent / "outside.txt").exists()
    if "a.txt" not in left:
        assert (repository / "a.txt").read_text() == "one\n"
    if "b.txt" not in left:
        assert (repository / "b.txt").read_text() == "bee\n"
        assert git("diff", "--cached", "--name-only", cwd=repository) == ""
    assert (repository / "new.txt").exists() == ("new.txt" in left)


def test_discarding_a_staged_rename_brings_back_the_old_name(repository):
    git("mv", "a.txt", "renamed.txt", cwd=repository)
    discard_changes(repository, "renamed.txt")
    assert not (repository / "renamed.txt").exists()
    assert (repository / "a.txt").read_text() == "one\n"


def test_discard_works_in_a_repository_without_commits(tmp_path):
    folder = tmp_path / "repo"
    git("init", "-b", "main", str(folder), cwd=tmp_path)
    (folder / "staged.txt").write_text("staged\n")
    git("add", "staged.txt", cwd=folder)
    (folder / "loose.txt").write_text("loose\n")
    discard_changes(folder, "staged.txt")
    assert changed(folder) == {"loose.txt"} and not (folder / "staged.txt").exists()
    (folder / "staged.txt").write_text("staged\n")
    git("add", "staged.txt", cwd=folder)
    discard_changes(folder, None)
    assert changed(folder) == set() and list(folder.iterdir()) == [folder / ".git"]


def test_nothing_is_discarded_when_the_checkpoint_fails(repository):
    summaries: list[str] = []

    def failing(summary: str) -> int | None:
        summaries.append(summary)
        return None

    with pytest.raises(GitError, match="No checkpoint"):
        discard_changes(repository, "a.txt", failing)
    assert summaries == ["Before discarding the changes to a.txt"]
    assert (repository / "a.txt").read_text() == "two\n"
    assert discard_changes(repository, None, lambda summary: 7) == 7
    assert changed(repository) == set()


def test_a_path_is_taken_literally_not_as_a_pattern(repository):
    # As a pattern, [ab].txt would match a.txt and b.txt.
    (repository / "[ab].txt").write_text("brackets\n")
    discard_changes(repository, "[ab].txt")
    assert not (repository / "[ab].txt").exists()
    assert changed(repository) == {"a.txt", "b.txt", "new.txt"}


MODIFIED = FileChange("a.txt", "modified", False)
UNTRACKED = FileChange("new.txt", "untracked", False)
RENAMED = FileChange("b2.txt", "renamed", True, "b.txt")
DELETED_AND_UNTRACKED = [FileChange("c.txt", "deleted", True), FileChange("c.txt", "untracked", False)]

# name, changes, path, plan or the error
PLANS = [
    ("all", [MODIFIED, UNTRACKED], None, DiscardPlan(("a.txt",), ("new.txt",), True)),
    ("one tracked", [MODIFIED, UNTRACKED], "a.txt", DiscardPlan(("a.txt",), (), False)),
    ("one untracked", [MODIFIED, UNTRACKED], "new.txt", DiscardPlan((), ("new.txt",), False)),
    ("a rename takes both names", [RENAMED], "b2.txt", DiscardPlan(("b2.txt", "b.txt"), (), False)),
    ("a path listed twice", DELETED_AND_UNTRACKED, "c.txt", DiscardPlan(("c.txt",), ("c.txt",), False)),
    ("the old name of a rename", [RENAMED], "b.txt", "not one of the changed files"),
    ("a path outside", [MODIFIED], "../a.txt", "not one of the changed files"),
    ("an absolute path", [MODIFIED], "/etc/passwd", "not one of the changed files"),
    ("nothing changed", [], None, "no changes to discard"),
]


@pytest.mark.parametrize(("name", "changes", "path", "expected"), PLANS, ids=[plan[0] for plan in PLANS])
def test_plan_discard_only_takes_changed_files(name, changes, path, expected):
    if isinstance(expected, str):
        with pytest.raises(GitError, match=expected):
            plan_discard(changes, path)
    else:
        assert plan_discard(changes, path) == expected


def project_with_changes(client, tmp_path: Path) -> tuple[dict, Path, str]:
    project = client.post("/api/v1/environments/1/projects", json={"name": "Site"}).json()
    folder = Path(tmp_path) / "workspace" / project["folder"]
    (folder / "index.html").write_text("<h1>Hi</h1>\n")
    commit_all(folder, "Add page")
    (folder / "index.html").write_text("<h1>Changed</h1>\n")
    (folder / "draft.html").write_text("draft\n")
    return project, folder, f"/api/v1/environments/1/git/repositories/project-{project['id']}/discard"


def test_git_api_discard_takes_a_checkpoint_first(client, tmp_path):
    project, folder, url = project_with_changes(client, tmp_path)
    assert client.post(url, json={"path": "../index.html"}).status_code == 409

    one = client.post(url, json={"path": "draft.html"})
    assert one.status_code == 200
    assert [change["path"] for change in one.json()["repository"]["changes"]] == ["index.html"]
    assert not (folder / "draft.html").exists()

    everything = client.post(url, json={}).json()
    assert everything["repository"]["changes"] == []
    assert (folder / "index.html").read_text() == "<h1>Hi</h1>\n"
    checkpoints = client.get(f"/api/v1/projects/{project['id']}/checkpoints").json()
    assert [checkpoint["kind"] for checkpoint in checkpoints] == ["discard", "discard"]
    assert checkpoints[0]["id"] == everything["checkpoint_id"]
    assert checkpoints[0]["summary"] == "Before discarding all uncommitted changes"

    restored = client.post(f"/api/v1/projects/{project['id']}/checkpoints/{everything['checkpoint_id']}/restore")
    assert restored.status_code == 200
    assert (folder / "index.html").read_text() == "<h1>Changed</h1>\n"


def test_git_api_discard_waits_for_a_running_reply_and_skips_the_blueprint(client, tmp_path, monkeypatch):
    project, folder, url = project_with_changes(client, tmp_path)
    conversation = client.post(
        "/api/v1/environments/1/conversations",
        json={"model": "claude-opus-5-5", "effort": "medium", "project_id": project["id"]},
    ).json()
    replies = client.app.state.replies
    monkeypatch.setattr(replies, "running_in", lambda environment_id: [conversation["id"]])
    refused = client.post(url, json={})
    assert refused.status_code == 409 and "reply is running" in refused.json()["detail"]
    assert (folder / "draft.html").exists()

    monkeypatch.setattr(replies, "running_in", lambda environment_id: [])
    blueprint = client.post("/api/v1/environments/1/git/repositories/blueprint-global/discard", json={})
    assert blueprint.status_code == 409 and "project repository" in blueprint.json()["detail"]
