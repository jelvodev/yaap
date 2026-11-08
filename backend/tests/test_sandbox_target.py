"""A project's sandbox as a target: YAAP asks the real helper (in process, on a fake Docker API) to
start it and connects through the helper's relay to the in-process SSH server."""

import asyncio
from dataclasses import replace

import httpx
import pytest

import app.sandbox.service as sandbox_service
from app.agent.policy import ToolPolicy
from app.agent.tools import tools_for
from app.sandbox.client import HelperClient
from app.sandbox.service import SANDBOXES, find_sandbox, public_key
from app.storage.models import Conversation, Target
from app.targets.service import chosen_target, describe_target, run_target
from tests.sandbox_fakes import FakeDocker
from tests.test_ssh_targets import SshTestServer, run_steps
from tests.test_tool_guard import start_run
from yaap_sandbox.app import create_app as create_helper
from yaap_sandbox.docker_api import DockerApi
from yaap_sandbox.relay import relay_handler
from yaap_sandbox.sandboxes import Config, Sandboxes

TOKEN = "sandbox-test-token"


class SandboxWorld:
    """The helper with a fake Docker, its relay in the SSH server's thread, and YAAP's client for both."""

    def __init__(self, client, server: SshTestServer, tmp_path) -> None:
        self.client = client
        self.server = server
        self.docker = FakeDocker()
        self.docker.add_image()
        config = Config(workspace_dir=tmp_path / "workspace", workspace_host="/host/workspace", instance="yaap")
        self.sandboxes = Sandboxes(DockerApi("http://docker", transport=self.docker.transport()), config)
        real_address = self.sandboxes.address

        async def address(key: str) -> tuple[str, int]:
            # The labels and the running state are checked as in Docker; the sshd is the test server.
            await real_address(key)
            return "127.0.0.1", server.port

        async def start_relay() -> asyncio.Server:
            return await asyncio.start_server(relay_handler(TOKEN, address), "127.0.0.1", 0)

        self.relay = asyncio.run_coroutine_threadsafe(start_relay(), server.loop).result(10)
        relay_port = self.relay.sockets[0].getsockname()[1]
        self.helper = HelperClient(
            "http://helper",
            lambda: TOKEN,
            transport=httpx.ASGITransport(app=create_helper(self.sandboxes, TOKEN)),
            relay=("127.0.0.1", relay_port),
        )
        SANDBOXES.helper = self.helper

    def project(self, name: str = "Site", network: bool = False) -> dict:
        project = self.client.post("/api/v1/environments/1/projects", json={"name": name}).json()
        chosen = self.client.put(f"/api/v1/projects/{project['id']}/sandbox", json={"network": network})
        assert chosen.status_code == 200, chosen.text
        with self.client.app.state.session_factory() as session:
            target = find_sandbox(session, project["id"])
            assert target is not None
            self.server.authorized.add(public_key(session, target))
        return project

    def conversation(self, project: dict) -> int:
        return self.client.post(
            "/api/v1/environments/1/conversations",
            json={"model": "claude-opus-5-5", "effort": "medium", "project_id": project["id"]},
        ).json()["id"]

    def run(self, tmp_path, conversation_id: int, **values):
        with self.client.app.state.session_factory() as session:
            conversation = session.get(Conversation, conversation_id)
            target, folder = chosen_target(session, conversation)
            on_target = run_target(session, self.client.app.state.cipher, target, folder, conversation)
            description = describe_target(on_target.target, on_target.root)
        registry, run = start_run(tmp_path, target=on_target.runtime, target_root=on_target.root, **values)
        run.config = replace(run.config, tool_names=tools_for(True, False))
        return registry, run, description

    def close(self) -> None:
        self.server.loop.call_soon_threadsafe(self.relay.close)
        SANDBOXES.helper = None


@pytest.fixture
def world(client, tmp_path, monkeypatch):
    root = tmp_path / "remote"
    (root / "workspace").mkdir(parents=True)
    (root / "workspace" / "Makefile").write_text("test:\n\techo ok\n", encoding="utf-8")
    server = SshTestServer(root)
    # The host key YAAP makes for a new sandbox is the one the test server has.
    host_private = server.host_key.export_private_key().decode()
    monkeypatch.setattr(sandbox_service, "new_host_key", lambda: (host_private, server.host_key_line))
    found = SandboxWorld(client, server, tmp_path)
    yield found
    found.close()
    server.close()


def test_a_project_chooses_its_sandbox(world, client):
    project = world.project()
    setting = next(item for item in client.get(f"/api/v1/projects/{project['id']}/settings").json() if item["key"] == "target")
    assert setting["value"] == {"sandbox": True, "network": False}
    # The sandbox is not one of the computers in Settings › Targets, and no conversation outside a project gets it.
    assert client.get("/api/v1/environments/1/targets").json() == []
    with client.app.state.session_factory() as session:
        target = find_sandbox(session, project["id"])
        assert target.kind == "sandbox" and target.host_key is None
        sandbox_id = target.id
    outside = client.post(
        "/api/v1/environments/1/conversations",
        json={"model": "claude-opus-5-5", "effort": "medium", "target_id": sandbox_id},
    )
    assert outside.status_code == 422
    assert client.post(f"/api/v1/targets/{sandbox_id}/test").status_code == 404

    shown = client.get(f"/api/v1/conversations/{world.conversation(project)}").json()["target"]
    assert shown["name"] == "Sandbox" and shown["folder"] == "/workspace"
    state = client.get(f"/api/v1/projects/{project['id']}/sandbox").json()
    assert state["available"] and state["chosen"] and state["state"] == "missing" and state["network"] is False


def test_a_sandbox_target_runs_a_command_in_the_project_folder_without_network(world, client, tmp_path):
    project = world.project()
    registry, run, description = world.run(tmp_path, world.conversation(project), policy=ToolPolicy())
    assert "no network access" in description and "/workspace" in description
    # No Docker inside: a web app runs with start_process and its port, shown in the preview.
    assert "no dockerd and no docker compose" in description and "start_process and its port" in description

    results, seen = run_steps(
        registry,
        run,
        [("run_command", {"command": "make test"}), ("write_file", {"path": "notes.txt", "content": "hi\n"})],
    )
    assert "ran the script" in results[0][0], results
    assert not results[1][1], results
    # The sandbox was made without network, with the project folder as its /workspace.
    created = world.docker.created[0]
    assert created["HostConfig"]["NetworkMode"] == "yaap-sandbox-offline"
    assert created["HostConfig"]["Mounts"][0] == {
        "Type": "bind",
        "Source": f"/host/workspace/{project['folder']}",
        "Target": "/workspace",
    }
    assert world.docker.containers["yaap-sandbox-p" + str(project["id"])]["State"]["Running"]
    script = world.server.scripts[-1]
    assert "cd '/workspace' || exit 1" in script and "make test" in script
    assert (world.server.root / "workspace" / "notes.txt").read_text(encoding="utf-8") == "hi\n"
    # Changes follow the project's permissions as in the container: the command asks, the write does not, and
    # nothing says it cannot be rolled back.
    assert [event["tool"] for event in seen] == ["run_command"]
    assert "cannot roll back" not in seen[0]["summary"]
    with client.app.state.session_factory() as session:
        target = find_sandbox(session, project["id"])
        # YAAP made the host key, so it is pinned without asking; the use is recorded for the idle stop.
        assert target.host_key == world.server.host_key_line and target.pending_host_key is None
        assert target.last_used_at is not None


def test_switching_the_network_on_makes_the_sandbox_again(world, client, tmp_path):
    project = world.project()
    conversation_id = world.conversation(project)
    registry, run, _ = world.run(tmp_path, conversation_id, policy=ToolPolicy(access="full"))
    run_steps(registry, run, [("run_command", {"command": "true"})])
    assert client.put(f"/api/v1/projects/{project['id']}/sandbox", json={"network": True}).status_code == 200

    registry, run, description = world.run(tmp_path, conversation_id, policy=ToolPolicy(access="full"))
    assert "has network access" in description
    run_steps(registry, run, [("run_command", {"command": "true"})])
    assert [body["HostConfig"]["NetworkMode"] for body in world.docker.created] == [
        "yaap-sandbox-offline",
        "yaap-sandbox-online",
    ]


def test_a_container_yaap_holds_no_pin_for_is_made_again(world, client, tmp_path):
    project = world.project()
    conversation_id = world.conversation(project)
    registry, run, _ = world.run(tmp_path, conversation_id, policy=ToolPolicy(access="full"))
    run_steps(registry, run, [("run_command", {"command": "true"})])
    with client.app.state.session_factory() as session:
        target = find_sandbox(session, project["id"])
        target.host_key = None
        session.commit()
    registry, run, _ = world.run(tmp_path, conversation_id, policy=ToolPolicy(access="full"))
    run_steps(registry, run, [("run_command", {"command": "true"})])
    assert len(world.docker.created) == 2
    with client.app.state.session_factory() as session:
        assert session.get(Target, target.id).host_key == world.server.host_key_line


def test_without_the_helper_the_sandbox_is_not_offered(client):
    SANDBOXES.helper = None
    project = client.post("/api/v1/environments/1/projects", json={"name": "Site"}).json()
    refused = client.put(f"/api/v1/projects/{project['id']}/sandbox", json={"network": False})
    assert refused.status_code == 409 and "helper is not running" in refused.json()["detail"]
    state = client.get(f"/api/v1/projects/{project['id']}/sandbox").json()
    assert state["available"] is False and "helper is not running" in state["detail"]


def test_the_sandbox_setting_has_one_shape(client):
    project = client.post("/api/v1/environments/1/projects", json={"name": "Site"}).json()
    url = f"/api/v1/projects/{project['id']}/settings/target"
    assert client.put(url, json={"value": {"sandbox": True, "network": "yes"}}).status_code == 422
    assert client.put(url, json={"value": {"sandbox": True, "id": 3}}).status_code == 422
    assert client.put(url, json={"value": {"sandbox": True, "network": True}}).status_code == 200
