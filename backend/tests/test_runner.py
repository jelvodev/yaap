"""The runner of an environment (choice 14): commands, MCP servers over stdio and the browser run in a
sandbox of the environment, never in YAAP's own process. The real helper runs in process on a fake Docker API with
its relay in front of the in-process SSH server, as in the sandbox tests."""

import asyncio
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from app.agent.policy import ToolPolicy
from app.agent.processes import ProcessManager
from app.agent.tools import tools_for
from app.checkpoints.service import CheckpointStore
from app.mcp_client.config import ServerConfig
from app.mcp_client.manager import McpManager
from app.mcp_client.remote import LAUNCHER, launch_script
from app.mcp_client.runs import server_place
from app.providers.fake_provider import ScriptedResponse
from app.runner.service import RUNNERS, find_runner, runner_path
from app.sandbox.service import SANDBOXES, public_key
from app.storage.models import Conversation, Target
from app.targets.container import LocalExecutor
from app.targets.service import allowed_access, conversation_run_target, describe_target
from app.targets.ssh import CONNECTIONS
from tests.test_chat import add_key, send
from tests.test_sandbox_target import world  # noqa: F401 - the fixture
from tests.test_ssh_targets import run_steps
from tests.test_tool_guard import start_run

REPOSITORY = Path(__file__).resolve().parents[2]
STUB = str(Path(__file__).with_name("runner_launch_stub.py"))


@pytest.fixture
def runner(world, client, monkeypatch):  # noqa: F811 - the fixture
    """The world of the sandbox tests with runners on, and YAAP's own process refusing to run any command."""

    async def no_local_command(*_args, **_kwargs):
        raise AssertionError("A command ran in YAAP's own process.")

    monkeypatch.setattr(LocalExecutor, "run", no_local_command)
    monkeypatch.setattr(LocalExecutor, "spawn", no_local_command)
    RUNNERS.enabled = True
    yield world
    RUNNERS.enabled = False


def authorize(world, client, environment_id: int) -> None:  # noqa: F811 - the fixture's value
    target_id = RUNNERS.ensure(environment_id)
    with client.app.state.session_factory() as session:
        world.server.authorized.add(public_key(session, session.get_one(Target, target_id)))


def project_in(client, environment_id: int, name: str = "Site") -> tuple[dict, int]:
    project = client.post(f"/api/v1/environments/{environment_id}/projects", json={"name": name}).json()
    conversation = client.post(
        f"/api/v1/environments/{environment_id}/conversations",
        json={"model": "claude-opus-5-5", "effort": "medium", "project_id": project["id"]},
    ).json()
    return project, conversation["id"]


def runner_run(client, project: dict, conversation_id: int, **values):
    with client.app.state.session_factory() as session:
        conversation = session.get_one(Conversation, conversation_id)
        on_target = conversation_run_target(session, client.app.state.cipher, conversation)
        assert on_target is not None
        description = describe_target(on_target.target, on_target.root)
    folder = Path(client.app.state.workspace_dir) / project["folder"]
    registry, run = start_run(
        folder, target=on_target.runtime, target_root=on_target.file_root, processes=ProcessManager(), **values
    )
    run.config = replace(
        run.config,
        project_id=project["id"],
        conversation_id=conversation_id,
        environment_id=conversation.environment_id,
        tool_names=tools_for(True, False, None, not on_target.has_container_tools),
    )
    checkpoints = CheckpointStore(client.app.state.session_factory, Path(client.app.state.data_dir))
    run.services = replace(run.services, checkpoints=checkpoints)
    return registry, run, description


def test_a_project_command_runs_in_the_runner_that_mounts_only_its_environment(runner, client):
    authorize(runner, client, 1)
    project, conversation_id = project_in(client, 1)
    registry, run, description = runner_run(client, project, conversation_id, policy=ToolPolicy(access="full"))
    slug, name = project["folder"].split("/")
    assert f"/workspace/{name}" in description and "has network access" in description
    # No Docker inside: a web app runs with start_process and its port, shown in the preview.
    assert "no dockerd and no docker compose" in description and "start_process and its port" in description

    results, _ = run_steps(registry, run, [("run_command", {"command": "cat /proc/1/environ /data/yaap.db"})])
    assert "ran the script" in results[0][0], results
    script = runner.server.scripts[-1]
    assert f"cd '/workspace/{name}' || exit 1" in script
    # The runner holds only the environment's folder: no data folder, no database, no Claude Code sign-in, and none of
    # YAAP's environment (the sandbox entrypoint starts sshd with an empty one).
    created = runner.docker.created[0]
    assert created["Labels"]["org.yaap.sandbox.key"] == "e1"
    assert created["HostConfig"]["Mounts"] == [{"Type": "bind", "Source": f"/host/workspace/{slug}", "Target": "/workspace"}]
    assert created["HostConfig"]["NetworkMode"] == "yaap-sandbox-online"
    assert not any("MASTER_KEY" in item for item in created["Env"])
    with client.app.state.session_factory() as session:
        target = find_runner(session, 1)
        assert target.kind == "runner" and target.project_id is None
        assert target.host_key == runner.server.host_key_line and target.last_used_at is not None
    # Not a computer of Settings › Targets, and the conversation shows no target in its header.
    assert client.get("/api/v1/environments/1/targets").json() == []
    assert client.get(f"/api/v1/conversations/{conversation_id}").json()["target"] is None


def test_the_runner_of_one_environment_does_not_see_another_environments_workspace(runner, client):
    second = client.post("/api/v1/environments", json={"name": "Clients", "colour": "#336699"})
    assert second.status_code in (200, 201), second.text
    other_id = second.json()["id"]
    for environment_id in (1, other_id):
        authorize(runner, client, environment_id)
        project, conversation_id = project_in(client, environment_id)
        registry, run, _ = runner_run(client, project, conversation_id, policy=ToolPolicy(access="full"))
        run_steps(registry, run, [("run_command", {"command": "ls /workspace"})])
    mounts = {body["Labels"]["org.yaap.sandbox.key"]: body["HostConfig"]["Mounts"] for body in runner.docker.created}
    assert set(mounts) == {"e1", f"e{other_id}"}
    sources = [mount[0]["Source"] for mount in mounts.values()]
    assert all(len(mount) == 1 and mount[0]["Target"] == "/workspace" for mount in mounts.values())
    assert len(set(sources)) == 2 and not any(a.startswith(b + "/") for a in sources for b in sources)


def test_yaap_shares_no_network_with_a_runner():
    compose = yaml.safe_load((REPOSITORY / "docker-compose.yml").read_text(encoding="utf-8"))
    services, networks = compose["services"], compose["networks"]
    runner_networks = {"sandbox-offline", "sandbox-online"}
    assert not runner_networks & set(services["yaap"]["networks"])
    # YAAP reaches the helper over sandbox-control only, which no runner joins; the offline network has no route out.
    assert networks["sandbox-control"]["internal"] and networks["sandbox-offline"]["internal"]
    assert {networks[name]["name"] for name in runner_networks} == {"yaap-sandbox-offline", "yaap-sandbox-online"}
    # The helper, its proxy and the sandbox image always run: no Compose profile any more.
    assert all("profiles" not in services[name] for name in ("yaap-sandbox", "docker-proxy", "sandbox-image"))


def test_the_runner_network_setting_takes_it_offline(runner, client):
    authorize(runner, client, 1)
    assert client.put("/api/v1/environments/1/settings/runner_network", json={"value": "off"}).status_code == 200
    project, conversation_id = project_in(client, 1)
    registry, run, description = runner_run(client, project, conversation_id, policy=ToolPolicy(access="full"))
    assert "no network access" in description
    run_steps(registry, run, [("run_command", {"command": "true"})])
    assert runner.docker.created[0]["HostConfig"]["NetworkMode"] == "yaap-sandbox-offline"


def test_without_the_helper_a_command_is_refused_with_what_to_do(runner, client):
    project, conversation_id = project_in(client, 1)
    SANDBOXES.helper = None
    registry, run, _ = runner_run(client, project, conversation_id, policy=ToolPolicy(access="full"))
    results, _ = run_steps(registry, run, [("run_command", {"command": "ls"}), ("start_process", {"command": "npm run dev"})])
    for output, is_error in results:
        assert is_error and "runner" in output and "docker compose up -d --build" in output, output


def test_a_reply_starts_without_the_runner_and_its_command_is_refused(runner, client, fake):
    add_key(client)
    project, conversation_id = project_in(client, 1)
    SANDBOXES.helper = None
    client.put("/api/v1/environments/1/settings/tool_permissions", json={"value": {"run_command": "auto"}})
    fake.responses.append(ScriptedResponse(text="Listing.", tool_calls=(("run_command", {"command": "ls"}),)))
    fake.responses.append(ScriptedResponse(text="The runner is not there."))
    events = send(client, conversation_id, "List the files.")
    result = next(data for name, data in events if name == "tool_result")
    assert result["is_error"] and "docker compose up -d --build" in result["output"], result
    system = "\n".join(part.text for part in fake.requests[0].system)
    assert "runner" in system and f"/workspace/{project['folder'].split('/')[1]}" in system
    assert "run_command" in [tool.name for tool in fake.requests[0].tool_box.definitions]


def test_checkpoints_and_the_editor_work_on_a_project_whose_commands_run_in_the_runner(runner, client):
    authorize(runner, client, 1)
    project, conversation_id = project_in(client, 1)
    registry, run, _ = runner_run(client, project, conversation_id, policy=ToolPolicy(access="full"))
    results, _ = run_steps(
        registry,
        run,
        [("write_file", {"path": "notes.txt", "content": "one\n"}), ("run_command", {"command": "make"})],
    )
    assert not results[0][1] and not results[1][1], results
    folder = Path(client.app.state.workspace_dir) / project["folder"]
    # The file tools write YAAP's own copy of the folder, the one the runner mounts; no SFTP to the runner.
    assert (folder / "notes.txt").read_text(encoding="utf-8") == "one\n"
    assert not (runner.server.root / "workspace" / "notes.txt").exists()
    kinds = [item["tool"] for item in client.get(f"/api/v1/projects/{project['id']}/checkpoints").json()]
    assert kinds.count("write_file") == 1 and kinds.count("run_command") == 1

    opened = client.get(f"/api/v1/projects/{project['id']}/files/content", params={"path": "notes.txt"}).json()
    saved = client.put(
        f"/api/v1/projects/{project['id']}/files/content",
        json={"path": "notes.txt", "text": "two\n", "base_hash": opened["hash"]},
    )
    assert saved.status_code == 200 and saved.json()["checkpoint_id"] is not None, saved.text
    assert (folder / "notes.txt").read_text(encoding="utf-8") == "two\n"


def test_an_mcp_server_over_stdio_runs_in_the_runner_with_its_secret_passed_per_start(runner, client, tmp_path):
    authorize(runner, client, 1)
    record = tmp_path / "launches.txt"
    runner.server.streams[LAUNCHER] = [sys.executable, STUB, str(record)]
    server = ServerConfig(name="tools", transport="stdio", command="npx", args=("-y", "tool server"), env=(("TOKEN", "s3cr'et"),))
    where = server_place(1)
    assert where.scope == "runner-e1" and where.cwd == "/workspace"
    manager = McpManager()

    async def scenario():
        tools, errors = await manager.tools([server], where.scope, where.cwd, where.place)
        assert not errors, errors
        reading = next(tool for tool in tools if tool.tool == "env")
        result = await manager.call(reading, {"name": "TOKEN"})
        await manager.shutdown()
        # The connection belongs to this event loop, which ends here.
        for key in list(CONNECTIONS.open):
            CONNECTIONS.close(key)
        return result.text

    assert asyncio.run(asyncio.wait_for(scenario(), 30)) == "s3cr'et"
    launched = record.read_text(encoding="utf-8")
    assert launched.startswith(launch_script(server, "/workspace"))
    assert "cd '/workspace' || exit 1" in launched and "exec 'npx' '-y' 'tool server'" in launched
    # The command line SSH sees is the fixed launcher; the value only travels on the channel's input.
    assert "s3cr" not in LAUNCHER and runner.docker.created[0]["Labels"]["org.yaap.sandbox.key"] == "e1"


def test_mcp_servers_and_the_browser_stay_in_yaap_only_with_the_runner_off():
    previous = RUNNERS.enabled
    RUNNERS.enabled = False
    try:
        assert server_place(1).place is None and server_place(1).scope == ""
    finally:
        RUNNERS.enabled = previous


def test_the_idle_pass_stops_an_unused_runner_keeps_a_busy_one_and_never_removes_it(runner, client):
    authorize(runner, client, 1)
    project, conversation_id = project_in(client, 1)
    registry, run, _ = runner_run(client, project, conversation_id, policy=ToolPolicy(access="full"))
    run_steps(registry, run, [("run_command", {"command": "true"})])
    with client.app.state.session_factory() as session:
        target = find_runner(session, 1)
        target.last_used_at = datetime.now(UTC) - timedelta(hours=3)
        session.commit()

    class Running:
        status = "running"
        project_id = None
        environment_id = 1

    class Processes:
        processes = {1: Running()}

    previous = SANDBOXES.processes
    SANDBOXES.processes = Processes()
    try:
        assert asyncio.run(SANDBOXES.stop_idle()) == ([], [])
    finally:
        SANDBOXES.processes = previous
    assert asyncio.run(SANDBOXES.stop_idle()) == (["e1"], [])
    assert runner.docker.containers["yaap-sandbox-e1"]["State"]["Running"] is False


@pytest.mark.parametrize(
    ("kind", "access", "unattended", "expected"),
    [
        ("runner", "full", False, "full"),
        ("runner", "full", True, "full"),
        ("sandbox", "full", True, "default"),
        ("ssh", "full", False, "default"),
        (None, "full", True, "full"),
    ],
)
def test_the_runner_keeps_the_access_of_yaaps_container(kind, access, unattended, expected):
    target = Target(kind=kind, allow_full_access=kind == "sandbox") if kind else None
    assert allowed_access(access, target, unattended) == expected


@pytest.mark.parametrize(
    ("local", "expected"),
    [("work", "/workspace"), ("work/site", "/workspace/site"), ("work/site/src", "/workspace/site/src")],
)
def test_a_folder_of_the_environment_has_its_path_in_the_runner(tmp_path, local, expected):
    (tmp_path / local).mkdir(parents=True)
    assert runner_path(tmp_path / "work", str(tmp_path / local)) == expected


def test_a_folder_of_another_environment_has_no_path_in_the_runner(tmp_path):
    from app.targets.base import TargetError

    (tmp_path / "home" / "site").mkdir(parents=True)
    with pytest.raises(TargetError):
        runner_path(tmp_path / "work", str(tmp_path / "home" / "site"))


def test_stopping_a_process_over_ssh_closes_its_channel():
    class Remote:
        pid = -1
        killed = False

        def kill(self) -> None:
            self.killed = True

    class Managed:
        process = Remote()

    ProcessManager.signal(Managed(), 15)  # type: ignore[arg-type] - only the process is read
    assert Managed.process.killed
