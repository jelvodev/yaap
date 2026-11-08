"""The project folder on the user's computer (plan chat-interface 1.1): Docker's forms of the data folder's host path,
the helper's answer, and the line in the project part of the system prompt."""

import asyncio
from pathlib import Path

import httpx
import pytest

from app.blueprint.context import project_part
from app.providers.fake_provider import ScriptedResponse
from app.sandbox.client import HelperClient
from app.sandbox.host_path import discover_host_data, host_path, project_host_folder
from app.storage.models import Project
from tests.sandbox_fakes import FakeDocker
from yaap_sandbox.app import create_app as create_helper
from yaap_sandbox.docker_api import DockerApi
from yaap_sandbox.sandboxes import Config, Sandboxes

TOKEN = "host-path-token"
COMPOSE = {"com.docker.compose.project": "yaap"}


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # Windows forms stay Windows, with backslashes.
        ("C:\\YAAP\\data", "C:\\YAAP\\data"),
        ("c:/YAAP/data/", "C:\\YAAP\\data"),
        ("D:\\", "D:\\"),
        ("\\\\server\\share\\yaap\\data", "\\\\server\\share\\yaap\\data"),
        # Docker Desktop's and WSL's Linux names for a Windows drive.
        ("/run/desktop/mnt/host/c/YAAP/data", "C:\\YAAP\\data"),
        ("/mnt/c/YAAP/data", "C:\\YAAP\\data"),
        ("/host_mnt/c/Users/jelle/yaap/data", "C:\\Users\\jelle\\yaap\\data"),
        ("/mnt/d", "D:\\"),
        # A Linux host's path stays as it is.
        ("/home/x/yaap/data", "/home/x/yaap/data"),
        ("/srv/yaap//data/", "/srv/yaap/data"),
        ("/mnt/data/yaap", "/mnt/data/yaap"),
        # Anything else is left out.
        ("", None),
        ("data", None),
        ("./data", None),
        ("C:data", None),
        ("/run/desktop/mnt/host/wsl/Ubuntu/home/x", None),
        ("/mnt/wsl/docker-desktop-bind-mounts/x", None),
        ("/var/lib/docker/volumes/yaap_data/_data", None),
        ("/home/x\n/data", None),
    ],
)
def test_docker_host_paths_become_the_users_form(source, expected):
    assert host_path(source) == expected


@pytest.mark.parametrize(
    ("host_data", "expected"),
    [
        ("C:\\YAAP\\data", "C:\\YAAP\\data\\workspace\\private\\testproject-yaap"),
        ("C:\\", "C:\\workspace\\private\\testproject-yaap"),
        ("/home/x/yaap/data", "/home/x/yaap/data/workspace/private/testproject-yaap"),
    ],
)
def test_the_project_folder_joins_with_the_hosts_separator(host_data, expected):
    assert project_host_folder(host_data, "private/testproject-yaap") == expected


def test_the_project_part_names_the_host_folder_only_when_known():
    project = Project(name="Site", slug="site", folder="private/site")
    known = project_part(project, host_folder="C:\\YAAP\\data\\workspace\\private\\site")
    assert "On the user's computer this folder is `C:\\YAAP\\data\\workspace\\private\\site`." in known
    assert "user's computer" not in project_part(project)


def helper_on(docker: FakeDocker, transport: httpx.AsyncBaseTransport | None = None) -> HelperClient:
    sandboxes = Sandboxes(DockerApi("http://docker", transport=docker.transport()), Config(instance="yaap"))
    transport = transport or httpx.ASGITransport(app=create_helper(sandboxes, TOKEN))
    return HelperClient("http://helper", lambda: TOKEN, transport=transport)


def test_yaap_learns_its_data_folder_from_the_helper(monkeypatch, tmp_path):
    monkeypatch.setenv("HOSTNAME", "helper1")
    docker = FakeDocker()
    docker.add_container("helper1", {**COMPOSE, "com.docker.compose.service": "yaap-sandbox"})
    docker.add_container(
        "yaap-yaap-1",
        {**COMPOSE, "com.docker.compose.service": "yaap"},
        mounts=[{"Type": "bind", "Source": "/run/desktop/mnt/host/c/YAAP/data", "Destination": "/data"}],
    )
    assert asyncio.run(discover_host_data(helper_on(docker), tmp_path)) == "C:\\YAAP\\data"

    # A form YAAP does not know is left out rather than guessed.
    docker.containers["yaap-yaap-1"]["Mounts"][0]["Source"] = "/run/desktop/mnt/host/wsl/x"
    assert asyncio.run(discover_host_data(helper_on(docker), tmp_path)) is None


def test_an_absent_or_old_helper_leaves_the_host_folder_unknown(tmp_path):
    old_helper = httpx.MockTransport(lambda request: httpx.Response(404, json={"detail": "Not Found"}))
    assert asyncio.run(discover_host_data(helper_on(FakeDocker(), old_helper), tmp_path, attempts=2, wait=0)) is None
    # Without a helper YAAP runs on the computer itself: its own data folder is the user's path.
    assert asyncio.run(discover_host_data(None, tmp_path)) == host_path(str(tmp_path.resolve()))


def test_a_project_conversation_is_told_its_folder_on_the_users_computer(client, fake):
    client.put("/api/v1/settings/anthropic_connection", json={"value": "claude_code"})
    project = client.post("/api/v1/environments/1/projects", json={"name": "Site"}).json()
    with client.app.state.session_factory() as session:
        folder = session.get_one(Project, project["id"]).folder

    def system_prompt(host_data: str | None) -> str:
        client.app.state.host_data_dir = host_data
        conversation_id = client.post(
            "/api/v1/environments/1/conversations",
            json={"model": "claude-opus-5-5", "effort": "medium", "project_id": project["id"]},
        ).json()["id"]
        fake.responses.append(ScriptedResponse(text="Ok."))
        client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Waar staat dit?"})
        return "\n\n".join(part.text for part in fake.requests[-1].system)

    expected = "C:\\YAAP\\data\\workspace\\" + folder.replace("/", "\\")
    assert f"On the user's computer this folder is `{expected}`." in system_prompt("C:\\YAAP\\data")
    assert "user's computer" not in system_prompt(None)


def test_the_test_app_without_a_helper_knows_its_own_data_folder(client, tmp_path):
    assert client.app.state.host_data_dir == host_path(str(Path(tmp_path).resolve()))
