"""The yaap-sandbox helper, against a fake Docker API: it only touches containers with YAAP's label."""

import asyncio
from pathlib import Path

import httpx
import pytest

from tests.sandbox_fakes import FakeDocker
from yaap_sandbox.app import create_app, ensure_token
from yaap_sandbox.docker_api import DockerApi
from yaap_sandbox.relay import relay_handler
from yaap_sandbox.sandboxes import (
    CONFIG_LABEL,
    INSTANCE_LABEL,
    KEY_LABEL,
    LABEL,
    Config,
    Sandboxes,
    SandboxError,
    StartSpec,
    cpu_percent,
    docker_path,
    memory_used,
)
from app.targets.ssh import new_key_pair

TOKEN = "helper-token"


@pytest.fixture
def docker() -> FakeDocker:
    fake = FakeDocker()
    fake.add_image()
    return fake


@pytest.fixture
def workspace(tmp_path) -> Path:
    (tmp_path / "workspace" / "default" / "site").mkdir(parents=True)
    return tmp_path / "workspace"


def sandboxes_for(docker: FakeDocker, workspace: Path) -> Sandboxes:
    config = Config(workspace_dir=workspace, workspace_host="/srv/yaap/data/workspace", instance="yaap")
    return Sandboxes(DockerApi("http://docker", transport=docker.transport()), config)


def call(sandboxes: Sandboxes, method: str, path: str, token: str = TOKEN, **kwargs) -> httpx.Response:
    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(sandboxes, TOKEN))
        async with httpx.AsyncClient(transport=transport, base_url="http://helper") as client:
            return await client.request(method, path, headers={"Authorization": f"Bearer {token}"}, **kwargs)

    return asyncio.run(go())


def start_body(network: bool = False, folder: str = "default/site") -> dict:
    host_key, _ = new_key_pair("host")
    _, public_key = new_key_pair("yaap")
    return {"folder": folder, "network": network, "authorized_key": public_key, "host_key": host_key}


def test_every_call_needs_the_token(docker, workspace):
    sandboxes = sandboxes_for(docker, workspace)
    assert call(sandboxes, "GET", "/sandboxes", token="wrong").status_code == 401
    assert call(sandboxes, "GET", "/sandboxes").status_code == 200


def test_start_creates_a_labelled_sandbox_without_network_from_the_sandbox_image(docker, workspace):
    sandboxes = sandboxes_for(docker, workspace)
    body = start_body()
    started = call(sandboxes, "POST", "/sandboxes/p1/start", json=body)
    assert started.status_code == 200, started.text
    assert started.json() == {"key": "p1", "state": "running", "created": True}
    created = docker.created[0]
    assert created["Image"] == "yaap-sandbox:local"
    assert created["Labels"][LABEL] == "1" and created["Labels"][KEY_LABEL] == "p1"
    assert created["Labels"][INSTANCE_LABEL] == "yaap"
    host = created["HostConfig"]
    assert host["NetworkMode"] == "yaap-sandbox-offline"
    assert host["Mounts"] == [{"Type": "bind", "Source": "/srv/yaap/data/workspace/default/site", "Target": "/workspace"}]
    assert host["Privileged"] is False and host["PortBindings"] == {}
    assert any(item.startswith("YAAP_AUTHORIZED_KEY=ssh-ed25519 ") for item in created["Env"])
    assert docker.containers["yaap-sandbox-p1"]["State"]["Running"]

    # Started again as it is: nothing is created.
    again = call(sandboxes, "POST", "/sandboxes/p1/start", json={**body, "host_key": start_body()["host_key"]})
    assert again.json()["created"] is False and len(docker.created) == 1

    # The network switched on: the sandbox is made again on the network with a route outside.
    online = call(sandboxes, "POST", "/sandboxes/p1/start", json={**body, "network": True})
    assert online.json()["created"] is True
    assert docker.created[-1]["HostConfig"]["NetworkMode"] == "yaap-sandbox-online"
    assert ("DELETE", "/containers/yaap-sandbox-p1") in docker.changes


def test_the_helper_refuses_containers_without_yaaps_label(docker, workspace):
    sandboxes = sandboxes_for(docker, workspace)
    # Someone else's container with a sandbox's name, and one of another YAAP installation.
    docker.add_container("yaap-sandbox-p1", {"com.example": "x"})
    docker.add_container("yaap-sandbox-p2", {LABEL: "1", KEY_LABEL: "p2", INSTANCE_LABEL: "other"})
    for key in ("p1", "p2"):
        assert call(sandboxes, "POST", f"/sandboxes/{key}/start", json=start_body()).status_code == 403
        assert call(sandboxes, "POST", f"/sandboxes/{key}/stop").status_code == 403
        assert call(sandboxes, "DELETE", f"/sandboxes/{key}").status_code == 403
        assert call(sandboxes, "GET", f"/sandboxes/{key}").status_code == 403
    assert docker.changes == []
    assert set(docker.containers) == {"yaap-sandbox-p1", "yaap-sandbox-p2"}
    # Listing shows only this installation's sandboxes.
    assert call(sandboxes, "GET", "/sandboxes").json() == []


def test_only_an_image_with_the_sandbox_label_is_used(docker, workspace):
    docker.add_image(labelled=False)
    refused = call(sandboxes_for(docker, workspace), "POST", "/sandboxes/p1/start", json=start_body())
    assert refused.status_code == 403 and "not YAAP's sandbox image" in refused.json()["detail"]
    assert docker.created == []


def test_folders_stay_inside_the_workspace(docker, workspace):
    sandboxes = sandboxes_for(docker, workspace)
    for folder in ("../etc", "/etc", "default/../..", "default//site"):
        assert call(sandboxes, "POST", "/sandboxes/p1/start", json=start_body(folder=folder)).status_code == 422, folder
    assert call(sandboxes, "POST", "/sandboxes/p1/start", json=start_body(folder="default/none")).status_code == 404
    assert call(sandboxes, "POST", "/sandboxes/BAD KEY/start", json=start_body()).status_code == 422
    bad_key = {**start_body(), "authorized_key": "ssh-rsa AAAA x; rm -rf /"}
    assert call(sandboxes, "POST", "/sandboxes/p1/start", json=bad_key).status_code == 422
    assert docker.created == []


def test_stop_remove_status_and_list(docker, workspace):
    sandboxes = sandboxes_for(docker, workspace)
    call(sandboxes, "POST", "/sandboxes/p1/start", json=start_body())
    status = call(sandboxes, "GET", "/sandboxes/p1").json()
    assert status["state"] == "running" and status["network"] is False
    assert status["cpu_percent"] == 80.0 and status["memory_bytes"] == 200 * 1024**2
    assert call(sandboxes, "GET", "/sandboxes").json() == [{"key": "p1", "state": "running"}]
    assert call(sandboxes, "POST", "/sandboxes/p1/stop").json()["state"] == "exited"
    assert call(sandboxes, "GET", "/sandboxes/p1").json()["state"] == "exited"
    assert call(sandboxes, "DELETE", "/sandboxes/p1").json()["state"] == "missing"
    assert call(sandboxes, "GET", "/sandboxes/p1").json() == {"key": "p1", "state": "missing"}


def test_a_changed_config_label_means_a_new_container(docker, workspace):
    sandboxes = sandboxes_for(docker, workspace)
    call(sandboxes, "POST", "/sandboxes/p1/start", json=start_body())
    docker.containers["yaap-sandbox-p1"]["Config"]["Labels"][CONFIG_LABEL] = "old"
    assert call(sandboxes, "POST", "/sandboxes/p1/start", json=start_body()).json()["created"] is True


def test_the_helper_learns_its_instance_and_workspace_from_its_own_container(docker, workspace):
    docker.add_container(
        "abc123",
        {"com.docker.compose.project": "yaap"},
        mounts=[{"Destination": "/workspace", "Source": "C:\\YAAP\\data\\workspace"}],
    )
    config = Config(workspace_dir=Path("/workspace"))
    sandboxes = Sandboxes(DockerApi("http://docker", transport=docker.transport()), config)
    asyncio.run(sandboxes.discover("abc123"))
    assert config.instance == "yaap" and config.workspace_host == "C:\\YAAP\\data\\workspace"
    assert docker_path(config.workspace_host) == "/run/desktop/mnt/host/c/YAAP/data/workspace"
    assert docker_path("/srv/data/workspace/") == "/srv/data/workspace"


def test_the_helper_answers_where_yaaps_data_is_on_the_host(docker, workspace, monkeypatch):
    project, service = "com.docker.compose.project", "com.docker.compose.service"
    monkeypatch.setenv("HOSTNAME", "helper1")
    docker.add_container("helper1", {project: "yaap", service: "yaap-sandbox"})
    sandboxes = sandboxes_for(docker, workspace)
    assert call(sandboxes, "GET", "/host/data", token="wrong").status_code == 401
    # No YAAP container in this Compose project: an empty answer.
    assert call(sandboxes, "GET", "/host/data").json() == {"source": ""}

    docker.add_container(
        "other-yaap-1",
        {project: "other", service: "yaap"},
        mounts=[{"Type": "bind", "Source": "/srv/other", "Destination": "/data"}],
    )
    docker.add_container(
        "yaap-yaap-1",
        {project: "yaap", service: "yaap"},
        mounts=[
            {"Type": "volume", "Source": "/var/lib/docker/volumes/control/_data", "Destination": "/run/yaap-sandbox"},
            {"Type": "bind", "Source": "C:\\YAAP\\data", "Destination": "/data"},
        ],
    )
    assert call(sandboxes, "GET", "/host/data").json() == {"source": "C:\\YAAP\\data"}

    # /data as a named volume is no folder on the user's computer.
    docker.containers["yaap-yaap-1"]["Mounts"][1]["Type"] = "volume"
    assert call(sandboxes, "GET", "/host/data").json() == {"source": ""}
    assert docker.changes == []


def test_docker_is_reached_through_the_proxy_or_a_socket_in_development():
    assert str(DockerApi("tcp://docker-proxy:2375").client.base_url) == "http://docker-proxy:2375"
    assert str(DockerApi("unix:///var/run/docker.sock").client.base_url) == "http://docker"


def test_cpu_and_memory_follow_docker_stats():
    stats = FakeDocker().stats()
    assert cpu_percent(stats) == 80.0
    assert memory_used(stats) == (200 * 1024**2, 4 * 1024**3)
    assert cpu_percent({}) is None


def test_the_token_is_made_once(tmp_path):
    path = tmp_path / "control" / "token"
    first = ensure_token(path)
    assert len(first) > 30 and ensure_token(path) == first


def test_the_relay_checks_the_token_and_the_sandbox_then_copies_bytes():
    async def scenario() -> list[bytes]:
        async def echo(reader, writer):
            writer.write(b"SSH-2.0-sandbox\r\n")
            while data := await reader.read(100):
                writer.write(data.upper())
            writer.close()

        upstream = await asyncio.start_server(echo, "127.0.0.1", 0)
        port = upstream.sockets[0].getsockname()[1]

        async def address(key: str):
            if key != "p1":
                raise SandboxError(403, "not a sandbox of this YAAP")
            return "127.0.0.1", port

        relay = await asyncio.start_server(relay_handler(TOKEN, address), "127.0.0.1", 0)
        relay_port = relay.sockets[0].getsockname()[1]
        answers = []
        for line in (f"YAAP-SANDBOX wrong p1\n", f"YAAP-SANDBOX {TOKEN} p2\n", f"YAAP-SANDBOX {TOKEN} p1\n"):
            reader, writer = await asyncio.open_connection("127.0.0.1", relay_port)
            writer.write(line.encode())
            answers.append(await reader.readline())
            if answers[-1] == b"OK\n":
                answers.append(await reader.readline())
                writer.write(b"hello")
                answers.append(await reader.read(5))
            writer.close()
        relay.close()
        upstream.close()
        return answers

    answers = asyncio.run(asyncio.wait_for(scenario(), 10))
    assert answers[0] == b"ERR not allowed\n"
    assert answers[1].startswith(b"ERR not a sandbox")
    assert answers[2:] == [b"OK\n", b"SSH-2.0-sandbox\r\n", b"HELLO"]


PUBLISHED = "ghcr.io/jelvodev/yaap-sandbox:0.8.0"


def sandboxes_with(docker: FakeDocker, workspace: Path, image: str, pull_wait: float = 5.0) -> Sandboxes:
    config = Config(image=image, workspace_dir=workspace, workspace_host="/srv/yaap/data/workspace", instance="yaap")
    config.pull_wait = pull_wait
    return Sandboxes(DockerApi("http://docker", transport=docker.transport()), config)


@pytest.mark.parametrize(
    ("image", "registry", "pull_error", "status", "pulled", "detail"),
    [
        (PUBLISHED, {PUBLISHED: True}, "", 200, [PUBLISHED], ""),
        # A bare name is pulled with a tag, never as the whole repository.
        ("yaap-sandbox", {"yaap-sandbox:latest": True}, "", 200, ["yaap-sandbox:latest"], ""),
        (PUBLISHED, {PUBLISHED: False}, "", 403, [PUBLISHED], "not YAAP's sandbox image"),
        (PUBLISHED, {}, "", 503, [PUBLISHED], "could not be downloaded"),
        (PUBLISHED, {PUBLISHED: True}, "unauthorized: denied", 503, [PUBLISHED], "unauthorized: denied"),
        # An image only a local build makes is not looked for in a registry.
        ("yaap-sandbox:local", {"yaap-sandbox:local": True}, "", 503, [], "docker compose up -d --build"),
    ],
)
def test_a_missing_runner_image_is_pulled_and_checked_for_the_label(
    workspace, image, registry, pull_error, status, pulled, detail
):
    docker = FakeDocker(registry=registry, pull_error=pull_error)
    started = call(sandboxes_with(docker, workspace, image), "POST", "/sandboxes/p1/start", json=start_body())
    assert started.status_code == status, started.text
    assert detail in started.text
    assert docker.pulls == pulled
    assert bool(docker.created) == (status == 200)


def test_an_image_that_is_there_is_not_pulled(docker, workspace):
    docker.add_image(PUBLISHED)
    docker.registry[PUBLISHED] = True
    started = call(sandboxes_with(docker, workspace, PUBLISHED), "POST", "/sandboxes/p1/start", json=start_body())
    assert started.status_code == 200 and docker.pulls == []


def test_starts_at_the_same_time_wait_for_one_download(workspace):
    docker = FakeDocker(registry={PUBLISHED: True})
    sandboxes = sandboxes_with(docker, workspace, PUBLISHED)
    (workspace / "default" / "other").mkdir()

    async def go() -> list[dict]:
        docker.pull_gate = asyncio.Event()
        first = asyncio.create_task(sandboxes.start("p1", StartSpec(**start_body(folder="default/site"))))
        second = asyncio.create_task(sandboxes.start("p2", StartSpec(**start_body(folder="default/other"))))
        await asyncio.sleep(0.05)
        docker.pull_gate.set()
        return await asyncio.gather(first, second)

    answers = asyncio.run(go())
    assert [answer["created"] for answer in answers] == [True, True]
    assert docker.pulls == [PUBLISHED]


def test_a_slow_download_answers_with_the_waiting_state_and_goes_on(workspace):
    docker = FakeDocker(registry={PUBLISHED: True})
    sandboxes = sandboxes_with(docker, workspace, PUBLISHED, pull_wait=0.05)

    async def go() -> dict:
        docker.pull_gate = asyncio.Event()
        with pytest.raises(SandboxError) as waiting:
            await sandboxes.start("p1", StartSpec(**start_body()))
        assert waiting.value.status == 503 and "Preparing the runner" in str(waiting.value)
        docker.pull_gate.set()
        sandboxes.config.pull_wait = 5.0
        return await sandboxes.start("p1", StartSpec(**start_body()))

    assert asyncio.run(go())["created"] is True
    assert docker.pulls == [PUBLISHED]
