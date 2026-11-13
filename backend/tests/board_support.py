"""A project with a git repository and its board switched on, for the tests of the board's routes."""

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient


def host_git(*arguments: str, cwd: Path) -> str:
    """Plain git as a user would run it."""
    completed = subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout


def board_project(client: TestClient, name: str = "Site", environment_id: int = 1) -> dict:
    """A project whose folder is a git repository with one commit and whose board is started on a branch."""
    project = client.post(f"/api/v1/environments/{environment_id}/projects", json={"name": name}).json()
    folder = Path(client.app.state.workspace_dir) / project["folder"]
    folder.mkdir(parents=True, exist_ok=True)
    host_git("init", "-b", "main", cwd=folder)
    (folder / "README.md").write_text("# Site\n", encoding="utf-8")
    host_git("add", ".", cwd=folder)
    host_git("commit", "-m", "Start", cwd=folder)
    switched = client.put(f"/api/v1/projects/{project['id']}/settings/project_board", json={"value": True})
    assert switched.status_code == 200, switched.text
    started = client.post(f"/api/v1/projects/{project['id']}/board", json={})
    assert started.status_code == 201, started.text
    return project


def add_card(client: TestClient, project_id: int, title: str = "A card", column: str = "idea", **fields: object) -> dict:
    body = {"title": title, "column": column, **fields}
    created = client.post(f"/api/v1/projects/{project_id}/board/cards", json=body)
    assert created.status_code == 201, created.text
    return created.json()
