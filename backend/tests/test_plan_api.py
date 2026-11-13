"""The plans API: read a plan, edit and discard a draft, and the refusals of approve, continue and stop."""

import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.plans.service import PlanService
from tests.test_commands_and_skills import start

STEP = {"title": "Add the route", "details": "Return ok.", "files": ["app.py"], "checks": [{"text": "ok", "run": "true"}]}


def edit_body(**changes: Any) -> dict[str, Any]:
    return {"title": "Health endpoint", "summary": "Add /health.", "risks": ["Clash"], "steps": [STEP], **changes}


def make_plan(client, title: str = "Health endpoint") -> tuple[dict, int]:
    """A draft plan of a new project, stored as submit_plan stores it."""
    project = client.post("/api/v1/environments/1/projects", json={"name": "Site"}).json()
    conversation_id = start(client, project_id=project["id"])
    view = PlanService(client.app.state.session_factory).submit(
        project["id"], conversation_id, {"title": title, "steps": [STEP]}
    )
    return project, view.id


def force_remove(function: Any, path: str, _error: Any) -> None:
    """Git marks its object files read only, which Windows will not delete."""
    Path(path).chmod(stat.S_IWRITE)
    function(path)


def host_git(*arguments: str, cwd: Path) -> None:
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *arguments],
        cwd=cwd,
        capture_output=True,
        check=True,
    )


def test_a_plan_is_read_with_its_steps_checks_and_warnings(client):
    project, plan_id = make_plan(client)
    body = client.get(f"/api/v1/plans/{plan_id}").json()
    assert body["status"] == "draft" and body["project_id"] == project["id"]
    assert body["steps"][0]["checks"] == [{"text": "ok", "run": "true"}]
    assert body["steps"][0]["status"] == "open" and body["warnings"] == [] and body["build"] is None


@pytest.mark.parametrize(
    ("method", "path"),
    [("get", ""), ("patch", ""), ("post", "/discard"), ("post", "/approve"), ("post", "/continue"), ("post", "/stop")],
)
def test_an_unknown_plan_is_a_404(client, method, path):
    kwargs = {"json": edit_body()} if method == "patch" else {}
    assert getattr(client, method)(f"/api/v1/plans/999{path}", **kwargs).status_code == 404


def test_editing_a_draft_replaces_its_title_risks_and_steps(client):
    _, plan_id = make_plan(client)
    steps = [STEP, {"title": "Test it", "checks": [{"text": "green", "run": "pytest -q"}]}]
    response = client.patch(f"/api/v1/plans/{plan_id}", json=edit_body(title="Renamed", risks=[], steps=steps))
    assert response.status_code == 200
    assert [step["title"] for step in response.json()["steps"]] == ["Add the route", "Test it"]
    stored = client.get(f"/api/v1/plans/{plan_id}").json()
    assert stored["title"] == "Renamed" and stored["risks"] == []


def test_an_invalid_edit_is_a_422_listing_every_error(client):
    _, plan_id = make_plan(client)
    response = client.patch(
        f"/api/v1/plans/{plan_id}", json=edit_body(title="", steps=[{"title": "A", "checks": []}, {"title": "B"}])
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_plan"
    assert any("Step 1 has no checks" in error for error in detail["errors"])
    assert any("Step 2 has no checks" in error for error in detail["errors"])
    assert client.get(f"/api/v1/plans/{plan_id}").json()["title"] == "Health endpoint"


def test_discarding_cancels_the_draft_and_freezes_it(client):
    _, plan_id = make_plan(client)
    discarded = client.post(f"/api/v1/plans/{plan_id}/discard")
    assert discarded.status_code == 200 and discarded.json()["status"] == "cancelled"
    assert client.post(f"/api/v1/plans/{plan_id}/discard").status_code == 409
    assert client.patch(f"/api/v1/plans/{plan_id}", json=edit_body()).status_code == 409


def test_approving_without_a_repository_says_how_to_make_one(client, tmp_path):
    project, plan_id = make_plan(client)
    shutil.rmtree(Path(tmp_path) / "workspace" / project["folder"] / ".git", onerror=force_remove)
    response = client.post(f"/api/v1/plans/{plan_id}/approve")
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "no_repository" and "Branches panel" in detail["message"]
    assert client.get(f"/api/v1/plans/{plan_id}").json()["status"] == "draft"


def test_approving_in_a_repository_without_a_commit_is_a_409_copy_failed(client):
    _, plan_id = make_plan(client)
    response = client.post(f"/api/v1/plans/{plan_id}/approve")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "copy_failed"
    assert client.get(f"/api/v1/plans/{plan_id}").json()["status"] == "draft"


def test_approving_a_plan_that_is_not_a_draft_is_a_409_with_its_code(client):
    _, plan_id = make_plan(client)
    client.post(f"/api/v1/plans/{plan_id}/discard")
    response = client.post(f"/api/v1/plans/{plan_id}/approve")
    assert response.status_code == 409 and response.json()["detail"]["code"] == "plan_not_draft"


def test_continue_of_a_draft_is_a_409_not_resumable(client):
    _, plan_id = make_plan(client)
    response = client.post(f"/api/v1/plans/{plan_id}/continue")
    assert response.status_code == 409 and response.json()["detail"]["code"] == "not_resumable"


def test_approving_a_draft_in_a_repository_makes_the_copy_and_starts_the_build(client, tmp_path, monkeypatch):
    project, plan_id = make_plan(client)
    folder = Path(tmp_path) / "workspace" / project["folder"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "app.py").write_text("print('hi')\n")
    host_git("init", "-b", "main", cwd=folder)
    host_git("add", "-A", cwd=folder)
    host_git("commit", "-m", "First", cwd=folder)
    # The steps themselves are the runner's tests; here the build only has to be made ready.
    monkeypatch.setattr(client.app.state.builds, "launch", lambda plan_id: None)

    response = client.post(f"/api/v1/plans/{plan_id}/approve")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "building" and body["base_branch"] == "main"
    assert body["build_branch"].startswith(f"yaap/plan-{plan_id}-")
    assert (folder / body["build_folder"] / "app.py").read_text() == "print('hi')\n"
    assert body["build"]["uncommitted"] == 0 and body["build"]["delivered"] is False
    assert client.post(f"/api/v1/plans/{plan_id}/approve").status_code == 409
