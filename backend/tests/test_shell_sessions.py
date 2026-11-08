"""Shell sessions that live as long as their conversation: across replies, idle end, on a target."""

import os
import shutil
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.agent.policy import ToolPolicy
from app.agent.shells import CONTAINER_PLACE, ShellSessions, shells_dir
from app.agent.tools import tools_for
from app.storage.models import ShellSessionRecord
from app.targets.sessions import STATE_PREFIX, ShellState
from app.targets.ssh import SshSettings, script_for
from tests.test_chat import start_conversation
from tests.test_processes import GIT_BASH, bash, project, run_all, shell_sessions, start_run  # noqa: F401
from tests.test_ssh_targets import run_steps, server, ssh_target  # noqa: F401
from tests.test_tool_guard import start_run as start_guarded_run


def age(shells: ShellSessions, hours: float) -> None:
    """Make every session look unused for `hours`."""
    assert shells.session_factory is not None
    with shells.session_factory() as session:
        for row in session.query(ShellSessionRecord):
            row.last_used_at = datetime.now(UTC) - timedelta(hours=hours)
        session.commit()


# --- Sessions per conversation (32.1) -----------------------------------------------------------------------------


def test_a_variable_set_in_one_reply_is_there_in_the_next(project, bash):
    shells = shell_sessions(project.parent)
    first_registry, first = start_run(project, shells=shells)
    run_all(first_registry, first, [("run_command", {"command": "cd sub && export GREETING=hello", "session": "dev"})])
    first_registry.finish(first)

    # The next reply is a new run of the same conversation.
    registry, second = start_run(project, shells=shells)
    (output, is_error), = run_all(
        registry, second, [("run_command", {"command": 'pwd; echo "greeting=$GREETING"', "session": "dev"})]
    )
    assert not is_error, output
    assert output.splitlines()[1].endswith("/sub") and "greeting=hello" in output
    # Another conversation has its own sessions.
    other_registry, other = start_run(project, shells=shells)
    other.config = replace(other.config, conversation_id=2)
    (output, _), = run_all(other_registry, other, [("run_command", {"command": 'echo "x=$GREETING"', "session": "dev"})])
    assert "x=hello" not in output


def test_start_process_uses_a_session_of_an_earlier_reply(project, bash):
    shells = shell_sessions(project.parent)
    registry, run = start_run(project, shells=shells)
    results = run_all(
        registry,
        run,
        [
            ("start_process", {"command": "echo hi", "session": "web"}),
            ("run_command", {"command": "export PORT=4000", "session": "web"}),
        ],
    )
    assert results[0][1] and "no session 'web' in this conversation" in results[0][0]
    assert not results[1][1]
    registry.finish(run)
    assert shells.open(1, "web", CONTAINER_PLACE, create=False) is not None


def test_a_session_ends_after_two_idle_hours(project, bash):
    shells = shell_sessions(project.parent)
    registry, run = start_run(project, shells=shells)
    run_all(registry, run, [("run_command", {"command": "export LEVEL=3", "session": "dev"})])
    age(shells, 1.5)
    (output, _), = run_all(registry, run, [("run_command", {"command": 'echo "level=$LEVEL"', "session": "dev"})])
    assert "level=3" in output

    age(shells, 2.5)
    (output, _), = run_all(registry, run, [("run_command", {"command": 'echo "level=$LEVEL"', "session": "dev"})])
    assert "level=" in output and "level=3" not in output

    # The periodic pass ends idle sessions and removes their state.
    age(shells, 2.5)
    assert shells.end_idle() == 1
    assert shells.open(1, "dev", CONTAINER_PLACE, create=False) is None
    assert not any((shells_dir(project.parent) / "1").glob("*"))


def test_secrets_are_not_kept_in_the_session_state(project, bash):
    registry, run = start_run(project)
    run.services = replace(run.services, secrets={"TOKEN": "s3cret-value"})
    (output, is_error), = run_all(
        registry, run, [("run_command", {"command": "export KEPT=yes; echo {{secret:TOKEN}} >/dev/null", "session": "s"})]
    )
    assert not is_error, output
    state = next((project.parent / "shells" / "1").iterdir()) / "state.env"
    text = state.read_text(encoding="utf-8")
    assert "KEPT" in text and "s3cret-value" not in text and "YAAP_SECRET_TOKEN" not in text


def test_the_project_panel_lists_and_ends_sessions_and_deleting_the_conversation_ends_them(client):
    project = client.post("/api/v1/environments/1/projects", json={"name": "Shells"}).json()
    conversation = client.post(
        "/api/v1/environments/1/conversations",
        json={"model": "claude-opus-5-5", "effort": "medium", "project_id": project["id"]},
    ).json()
    shells: ShellSessions = client.app.state.shells
    first = shells.open(conversation["id"], "dev", CONTAINER_PLACE, create=True)
    shells.open(conversation["id"], "build", CONTAINER_PLACE, create=True)
    assert first is not None
    folder = shells.folder(first)
    assert folder is not None
    folder.mkdir(parents=True)

    listed = client.get(f"/api/v1/projects/{project['id']}/shell-sessions").json()
    assert {item["name"] for item in listed} == {"dev", "build"}
    assert listed[0]["place"] == "YAAP's container" and listed[0]["conversation_id"] == conversation["id"]
    assert listed[0]["ends_at"] > listed[0]["last_used_at"]

    ended = client.delete(f"/api/v1/projects/{project['id']}/shell-sessions/{first.id}")
    assert ended.status_code == 204 and not folder.exists()
    assert [item["name"] for item in client.get(f"/api/v1/projects/{project['id']}/shell-sessions").json()] == ["build"]
    assert client.delete(f"/api/v1/projects/{project['id']}/shell-sessions/{first.id}").status_code == 404

    remaining = shells.open(conversation["id"], "build", CONTAINER_PLACE, create=False)
    assert remaining is not None and shells.folder(remaining) is not None
    shells.folder(remaining).mkdir(parents=True)  # type: ignore[union-attr]
    assert client.delete(f"/api/v1/conversations/{conversation['id']}").status_code == 204
    assert not (shells_dir(client.app.state.data_dir) / str(conversation["id"])).exists()
    with client.app.state.session_factory() as session:
        assert session.query(ShellSessionRecord).count() == 0


def test_sessions_outside_a_project_are_not_listed(client):
    project = client.post("/api/v1/environments/1/projects", json={"name": "Empty"}).json()
    client.app.state.shells.open(start_conversation(client), "dev", CONTAINER_PLACE, create=True)
    assert client.get(f"/api/v1/projects/{project['id']}/shell-sessions").json() == []


def test_a_session_made_elsewhere_starts_again(tmp_path):
    shells = shell_sessions(tmp_path)
    first = shells.open(1, "dev", CONTAINER_PLACE, create=True)
    moved = shells.open(1, "dev", "target-4", create=True)
    assert first is not None and moved is not None and moved.token != first.token
    assert shells.folder(moved) is None


# --- Sessions on a target (32.2) ----------------------------------------------------------------------------------


def local_bash() -> str | None:
    if sys.platform == "win32":
        return str(GIT_BASH) if GIT_BASH.exists() else None
    return shutil.which("bash")


def remote_temp(tmp_path, server, shell: str, program: str):
    """Run the target's scripts on this computer, with their temp folder in the test folder."""
    temp = tmp_path / "remote-temp"
    temp.mkdir()
    server.shells = {shell: program}
    server.environment = {**os.environ, "TMPDIR": temp.as_posix(), "TMP": str(temp), "TEMP": str(temp)}
    return temp


def target_session_run(tmp_path, server, shells, root: str, settings_change=None):
    target = ssh_target(server, tmp_path.name)
    if settings_change:
        settings_change(target.settings)
    registry, run = start_guarded_run(
        tmp_path, target=target, target_root=root, shells=shells, policy=ToolPolicy(access="full")
    )
    run.config = replace(run.config, tool_names=tools_for(True, False, on_target=True))
    return registry, run


def test_a_bash_session_on_a_target_keeps_folder_and_variables(tmp_path, server):
    program = local_bash()
    if program is None:
        pytest.skip("bash is not installed.")
    temp = remote_temp(tmp_path, server, "bash", program)
    site = server.root / "work" / "site"
    (site / "sub").mkdir()
    shells = shell_sessions(tmp_path)

    registry, run = target_session_run(tmp_path, server, shells, site.as_posix())
    run.services = replace(run.services, secrets={"TOKEN": "s3cret-value"})
    results, _ = run_steps(
        registry,
        run,
        [("run_command", {"command": "cd sub && export GREETING=hello && echo {{secret:TOKEN}} >/dev/null", "session": "dev"})],
    )
    assert results[0][0].startswith("Exit code 0"), results
    registry.finish(run)

    # The next reply: a new run, a new connection.
    registry, run = target_session_run(tmp_path, server, shells, site.as_posix())
    results, _ = run_steps(
        registry,
        run,
        [
            ("run_command", {"command": 'pwd; echo "greeting=$GREETING"; exit 4', "session": "dev"}),
            ("run_command", {"command": 'echo "greeting=$GREETING"'}),
        ],
    )
    output = results[0][0]
    assert output.startswith("Exit code 4") and "/sub" in output and "greeting=hello" in output, output
    assert "greeting=hello" not in results[1][0]
    state = list(temp.glob(f"{STATE_PREFIX}*.env"))
    assert len(state) == 1
    assert "GREETING" in state[0].read_text(encoding="utf-8")
    assert "s3cret-value" not in state[0].read_text(encoding="utf-8")


def test_a_powershell_session_on_a_target_keeps_folder_and_variables(tmp_path, server):
    program = shutil.which("pwsh") or shutil.which("powershell")
    if program is None:
        pytest.skip("PowerShell is not installed.")
    shell = "pwsh" if os.path.basename(program).lower().startswith("pwsh") else "powershell"
    temp = remote_temp(tmp_path, server, shell, program)
    site = server.root / "work" / "site"
    (site / "sub").mkdir()
    shells = shell_sessions(tmp_path)

    def windows(settings: SshSettings) -> None:
        settings.system, settings.shell = "windows", shell

    registry, run = target_session_run(tmp_path, server, shells, str(site), windows)
    run.services = replace(run.services, secrets={"TOKEN": "s3cret-value"})
    results, _ = run_steps(
        registry,
        run,
        [
            (
                "run_command",
                {
                    "command": "Set-Location sub; $env:GREETING = \"it's hello\"; $null = '{{secret:TOKEN}}'; exit 4",
                    "session": "dev",
                },
            )
        ],
    )
    assert results[0][0].startswith("Exit code 4"), results
    registry.finish(run)

    registry, run = target_session_run(tmp_path, server, shells, str(site), windows)
    results, _ = run_steps(
        registry,
        run,
        [("run_command", {"command": '(Get-Location).Path; "greeting=$env:GREETING"', "session": "dev"})],
    )
    output = results[0][0]
    assert output.startswith("Exit code 0"), output
    assert str(site / "sub").lower() in output.lower() and "greeting=it's hello" in output, output
    state = list(temp.glob(f"{STATE_PREFIX}*.ps1"))
    assert len(state) == 1
    text = state[0].read_text(encoding="utf-8")
    assert "GREETING" in text and "s3cret-value" not in text and "YAAP_SECRET" not in text


def test_session_scripts_restore_before_the_secrets_and_save_after_the_command():
    session = ShellState("abc123", save=True)
    bash_settings = SshSettings("h", 22, "yaap", "", None)
    script = script_for(bash_settings, "/work/site", "make test", {"YAAP_SECRET_KEY": "v"}, session)
    assert script.index("cd '/work/site'") < script.index('"${TMPDIR:-/tmp}/yaap-shell-abc123"')
    assert script.index(". \"$__yaap_state.env\"") < script.index("export YAAP_SECRET_KEY='v'")
    assert script.index("trap __yaap_save EXIT") < script.index("make test")

    windows = SshSettings("h", 22, "yaap", "", None, system="windows", shell="pwsh")
    script = script_for(windows, "C:\\site", "npm test", {"YAAP_SECRET_KEY": "v"}, session)
    assert "yaap-shell-abc123.ps1" in script and "try {\r\nnpm test\r\n} finally {" in script
    assert script.index("Invoke-Expression") < script.index("$env:YAAP_SECRET_KEY = 'v'")
    # start_process restores the session but does not save it.
    started = script_for(windows, "C:\\site", "npm run dev", {}, ShellState("abc123", save=False))
    assert "finally" not in started and "WriteAllText" not in started
