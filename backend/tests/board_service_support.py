"""Helpers for the board's service and API tests: a project with a started board, driven through the real app."""

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from app.board.service import BoardService
from app.storage.models import Project


def board_service(client: TestClient) -> BoardService:
    service: BoardService = client.app.state.board  # type: ignore[attr-defined]  # Starlette types state as Any
    return service


def board_url(project_id: int, tail: str = "") -> str:
    return f"/api/v1/projects/{project_id}/board{tail}"


def make_project(client: TestClient, name: str = "Site") -> dict:
    response = client.post("/api/v1/environments/1/projects", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def start_board(client: TestClient, name: str = "Site") -> dict:
    """A project whose board is started on its own branch (an empty repository is made for it)."""
    project = make_project(client, name)
    response = client.post(board_url(project["id"]), json={"storage": "branch", "create_repository": True})
    assert response.status_code == 201, response.text
    return project


def project_folder(client: TestClient, project: dict) -> Path:
    service = board_service(client)
    return service.storage.branch.folder(service.project(project["id"]))


def add_card(client: TestClient, project_id: int, title: str = "A card", **fields: object) -> dict:
    response = client.post(board_url(project_id, "/cards"), json={"title": title, **fields})
    assert response.status_code == 201, response.text
    return response.json()


def git(folder: Path, *arguments: str) -> str:
    done = subprocess.run(["git", *arguments], cwd=folder, capture_output=True, text=True, check=True)
    return done.stdout


def switch_board(client: TestClient, project_id: int, on: bool) -> None:
    response = client.put(f"/api/v1/projects/{project_id}/settings/project_board", json={"value": on})
    assert response.status_code == 200, response.text


def set_repository_url(client: TestClient, project_id: int, url: str) -> None:
    """Give the project a GitHub URL, as an imported project has, so issues and checks can be asked for."""
    service = board_service(client)
    with service.session_factory() as session:
        project = session.get(Project, project_id)
        assert project is not None
        project.repository_url = url
        session.commit()
