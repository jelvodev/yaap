"""A sandbox's lifecycle: it stops when idle, is removed with its project, and the project panel shows
its state, CPU and memory with Stop and Rebuild."""

import asyncio
from datetime import UTC, datetime, timedelta

from app.agent.policy import ToolPolicy
from app.sandbox.service import SANDBOXES, find_sandbox
from app.storage.models import Target, VaultEntry
from app.targets.ssh import CONNECTIONS
from tests.test_sandbox_target import world  # noqa: F401 - the fixture
from tests.test_ssh_targets import run_steps
from yaap_sandbox.sandboxes import INSTANCE_LABEL, KEY_LABEL, LABEL


def started(world, client, tmp_path, name="Site"):  # noqa: F811
    project = world.project(name)
    registry, run, _ = world.run(tmp_path, world.conversation(project), policy=ToolPolicy(access="full"))
    run_steps(registry, run, [("run_command", {"command": "true"})])
    return project


def used_ago(client, project_id: int, hours: float) -> None:
    with client.app.state.session_factory() as session:
        target = find_sandbox(session, project_id)
        target.last_used_at = datetime.now(UTC) - timedelta(hours=hours)
        session.commit()


def test_a_sandbox_stops_when_idle(world, client, tmp_path):  # noqa: F811
    idle = started(world, client, tmp_path, "Idle")
    busy = started(world, client, tmp_path, "Busy")
    used_ago(client, idle["id"], 2)
    used_ago(client, busy["id"], 0.5)
    # A container of this YAAP that no project owns any more, and someone else's container.
    world.docker.add_container("yaap-sandbox-p999", {LABEL: "1", KEY_LABEL: "p999", INSTANCE_LABEL: "yaap"})
    world.docker.add_container("postgres", {"com.example": "x"})

    stopped, removed = asyncio.run(SANDBOXES.stop_idle())
    assert stopped == [f"p{idle['id']}"] and removed == ["p999"]
    containers = world.docker.containers
    assert containers[f"yaap-sandbox-p{idle['id']}"]["State"]["Running"] is False
    assert containers[f"yaap-sandbox-p{busy['id']}"]["State"]["Running"] is True
    assert "yaap-sandbox-p999" not in containers and "postgres" in containers

    # The next command starts it again, without making a new container.
    created = len(world.docker.created)
    registry, run, _ = world.run(tmp_path, world.conversation(idle), policy=ToolPolicy(access="full"))
    results, _ = run_steps(registry, run, [("run_command", {"command": "true"})])
    assert "ran the script" in results[0][0]
    assert containers[f"yaap-sandbox-p{idle['id']}"]["State"]["Running"] is True
    assert len(world.docker.created) == created


def test_a_running_background_process_keeps_the_sandbox_up(world, client, tmp_path):  # noqa: F811
    project = started(world, client, tmp_path)
    used_ago(client, project["id"], 3)

    class Running:
        status = "running"
        project_id = project["id"]
        environment_id = 1

    class Processes:
        processes = {1: Running()}

    previous = SANDBOXES.processes
    SANDBOXES.processes = Processes()
    try:
        assert asyncio.run(SANDBOXES.stop_idle()) == ([], [])
    finally:
        SANDBOXES.processes = previous


def test_the_panel_shows_state_cpu_and_memory_with_stop_and_rebuild(world, client, tmp_path):  # noqa: F811
    project = started(world, client, tmp_path)
    url = f"/api/v1/projects/{project['id']}/sandbox"
    state = client.get(url).json()
    assert state["state"] == "running" and state["cpu_percent"] == 80.0
    assert state["memory_bytes"] == 200 * 1024**2 and state["memory_limit"] == 4 * 1024**3
    assert state["stops_at"] is not None

    stopped = client.post(f"{url}/stop").json()
    assert stopped["state"] == "exited" and stopped["cpu_percent"] is None

    rebuilt = client.post(f"{url}/rebuild")
    assert rebuilt.status_code == 200, rebuilt.text
    assert rebuilt.json()["state"] == "running"
    assert len(world.docker.created) == 2
    with client.app.state.session_factory() as session:
        target = find_sandbox(session, project["id"])
        assert target.host_key == world.server.host_key_line
        key = f"target-{target.id}"

    # Rebuild connected to check the new sandbox; that connection lives in the app's event loop, so close it there.
    async def close() -> None:
        connection = CONNECTIONS.open.pop(key)
        connection.close()
        await connection.wait_closed()

    client.portal.call(close)

    other = client.post("/api/v1/environments/1/projects", json={"name": "Other"}).json()
    assert client.post(f"/api/v1/projects/{other['id']}/sandbox/stop").status_code == 404


def test_the_sandbox_is_removed_with_the_project(world, client, tmp_path):  # noqa: F811
    project = world.project()
    with client.app.state.session_factory() as session:
        target = find_sandbox(session, project["id"])
        target_id, entry_id = target.id, target.key_entry_id
    world.docker.add_container(
        f"yaap-sandbox-p{project['id']}", {LABEL: "1", KEY_LABEL: f"p{project['id']}", INSTANCE_LABEL: "yaap"}
    )
    assert client.delete(f"/api/v1/projects/{project['id']}").status_code == 204
    assert f"yaap-sandbox-p{project['id']}" not in world.docker.containers
    with client.app.state.session_factory() as session:
        assert session.get(Target, target_id) is None
        assert session.get(VaultEntry, entry_id) is None
