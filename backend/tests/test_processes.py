import asyncio
import functools
import os
import shutil
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from app.agent.processes import ProcessManager
from app.agent.shells import ShellSessions
from app.agent.runs import RunConfig, RunRegistry, RunServices
from app.agent.command_tools import command_environment
from app.agent.gate import call_tool
from app.agent.tools import tools_for
from app.agent.policy import ToolPolicy
from app.storage.db import create_session_factory
from app.storage.models import Base
from app.targets.container import CONTAINER

PYTHON = Path(sys.executable).as_posix()
SERVER = f'"{PYTHON}" -c "import time; print(\'ready\', flush=True); time.sleep(60)"'
GIT_BASH = Path("C:/Program Files/Git/bin/bash.exe")


@pytest.fixture
def project(tmp_path):
    folder = tmp_path / "project"
    (folder / "sub").mkdir(parents=True)
    return folder


@pytest.fixture
def bash(monkeypatch):
    """Shell sessions need bash: on Windows, Git Bash (the bash in System32 is WSL's)."""
    if sys.platform == "win32":
        if not GIT_BASH.exists():
            pytest.skip("Git Bash is not installed.")
        monkeypatch.setenv("YAAP_BASH", str(GIT_BASH))
    elif shutil.which("bash") is None:
        pytest.skip("bash is not installed.")


def shell_sessions(data_dir: Path) -> ShellSessions:
    """Shell sessions on a database of their own, with their state under data_dir/shells."""
    engine = create_engine(f"sqlite:///{(data_dir / 'sessions.db').as_posix()}")
    Base.metadata.create_all(engine)
    shells = ShellSessions()
    shells.bind(create_session_factory(engine), data_dir)
    return shells


def start_run(folder: Path, manager: ProcessManager | None = None, shells: ShellSessions | None = None):
    registry = RunRegistry()
    config = RunConfig(
        conversation_id=1,
        environment_id=1,
        project_id=1,
        project_folder=folder,
        blueprint_layers=[],
        tool_names=tools_for(has_project=True, has_attachments=False),
        # Everything that would ask runs without approval in these tests.
        policy=ToolPolicy(permissions={}, commands=("echo", "cd", "pwd", "export", f'"{PYTHON}"')),
    )
    run = registry.start(config, RunServices(processes=manager, shells=shells or shell_sessions(folder.parent)))
    return registry, run


def run_all(registry, run, steps):
    """Run tool calls in one event loop; approvals are answered with approve."""

    async def approve():
        while True:
            event = (await run.events.get()).model_dump()
            if event["event"] == "approval_request":
                registry.decide(event["approval_id"], "approve")

    async def scenario():
        run.services = replace(run.services, events=asyncio.Queue())
        approver = asyncio.create_task(approve())
        results = []
        for step in steps:
            if callable(step):
                results.append(await step())
            else:
                results.append(await call_tool(registry, run, *step))
        approver.cancel()
        return results

    return asyncio.run(asyncio.wait_for(scenario(), timeout=60))


def test_a_session_keeps_folder_and_variables(project, bash):
    registry, run = start_run(project)
    results = run_all(
        registry,
        run,
        [
            ("run_command", {"command": "cd sub && export GREETING=hello", "session": "dev"}),
            ("run_command", {"command": 'pwd; echo "greeting=$GREETING"', "session": "dev"}),
            ("run_command", {"command": 'echo "other=$GREETING"', "session": "other"}),
        ],
    )
    assert not any(is_error for _, is_error in results), results
    assert results[1][0].splitlines()[1].endswith("/sub")
    assert "greeting=hello" in results[1][0]
    assert "other=" in results[2][0] and "other=hello" not in results[2][0]
    # The state lives under data/shells/<conversation>/, one folder per session.
    assert len(list((project.parent / "shells" / "1").iterdir())) == 2


def test_a_session_survives_exit_and_keeps_the_exit_code(project, bash):
    registry, run = start_run(project)
    results = run_all(
        registry,
        run,
        [
            ("run_command", {"command": "export LEVEL=3; exit 4", "session": "dev"}),
            ("run_command", {"command": 'echo "level=$LEVEL"', "session": "dev"}),
        ],
    )
    assert results[0][0].startswith("Exit code 4")
    assert "level=3" in results[1][0]


def test_bad_session_names_are_refused(project, bash):
    registry, run = start_run(project)
    (output, is_error), = run_all(registry, run, [("run_command", {"command": "echo hi", "session": "a b"})])
    assert is_error and "session name" in output


def test_background_process_lifecycle(project):
    manager = ProcessManager()
    registry, run = start_run(project, manager)

    results = run_all(
        registry,
        run,
        [
            ("start_process", {"command": SERVER, "port": 5173}),
            ("read_process_output", {"id": 1}),
            ("list_processes", {}),
            ("stop_process", {"id": 1}),
            ("read_process_output", {"id": 99}),
        ],
    )
    started, again, listing, stopped, missing = results
    assert not started[1] and "Started process 1" in started[0] and "ready" in started[0]
    assert "No new output." in again[0]
    assert "Process 1 (running, port 5173)" in listing[0]
    assert "stopped" in stopped[0]
    assert missing[1] and "No process 99" in missing[0]


def test_processes_of_another_project_are_not_reachable(project):
    manager = ProcessManager()
    registry, run = start_run(project, manager)

    async def start_elsewhere():
        managed = await manager.start(
            command=SERVER, executor=CONTAINER.executor(str(project)), env=command_environment(), environment_id=1, project_id=7, conversation_id=2
        )
        return managed

    async def cleanup():
        for managed in manager.processes.values():
            await manager.stop(managed, "test")
        return None

    results = run_all(registry, run, [start_elsewhere, ("stop_process", {"id": 1}), cleanup])
    assert results[1][1] and "No process 1" in results[1][0]


def test_idle_processes_are_stopped(project, monkeypatch):
    manager = ProcessManager()

    async def scenario():
        managed = await manager.start(
            command=SERVER, executor=CONTAINER.executor(str(project)), env=command_environment(), environment_id=1, project_id=1, conversation_id=1
        )
        managed.last_touched -= 2 * 60 * 60
        await manager.stop_idle()
        return managed

    managed = asyncio.run(asyncio.wait_for(scenario(), timeout=30))
    assert managed.status == "stopped" and "an hour" in (managed.ended_reason or "")


def test_project_page_lists_and_stops_processes(client, tmp_path):
    project = client.post("/api/v1/environments/1/projects", json={"name": "Server"}).json()
    manager: ProcessManager = client.app.state.processes
    folder = tmp_path / "workspace"
    start = functools.partial(
        manager.start,
        command=SERVER,
        executor=CONTAINER.executor(str(folder)),
        env=command_environment(),
        environment_id=1,
        project_id=project["id"],
        conversation_id=None,
        port=3000,
    )
    managed = client.portal.call(start)
    client.portal.call(functools.partial(asyncio.sleep, 1.5))

    listed = client.get(f"/api/v1/projects/{project['id']}/processes").json()
    assert [(item["id"], item["status"], item["port"]) for item in listed] == [(managed.id, "running", 3000)]
    assert "ready" in listed[0]["output_tail"]
    # Times carry their zone, so the browser does not read UTC as local time.
    assert listed[0]["started_at"].endswith(("Z", "+00:00"))

    stopped = client.post(f"/api/v1/projects/{project['id']}/processes/{managed.id}/stop")
    assert stopped.status_code == 200 and stopped.json()["status"] == "stopped"
    assert stopped.json()["ended_reason"] == "stopped from the project page"
    assert client.post(f"/api/v1/projects/{project['id']}/processes/{managed.id}/stop").status_code == 404
    assert client.get(f"/api/v1/projects/{project['id']}/processes").json()[0]["status"] == "stopped"


def test_allowlisted_commands_also_start_processes_without_asking():
    policy = ToolPolicy(commands=("npm run dev",))
    assert policy.allows("start_process", {"command": "npm run dev -- --port 5173"})
    assert not policy.allows("start_process", {"command": "npm run dev; curl evil"})
    assert os.environ is not None
