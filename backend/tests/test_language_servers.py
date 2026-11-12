"""The language-server client against the fake server in tests/lsp_fake_server.py:
starting it, a request and its answer, diagnostics collected, a crash and a restart, the idle stop, a missing
program, and a server in the runner over the SSH connection of the shell sessions."""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest

from app.lsp.client import LanguageClient
from app.lsp.connection import LspError, LspUnavailable, encode_message, read_message
from app.lsp.paths import PathMap
from app.lsp.pool import MAX_RESTARTS, LanguageServerPool
from app.lsp.process import LOCAL, Place
from app.lsp.results import diagnostic_rows, locations
from app.lsp.servers import ServerSpec, project_servers
from app.lsp.service import code_intel_for, place_for
from app.lsp.text import PositionError, TextEdit, apply_edits, resolve_position, split_lines
from app.runner.service import RUNNERS
from app.storage.models import Conversation
from app.targets.service import conversation_run_target
from app.targets.ssh import CONNECTIONS, SshSettings, SshTarget
from app.targets.stdio import LAUNCHER
from tests.test_runner import authorize, project_in, runner  # noqa: F401 - the fixture
from tests.test_sandbox_target import world  # noqa: F401 - the fixture the runner fixture needs

FAKE_SERVER = str(Path(__file__).with_name("lsp_fake_server.py"))
UTIL = "def add(a: int, b: int) -> int:\n    return a + b\n"
APP = "from util import add\n\nprint(add(1, 2))\n"


def fake_spec(language: str = "python", *extra: str, in_image: bool = False) -> ServerSpec:
    suffixes = {"python": {".py": "python"}, "typescript": {".ts": "typescript", ".tsx": "typescriptreact"}}
    command = (sys.executable, FAKE_SERVER, "--language", language, *extra)
    return ServerSpec(language, "fake-" + language, command, suffixes[language], in_image)


def local_place(folder: Path) -> Place:
    return Place(LOCAL, PathMap(str(folder.resolve()), windows=sys.platform == "win32"))


@pytest.fixture
def python_project(tmp_path: Path) -> Path:
    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "util.py").write_text(UTIL, encoding="utf-8")
    (folder / "app.py").write_text(APP, encoding="utf-8")
    return folder


def run(scenario, timeout: float = 60):  # noqa: ANN001, ANN201 - a coroutine and what it returns
    return asyncio.run(asyncio.wait_for(scenario, timeout))


# --- The protocol and its helpers -------------------------------------------------------------------------------------


class Chunks:
    """A reader over fixed bytes, as asyncio's StreamReader reads them."""

    def __init__(self, data: bytes) -> None:
        self.reader = asyncio.StreamReader()
        self.reader.feed_data(data)
        self.reader.feed_eof()

    async def readline(self) -> bytes:
        return await self.reader.readline()

    async def readexactly(self, n: int) -> bytes:
        return await self.reader.readexactly(n)


def test_messages_are_framed_with_content_length_and_read_back_one_by_one():
    first, second = {"jsonrpc": "2.0", "id": 1, "result": "é"}, {"jsonrpc": "2.0", "method": "x", "params": {}}
    data = encode_message(first) + b"\r\n" + encode_message(second)

    async def scenario():
        reader = Chunks(data)
        return [await read_message(reader), await read_message(reader), await read_message(reader)]

    assert run(scenario()) == [first, second, None]
    body = json.dumps(first, ensure_ascii=False, separators=(",", ":")).encode()
    assert encode_message(first) == b"Content-Length: %d\r\n\r\n" % len(body) + body


@pytest.mark.parametrize(
    ("data", "problem"),
    [
        (b"Content-Length: nope\r\n\r\n{}", "invalid Content-Length"),
        (b"Content-Length: 2\r\n\r\n[]", "not a JSON object"),
        (b"Content-Length: 99999999999\r\n\r\n", "does not read"),
    ],
)
def test_a_broken_frame_is_an_error(data: bytes, problem: str):
    async def scenario():
        return await read_message(Chunks(data))

    with pytest.raises(LspError, match=problem):
        run(scenario())


@pytest.mark.parametrize(
    ("root", "windows", "uri", "relative"),
    [
        ("/workspace/site", False, "file:///workspace/site/src/a.py", "src/a.py"),
        ("/workspace/site", False, "file:///workspace/site%20two/a.py", None),
        ("/workspace/site", False, "file:///workspace/other/a.py", None),
        ("/workspace/site", False, "file:///usr/lib/python3/typing.pyi", None),
        ("/workspace/site", False, "untitled:Untitled-1", None),
        ("/workspace", False, "file:///workspace/a%20b.py", "a b.py"),
        ("C:\\Users\\alex\\site", True, "file:///C:/Users/alex/site/src/a.py", "src/a.py"),
        ("C:\\Users\\alex\\site", True, "file:///c%3A/Users/Alex/site/src/a.py", "src/a.py"),
        ("C:\\Users\\alex\\site", True, "file:///D:/site/a.py", None),
    ],
)
def test_file_uris_map_to_paths_in_the_project_folder(root: str, windows: bool, uri: str, relative: str | None):
    assert PathMap(root, windows).relative(uri) == relative


def test_a_relative_path_becomes_the_uri_the_server_knows():
    assert PathMap("/workspace/site").uri("src/a b.py") == "file:///workspace/site/src/a%20b.py"
    assert PathMap("/workspace/site").uri() == "file:///workspace/site"
    assert PathMap("C:\\Users\\alex\\site", True).uri("src/a.py") == "file:///C:/Users/alex/site/src/a.py"
    assert PathMap("/workspace").label("file:///usr/lib/x.pyi") == "/usr/lib/x.pyi"


@pytest.mark.parametrize(
    ("text", "line", "symbol", "expected"),
    [
        ("def add(a, b):\n", 1, "add", (0, 4, 1)),
        ("user_id = id(x)\n", 1, "id", (0, 10, 1)),
        ("x = 1\n\nprint(add(1))\n", 2, "add", (2, 6, 3)),
        ("emoji = '😀'; name = 1\n", 1, "name", (0, 14, 1)),
        ("a\r\nb = value\r\n", 2, "value", (1, 4, 2)),
        ("$el = 1\n", 1, "$el", (0, 0, 1)),
    ],
)
def test_a_line_and_a_symbol_name_resolve_to_a_utf16_position(text: str, line: int, symbol: str, expected: tuple):
    found = resolve_position(text, line, symbol)
    assert (found.line, found.character, found.used_line) == expected


@pytest.mark.parametrize(
    ("line", "symbol", "problem"),
    [(9, "add", "does not exist"), (1, "missing", "is not on line 1"), (1, " ", "symbol's name")],
)
def test_a_position_that_cannot_be_resolved_says_why(line: int, symbol: str, problem: str):
    with pytest.raises(PositionError, match=problem):
        resolve_position("def add():\n    pass\n", line, symbol)


def test_text_edits_apply_on_the_original_positions_and_keep_line_endings():
    text = "foo = 1\r\nprint(foo, '😀', foo)\r\n"
    def edit(line: int, start: int, end: int) -> TextEdit:
        return TextEdit({"line": line, "character": start}, {"line": line, "character": end}, "bar")

    changed = apply_edits(text, [edit(1, 17, 20), edit(0, 0, 3), edit(1, 6, 9)])
    assert changed == "bar = 1\r\nprint(bar, '😀', bar)\r\n"
    assert split_lines("a\r\nb\rc\n") == ["a", "b", "c", ""]
    with pytest.raises(PositionError, match="overlap"):
        apply_edits(text, [edit(0, 0, 3), edit(0, 1, 2)])


def test_answers_are_read_as_locations_and_diagnostics_as_short_rows():
    selection = {"start": {"line": 2, "character": 4}}
    link = {"targetUri": "file:///w/a.py", "targetRange": {}, "targetSelectionRange": selection}
    plain = {"uri": "file:///w/b.py", "range": {"start": {"line": 0, "character": 0}}}
    found = [(item.uri, item.line) for item in locations([link, plain, plain])]
    assert found == [("file:///w/a.py", 2), ("file:///w/b.py", 0)]
    assert locations(None) == []
    errors = tuple(
        {"range": {"start": {"line": line, "character": 0}}, "severity": severity, "message": f"m{line}", "source": "s"}
        for line, severity in ((5, 2), (1, 1), (3, 1))
    )
    assert diagnostic_rows(errors, 2) == ["2:1 error: m1 (s)", "4:1 error: m3 (s)", "[1 more not shown.]"]


# --- A server's life --------------------------------------------------------------------------------------------------


def test_a_server_starts_answers_a_request_and_publishes_diagnostics(python_project: Path, tmp_path: Path):
    record = tmp_path / "initialize.json"
    spec = fake_spec("python", "--record", str(record))
    pool = LanguageServerPool()
    place = local_place(python_project)

    async def scenario():
        client = await pool.get(place, spec)
        assert client is await pool.get(place, spec) and pool.running(place, spec) is client
        uri, version, sent = await client.sync("app.py", APP)
        assert (version, sent) == (1, True)
        position = resolve_position(APP, 3, "add")
        asked = {"textDocument": {"uri": uri}, "position": position.lsp()}
        answer = await client.request("textDocument/definition", asked)
        found = [(place.paths.relative(item.uri), item.line) for item in locations(answer)]

        after = client.generation
        broken = APP + 'total: int = "three"\n'
        uri, version, sent = await client.sync("app.py", broken)
        published = await client.wait_diagnostics(uri, version, after, 10)
        assert sent and version == 2 and published is not None and published.version == 2
        _, _, unchanged = await client.sync("app.py", broken)
        await pool.stop_all()
        return found, [item["message"] for item in published.diagnostics], unchanged

    found, messages, unchanged = run(scenario())
    assert found == [("util.py", 0)]
    assert messages == ['Type "str" is not assignable to declared type "int"'] and unchanged is False
    initialize = json.loads(record.read_text(encoding="utf-8"))
    # A local server gets YAAP's process id; the root is the project folder as the server sees it.
    assert initialize["processId"] == os.getpid() and initialize["rootUri"] == place.paths.uri()
    assert initialize["capabilities"]["general"]["positionEncodings"] == ["utf-16"]


def test_a_crashed_server_is_started_again_but_not_endlessly(python_project: Path):
    spec = fake_spec()
    pool = LanguageServerPool()
    place = local_place(python_project)

    async def crash(client: LanguageClient) -> None:
        await client.connection.notify("fake/crash", {})
        for _ in range(100):
            if not client.alive:
                return
            await asyncio.sleep(0.05)
        raise AssertionError("The fake server did not stop.")

    async def scenario():
        first = await pool.get(place, spec)
        await crash(first)
        assert pool.running(place, spec) is None
        second = await pool.get(place, spec)
        assert second is not first and second.alive
        # Documents are opened again in the new server.
        _, version, sent = await second.sync("util.py", UTIL)
        assert (version, sent) == (1, True)
        for _ in range(MAX_RESTARTS):
            await crash(await pool.get(place, spec))
        with pytest.raises(LspError, match=f"stopped {MAX_RESTARTS + 1} times") as refused:
            await pool.get(place, spec)
        await pool.stop_all()
        return str(refused.value)

    assert "crashing as asked" in run(scenario())


def test_an_idle_server_is_stopped_and_starts_again_when_needed(python_project: Path):
    spec = fake_spec()
    pool = LanguageServerPool(idle_seconds=60)
    place = local_place(python_project)

    async def scenario():
        client = await pool.get(place, spec)
        assert await pool.stop_idle() == 0
        pool.servers[pool.key(place, spec)].last_used = time.monotonic() - 61
        assert await pool.stop_idle() == 1
        assert not client.alive and pool.running(place, spec) is None
        again = await pool.get(place, spec)
        alive = again.alive
        await pool.stop_all()
        return alive, client.process.exit_code

    alive, exit_code = run(scenario())
    # The first server ended through shutdown and exit, not by being killed.
    assert alive and exit_code == 0


def test_a_program_that_is_not_installed_is_remembered_and_not_offered(python_project: Path):
    missing = ServerSpec("python", "nowhere", ("yaap-no-such-language-server", "--stdio"), {".py": "python"})
    pool = LanguageServerPool()
    place = local_place(python_project)

    with pytest.raises(LspUnavailable, match="yaap-no-such-language-server .* is not installed"):
        run(pool.get(place, missing))
    assert pool.is_missing(place, missing) and not pool.can_run(place, missing, installed_here=True)
    # Not installed locally: no code intelligence for the run at all.
    assert code_intel_for(python_project, None, frozenset(), (missing,), LanguageServerPool()) is None


def test_a_run_gets_code_intelligence_only_for_languages_its_project_has(python_project: Path, tmp_path: Path):
    python, typescript = fake_spec("python"), fake_spec("typescript")
    pool = LanguageServerPool()
    intel = code_intel_for(python_project, None, frozenset(), (python, typescript), pool)
    assert intel is not None and intel.servers == (python,) and intel.place.key == LOCAL
    assert intel.spec_for("src/a.py") is python and intel.spec_for("notes.md") is None
    with pytest.raises(LspUnavailable, match="No language server runs for notes.md"):
        intel.require("notes.md")
    (python_project / "node_modules").mkdir()
    (python_project / "node_modules" / "x.ts").write_text("", encoding="utf-8")
    assert project_servers(python_project, frozenset({"node_modules"}), (python, typescript)) == (python,)
    empty = tmp_path / "empty"
    empty.mkdir()
    assert code_intel_for(empty, None, frozenset(), (python,), pool) is None
    # On a computer over SSH the project's files are not in YAAP: no servers there.
    computer = SshTarget("target-9", SshSettings("host", 22, "me", "key", None))
    assert place_for(python_project, computer) is None


# --- In the runner, over SSH ------------------------------------------------------------------------------------------


def test_a_server_runs_in_the_runner_over_the_ssh_connection_with_mapped_paths(  # noqa: F811 - the fixture
    runner, client, tmp_path
):
    authorize(runner, client, 1)
    project, conversation_id = project_in(client, 1)
    folder = Path(client.app.state.workspace_dir) / project["folder"]
    (folder / "util.py").write_text(UTIL, encoding="utf-8")
    (folder / "app.py").write_text(APP, encoding="utf-8")
    name = project["folder"].split("/")[1]
    launched, initialize = tmp_path / "launch.txt", tmp_path / "initialize.json"
    runner.server.streams[LAUNCHER] = [
        sys.executable, FAKE_SERVER, "--launch-record", str(launched), "--record", str(initialize),
        "--map", f"/workspace/{name}={folder.resolve().as_posix()}",
    ]
    with client.app.state.session_factory() as session:
        conversation = session.get_one(Conversation, conversation_id)
        on_target = conversation_run_target(session, client.app.state.cipher, conversation)
    assert on_target is not None and RUNNERS.enabled
    image_server = ServerSpec("python", "pyright", ("pyright-langserver", "--stdio"), {".py": "python"}, in_image=True)
    pool = LanguageServerPool()
    intel = code_intel_for(folder, on_target.runtime, frozenset(), (image_server,), pool)
    assert intel is not None and intel.place.root == f"/workspace/{name}" and intel.place.remote is on_target.runtime

    async def scenario():
        client_, _ = await intel.client("app.py")
        uri, _, _ = await client_.sync("app.py", APP)
        position = resolve_position(APP, 1, "add").lsp()
        answer = await client_.request("textDocument/references", {
            "textDocument": {"uri": uri}, "position": position, "context": {"includeDeclaration": True},
        })
        found = sorted((intel.place.paths.relative(item.uri), item.line) for item in locations(answer))
        await pool.stop_all()
        for key in list(CONNECTIONS.open):
            CONNECTIONS.close(key)
        return uri, found

    uri, found = run(scenario())
    assert uri == f"file:///workspace/{name}/app.py"
    assert found == [("app.py", 0), ("app.py", 2), ("util.py", 0)]
    script = launched.read_text(encoding="utf-8")
    assert f"cd '/workspace/{name}' || exit 1" in script and "exec 'pyright-langserver' '--stdio'" in script
    sent = json.loads(initialize.read_text(encoding="utf-8"))
    # YAAP's process id means nothing in the runner, so the server gets none.
    assert sent["processId"] is None and sent["rootUri"] == f"file:///workspace/{name}"

