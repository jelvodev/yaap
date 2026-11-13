"""The scope of a build's review: the diff base_commit..branch in the project repository."""

import subprocess
from pathlib import Path

import anyio
import pytest

from app.core.reply_services import ReplyServices
from app.providers.fake_provider import ScriptedResponse
from app.reviews.scope import BuildTarget, NothingToReview, branch_scope
from app.reviews.service import ReviewService
from app.storage.models import Project
from tests.test_chat import add_key
from tests.test_reviews import REPORT, Backend, two_vendor_registry


def git(folder: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *arguments],
        cwd=folder, capture_output=True, text=True, check=True,
    ).stdout.strip()


def project_with_branch(client) -> tuple[Project, Path, str]:
    """A project whose repository has a base commit and a branch `build/cards` one commit ahead of it."""
    add_key(client)
    created = client.post("/api/v1/environments/1/projects", json={"name": "Shop"}).json()
    folder = Path(client.app.state.workspace_dir) / created["folder"]
    (folder / "app.py").write_text("print('one')\n", encoding="utf-8")
    git(folder, "add", "-A")
    git(folder, "commit", "-m", "Base")
    base = git(folder, "rev-parse", "HEAD")
    git(folder, "branch", "build/cards")
    git(folder, "checkout", "-q", "build/cards")
    (folder / "app.py").write_text("print('two')\n", encoding="utf-8")
    (folder / "extra.py").write_text("x = 1\n", encoding="utf-8")
    git(folder, "add", "-A")
    git(folder, "commit", "-m", "Build")
    git(folder, "checkout", "-q", "-")
    with client.app.state.session_factory() as session:
        project = session.get_one(Project, created["id"])
        session.expunge(project)
    return project, folder, base


def scope_of(client, project: Project, target: BuildTarget):
    workspace = Path(client.app.state.workspace_dir)
    with client.app.state.session_factory() as session:
        return branch_scope(session, workspace, project, target)


def test_the_scope_is_the_diff_of_the_branch_since_its_base_commit(client):
    project, _, base = project_with_branch(client)

    scope = scope_of(client, project, BuildTarget(base, "build/cards", "Add the cards", None, 21))

    assert scope is not None
    assert (scope.source, scope.base_commit, scope.branch, scope.card_id) == ("build", base, "build/cards", 21)
    assert (scope.project_id, scope.objective) == (project.id, "Add the cards")
    assert "-print('one')" in scope.diff and "+print('two')" in scope.diff and "+++ b/extra.py" in scope.diff


def test_the_project_folder_is_not_changed_by_reading_the_branch(client):
    project, folder, base = project_with_branch(client)
    before = git(folder, "status", "--porcelain"), git(folder, "rev-parse", "HEAD"), (folder / "app.py").read_text()

    scope_of(client, project, BuildTarget(base, "build/cards"))

    assert (git(folder, "status", "--porcelain"), git(folder, "rev-parse", "HEAD"), (folder / "app.py").read_text()) == before


def test_a_branch_that_changed_nothing_has_no_scope(client):
    project, folder, base = project_with_branch(client)
    git(folder, "branch", "build/empty", base)

    assert scope_of(client, project, BuildTarget(base, "build/empty")) is None


def test_the_conversation_of_the_build_names_the_preset_and_the_writing_vendor(client):
    project, _, base = project_with_branch(client)
    conversation_id = client.post(
        "/api/v1/environments/1/conversations",
        json={"model": "claude-opus-5-5", "effort": "medium", "project_id": project.id, "preset": "Balanced"},
    ).json()["id"]

    scope = scope_of(client, project, BuildTarget(base, "build/cards", None, conversation_id))

    assert scope is not None and scope.conversation_id == conversation_id
    assert scope.preset == "Balanced" and scope.writer_vendors


@pytest.mark.parametrize(
    "branch",
    ["build/missing", "--output=x", "", "has space", "bad..name"],
    ids=["unknown_branch", "option", "empty", "space", "dotdot"],
)
def test_a_branch_that_cannot_be_read_is_nothing_to_review(client, branch):
    project, _, base = project_with_branch(client)

    with pytest.raises(NothingToReview):
        scope_of(client, project, BuildTarget(base, branch))


@pytest.mark.parametrize(
    "commit",
    ["--output=x", "HEAD", "build/cards", "abc", "0" * 40, "xyz1234"],
    ids=["option", "name", "branch", "too_short", "unknown_hash", "not_hex"],
)
def test_a_base_that_is_not_a_known_commit_hash_is_nothing_to_review(client, commit):
    project, _, _ = project_with_branch(client)

    with pytest.raises(NothingToReview):
        scope_of(client, project, BuildTarget(commit, "build/cards"))


def test_a_very_large_diff_is_cut_with_a_note(client):
    project, folder, base = project_with_branch(client)
    git(folder, "checkout", "-q", "build/cards")
    (folder / "big.txt").write_text("".join(f"line {n} of a large file\n" for n in range(60000)), encoding="utf-8")
    git(folder, "add", "-A")
    git(folder, "commit", "-m", "Big")
    git(folder, "checkout", "-q", "-")

    scope = scope_of(client, project, BuildTarget(base, "build/cards"))

    assert scope is not None and scope.diff.endswith("[The diff is longer than this; the rest is not shown.]")


def test_a_card_review_with_only_the_writers_vendor_is_done_by_that_vendor_and_marked_so(client, fake):
    other = Backend()
    client.app.state.providers = two_vendor_registry(fake, other)
    project, _, base = project_with_branch(client)
    conversation_id = client.post(
        "/api/v1/environments/1/conversations",
        json={"model": "claude-opus-5-5", "effort": "medium", "project_id": project.id},
    ).json()["id"]
    fake.responses.append(ScriptedResponse(text=REPORT))
    service = ReviewService(ReplyServices.from_state(client.app.state))
    with client.app.state.session_factory() as session:
        scope = branch_scope(
            session, Path(client.app.state.workspace_dir), project, BuildTarget(base, "build/cards", None, conversation_id)
        )
        assert scope is not None and scope.writer_vendors == ("anthropic",)
        review_id = service.create(session, scope)

    anyio.run(service.run, review_id)

    review = client.get(f"/api/v1/projects/{project.id}/reviews/{review_id}").json()
    assert (review["status"], review["same_vendor"], review["reviewer_provider"]) == ("done", True, "anthropic")
    assert review["reviewer_model"] != "claude-opus-5-5" and other.requests == []
