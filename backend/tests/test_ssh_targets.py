"""Computers over SSH as targets, against an SSH server in the test process."""

import asyncio
import codecs
import threading
from dataclasses import replace
from pathlib import Path

import asyncssh
import pytest

from app.agent.policy import ToolPolicy
from app.agent.gate import call_tool
from app.agent.tools import tools_for
from app.secrets.service import substitute
from app.storage.models import Conversation, Message, Target
from app.targets.service import allowed_access, working_folder
from app.targets.setup import windows_setup_script
from app.targets.ssh import CONNECTIONS, SshSettings, SshTarget, new_key_pair, script_for
from tests.test_chat import start_conversation
from tests.test_tool_guard import answer_approvals, start_run


class SshTestServer:
    """An SSH server on 127.0.0.1 in its own thread: SFTP inside `root`, and commands answered from a script.

    It accepts the client keys in `authorized`; a command over `bash -s` records the script it got on standard input.
    With `shells` set (shell name -> program on this computer), scripts for bash and PowerShell really run here, with
    `environment`, so shell sessions can be tested end to end. With `streams` set (exact command -> program), that
    command runs here with its input and output streamed both ways while it runs (an MCP server in a runner).
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.authorized: set[str] = set()
        self.scripts: list[str] = []
        self.shells: dict[str, str] = {}
        self.streams: dict[str, list[str]] = {}
        self.environment: dict[str, str] | None = None
        self.host_key = asyncssh.generate_private_key("ssh-ed25519")
        self.loop = asyncio.new_event_loop()
        self.port = 0
        self.server = None
        started = threading.Event()
        threading.Thread(target=self.serve, args=(started,), daemon=True).start()
        started.wait(10)

    @property
    def host_key_line(self) -> str:
        return self.host_key.export_public_key().decode().strip()

    def serve(self, started: threading.Event) -> None:
        asyncio.set_event_loop(self.loop)
        outer = self

        class Server(asyncssh.SSHServer):
            def begin_auth(self, username: str) -> bool:
                return True

            def public_key_auth_supported(self) -> bool:
                return True

            def validate_public_key(self, username: str, key: asyncssh.SSHKey) -> bool:
                line = key.export_public_key().decode().split()[1]
                return any(entry.split()[1] == line for entry in outer.authorized)

        async def run_here(program: list[str], process: asyncssh.SSHServerProcess) -> None:
            script = await process.stdin.read()
            outer.scripts.append(script)
            local = await asyncio.create_subprocess_exec(
                *program,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=outer.environment,
            )
            output, _ = await local.communicate(script.encode("utf-8"))
            process.stdout.write(output.decode("utf-8", errors="replace"))
            process.exit(local.returncode or 0)

        async def stream_here(program: list[str], process: asyncssh.SSHServerProcess) -> None:
            local = await asyncio.create_subprocess_exec(
                *program, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, env=outer.environment
            )

            # Forwarded as it comes, not by line: a language server's messages do not end in a newline.
            async def forward_input() -> None:
                while chunk := await process.stdin.read(65536):
                    local.stdin.write(chunk.encode("utf-8"))
                    await local.stdin.drain()
                local.stdin.close()

            async def forward_output() -> None:
                decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
                while chunk := await local.stdout.read(65536):
                    process.stdout.write(decoder.decode(chunk))

            feeding = asyncio.create_task(forward_input())
            await forward_output()
            feeding.cancel()
            process.exit(await local.wait())

        async def handle(process: asyncssh.SSHServerProcess) -> None:
            command = process.command or ""
            program = command.split()
            if command in outer.streams:
                await stream_here(outer.streams[command], process)
            elif program and program[0] in outer.shells:
                await run_here([outer.shells[program[0]], *program[1:]], process)
            elif command == "uname -s":
                process.stdout.write("Linux\n")
                process.exit(0)
            elif command == "id -u":
                process.stdout.write("1000\n")
                process.exit(0)
            elif command == "bash -s":
                script = await process.stdin.read()
                outer.scripts.append(script)
                process.stdout.write("ran the script\n")
                process.exit(3)
            else:
                process.exit(127)

        async def start() -> None:
            self.server = await asyncssh.create_server(
                Server,
                "127.0.0.1",
                0,
                server_host_keys=[self.host_key],
                process_factory=handle,
                sftp_factory=lambda channel: asyncssh.SFTPServer(channel, chroot=str(self.root)),
                encoding="utf-8",
            )
            self.port = self.server.sockets[0].getsockname()[1]
            started.set()

        self.loop.run_until_complete(start())
        self.loop.run_forever()

    def close(self) -> None:
        async def stop() -> None:
            self.server.close()
            await self.server.wait_closed()
            for task in asyncio.all_tasks():
                if task is not asyncio.current_task():
                    task.cancel()

        asyncio.run_coroutine_threadsafe(stop(), self.loop).result(10)
        self.loop.call_soon_threadsafe(self.loop.stop)


@pytest.fixture
def server(tmp_path):
    root = tmp_path / "remote"
    (root / "work" / "site").mkdir(parents=True)
    (root / "work" / "site" / "index.html").write_text("<h1>Hello</h1>\n", encoding="utf-8")
    found = SshTestServer(root)
    yield found
    found.close()


def add_target(client, server, **values):
    response = client.post(
        "/api/v1/environments/1/targets",
        json={"name": "Laptop", "host": "127.0.0.1", "port": server.port, "folders": ["/work"], **values},
    )
    assert response.status_code == 201, response.text
    target = response.json()
    server.authorized.add(target["public_key"])
    return target


def ready_target(client, server, **values):
    target = add_target(client, server, **values)
    tested = client.post(f"/api/v1/targets/{target['id']}/test").json()
    assert tested["state"] == "confirm", tested
    confirmed = client.post(
        f"/api/v1/targets/{target['id']}/host-key", json={"fingerprint": tested["pending_fingerprint"]}
    )
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()


def test_a_target_is_ready_only_after_its_host_key_is_confirmed(client, server):
    target = add_target(client, server)
    assert target["state"] == "new" and target["public_key"].startswith("ssh-ed25519 ")
    # The private key went into the vault as YAAP's own key, never offered to the agent.
    entries = client.get("/api/v1/vault/entries", params={"environment_id": 1}).json()
    key = next(entry for entry in entries if entry["kind"] == "ssh_key")
    assert key["agent_use"] is False

    tested = client.post(f"/api/v1/targets/{target['id']}/test").json()
    assert tested["state"] == "confirm" and tested["host_fingerprint"] is None
    expected = asyncssh.import_public_key(server.host_key_line).get_fingerprint("sha256")
    assert tested["pending_fingerprint"] == expected

    wrong = client.post(f"/api/v1/targets/{target['id']}/host-key", json={"fingerprint": "SHA256:not-this-one"})
    assert wrong.status_code == 409
    confirmed = client.post(f"/api/v1/targets/{target['id']}/host-key", json={"fingerprint": expected}).json()
    assert confirmed["state"] == "ready", confirmed
    assert confirmed["system"] == "linux" and confirmed["rights"] == "standard"
    assert confirmed["host_fingerprint"] == expected


def test_the_test_names_missing_folders(client, server):
    target = ready_target(client, server, folders=["/work", "/nowhere"])
    assert target["state"] == "problem" and "/nowhere" in target["last_test_result"]


def test_a_changed_host_key_blocks_the_target(client, server):
    target = ready_target(client, server)
    with client.app.state.session_factory() as session:
        stored = session.get(Target, target["id"])
        stored.host_key = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode()
        session.commit()
    tested = client.post(f"/api/v1/targets/{target['id']}/test").json()
    assert tested["state"] == "problem" and "host key" in tested["last_test_result"]


def test_a_computer_that_does_not_answer_is_a_problem(client, server):
    target = add_target(client, server, port=1)
    tested = client.post(f"/api/v1/targets/{target['id']}/test").json()
    assert tested["state"] == "problem" and "Cannot connect" in tested["last_test_result"]


def test_another_host_must_be_confirmed_again(client, server):
    target = ready_target(client, server)
    changed = client.patch(f"/api/v1/targets/{target['id']}", json={"host": "localhost"}).json()
    assert changed["state"] == "new" and changed["host_fingerprint"] is None


def test_full_access_on_a_target_takes_a_confirmation(client, server):
    target = add_target(client, server)
    refused = client.patch(f"/api/v1/targets/{target['id']}", json={"allow_full_access": True})
    assert refused.status_code == 409
    allowed = client.patch(
        f"/api/v1/targets/{target['id']}", json={"allow_full_access": True, "confirm_full_access": True}
    )
    assert allowed.status_code == 200 and allowed.json()["allow_full_access"] is True


def test_folders_must_be_absolute(client, server):
    response = client.post(
        "/api/v1/environments/1/targets", json={"name": "Bad", "host": "127.0.0.1", "folders": ["work"]}
    )
    assert response.status_code == 422


def test_removing_a_target_removes_its_key_and_frees_its_conversations(client, server):
    target = add_target(client, server)
    conversation = client.post(
        "/api/v1/environments/1/conversations",
        json={"model": "claude-opus-5-5", "effort": "medium", "target_id": target["id"]},
    ).json()
    assert conversation["target_id"] == target["id"]
    assert client.delete(f"/api/v1/targets/{target['id']}").status_code == 204
    entries = client.get("/api/v1/vault/entries", params={"environment_id": 1}).json()
    assert not [entry for entry in entries if entry["kind"] == "ssh_key"]
    assert client.get(f"/api/v1/conversations/{conversation['id']}").json()["target_id"] is None


def test_a_conversation_chooses_its_target_before_the_first_message(client, server):
    target = add_target(client, server)
    conversation_id = start_conversation(client)
    chosen = client.patch(f"/api/v1/conversations/{conversation_id}", json={"target_id": target["id"]})
    assert chosen.status_code == 200 and chosen.json()["target_id"] == target["id"]
    shown = client.get(f"/api/v1/conversations/{conversation_id}").json()["target"]
    assert shown["name"] == "Laptop" and shown["allow_full_access"] is False

    # Full access only when the target allows it.
    assert client.patch(f"/api/v1/conversations/{conversation_id}", json={"access": "full"}).status_code == 409
    client.patch(f"/api/v1/targets/{target['id']}", json={"allow_full_access": True, "confirm_full_access": True})
    assert client.patch(f"/api/v1/conversations/{conversation_id}", json={"access": "full"}).status_code == 200

    with client.app.state.session_factory() as session:
        conversation = session.get(Conversation, conversation_id)
        session.add(
            Message(conversation_id=conversation_id, role="user", content=[{"type": "text", "text": "hi"}])
        )
        session.commit()
        assert conversation is not None
    later = client.patch(f"/api/v1/conversations/{conversation_id}", json={"target_id": None})
    assert later.status_code == 409


def test_a_project_names_its_target_and_folder(client, server):
    target = add_target(client, server)
    project = client.post("/api/v1/environments/1/projects", json={"name": "Site"}).json()
    bad = client.put(
        f"/api/v1/projects/{project['id']}/settings/target", json={"value": {"id": target["id"], "folder": "site"}}
    )
    assert bad.status_code == 422
    good = client.put(
        f"/api/v1/projects/{project['id']}/settings/target",
        json={"value": {"id": target["id"], "folder": "/work/site"}},
    )
    assert good.status_code == 200, good.text
    conversation = client.post(
        "/api/v1/environments/1/conversations",
        json={"model": "claude-opus-5-5", "effort": "medium", "project_id": project["id"]},
    ).json()
    shown = client.get(f"/api/v1/conversations/{conversation['id']}").json()["target"]
    assert shown["id"] == target["id"] and shown["folder"] == "/work/site"


def ssh_target(server, key_name="test") -> SshTarget:
    private_key, public_key = new_key_pair("test")
    server.authorized.add(public_key)
    settings = SshSettings(
        host="127.0.0.1",
        port=server.port,
        user="yaap",
        private_key=private_key,
        host_key=server.host_key_line,
        label="Laptop",
    )
    return SshTarget(f"test-{key_name}", settings)


def run_steps(registry, run, steps, decision="approve"):
    """Tool calls in one event loop, answering approvals; the pooled SSH connection is closed in that loop."""
    seen: list[dict] = []

    async def scenario():
        run.services = replace(run.services, events=asyncio.Queue())
        answerer = asyncio.create_task(answer_approvals(registry, run, decision, seen))
        try:
            return [await call_tool(registry, run, name, arguments) for name, arguments in steps]
        finally:
            answerer.cancel()
            CONNECTIONS.close(run.target.key)

    return asyncio.run(asyncio.wait_for(scenario(), timeout=30)), seen


def target_run(tmp_path, server, **values):
    registry, run = start_run(
        tmp_path,
        target=ssh_target(server, tmp_path.name),
        target_root="/work/site",
        **values,
    )
    run.config = replace(run.config, tool_names=tools_for(True, False, on_target=True))
    return registry, run


def test_file_tools_work_in_the_folder_on_the_target(tmp_path, server):
    registry, run = target_run(tmp_path, server, policy=ToolPolicy(access="full"))
    steps = [
        ("list_files", {}),
        ("read_file", {"path": "index.html"}),
        ("edit_file", {"path": "index.html", "old_string": "Hello", "new_string": "Hi"}),
        ("write_file", {"path": "css/site.css", "content": "h1 { color: red; }\n"}),
        ("search", {"pattern": "color"}),
        ("read_file", {"path": "../../secret.txt"}),
        ("write_file", {"path": ".git/config", "content": "x"}),
    ]
    results, seen = run_steps(registry, run, steps)
    assert "index.html" in results[0][0]
    assert "<h1>Hello</h1>" in results[1][0]
    assert not results[2][1] and not results[3][1], results
    assert "css/site.css:1" in results[4][0]
    assert results[5][1] and "outside the working folder" in results[5][0]
    assert results[6][1] and ".git" in results[6][0]
    assert (server.root / "work" / "site" / "index.html").read_text(encoding="utf-8") == "<h1>Hi</h1>\n"
    assert (server.root / "work" / "site" / "css" / "site.css").exists()
    assert seen == []


def test_changes_on_a_target_ask_and_say_they_cannot_be_rolled_back(tmp_path, server):
    # Allowlisted commands still ask on a target.
    registry, run = target_run(tmp_path, server, policy=ToolPolicy(commands=("echo",)))
    results, seen = run_steps(
        registry,
        run,
        [("write_file", {"path": "a.txt", "content": "a"}), ("run_command", {"command": "echo hi"})],
        decision="deny",
    )
    assert [event["tool"] for event in seen] == ["write_file", "run_command"]
    assert all("cannot roll back" in event["summary"] for event in seen)
    assert not (server.root / "work" / "site" / "a.txt").exists()


def test_commands_run_in_the_folder_with_secrets_as_variables(tmp_path, server):
    registry, run = target_run(tmp_path, server, policy=ToolPolicy(access="full"))
    run.services = replace(run.services, secrets={"TOKEN": "s3cret-value"})
    results, seen = run_steps(
        registry, run, [("run_command", {"command": "deploy --token {{secret:TOKEN}}"})]
    )
    output, is_error = results[0]
    assert "Exit code 3" in output and "ran the script" in output, output
    # A command with a secret asks, also with full access.
    assert [event["tool"] for event in seen] == ["run_command"]
    script = server.scripts[-1]
    assert "export YAAP_SECRET_TOKEN='s3cret-value'" in script
    assert "cd '/work/site' || exit 1" in script
    assert "deploy --token ${YAAP_SECRET_TOKEN}" in script


def test_git_tools_are_not_offered_on_a_target():
    names = tools_for(False, False, on_target=True)
    assert {"read_file", "write_file", "run_command", "start_process"} <= names
    assert not names & {"git_status", "git_commit", "git_push", "search_project"}


def test_windows_commands_use_powershell_variables():
    command, variables = substitute("Invoke-Thing -Key {{secret:KEY}}", {"KEY": "v"}, powershell=True)
    assert command == "Invoke-Thing -Key $env:YAAP_SECRET_KEY"
    settings = SshSettings("h", 22, "yaap", "", None, system="windows", shell="pwsh")
    script = script_for(settings, "C:\\Users\\alex\\site", command, variables)
    assert "$env:YAAP_SECRET_KEY = 'v'" in script
    assert "Set-Location -LiteralPath 'C:\\Users\\alex\\site'" in script


def test_full_access_on_a_target_needs_its_switch_and_never_goes_to_scheduled_tasks():
    closed = Target(name="t", allow_full_access=False)
    open_ = Target(name="t", allow_full_access=True)
    assert allowed_access("full", closed) == "default"
    assert allowed_access("full", open_) == "full"
    assert allowed_access("full", open_, unattended=True) == "default"
    assert allowed_access("read_only", closed) == "read_only"
    assert allowed_access("full", None) == "full"


def test_the_working_folder_stays_inside_the_listed_folders():
    target = Target(name="Laptop", system="windows", folders=["C:\\Users\\alex\\Projects"], whole_computer=False)
    assert working_folder(target, "C:\\Users\\alex\\Projects\\site") == "C:\\Users\\alex\\Projects\\site"
    assert working_folder(target, "c:/users/alex/projects/site") == "c:/users/alex/projects/site"
    assert working_folder(target, None) == "C:\\Users\\alex\\Projects"
    with pytest.raises(Exception, match="not one of the folders"):
        working_folder(target, "C:\\Windows")
    target.whole_computer = True
    assert working_folder(target, "C:\\Windows") == "C:\\Windows"
    assert working_folder(target, None) == "C:\\"


def test_the_setup_script_limits_the_user_and_the_port():
    standard = windows_setup_script("ssh-ed25519 AAAA yaap", "standard", ["C:\\Users\\alex\\Projects"])
    assert "Match User $user" in standard and "PasswordAuthentication no" in standard
    assert "icacls.exe 'C:\\Users\\alex\\Projects' /grant" in standard
    assert "-RemoteAddress 172.16.0.0/12,192.168.65.0/24" in standard
    assert "Add-LocalGroupMember" not in standard
    administrator = windows_setup_script("ssh-ed25519 AAAA yaap", "administrator", [])
    assert "S-1-5-32-544" in administrator and "administrators_authorized_keys" in administrator
