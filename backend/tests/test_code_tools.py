"""The code tools on fixture projects in Python and TypeScript, against the fake language server of
tests/lsp_fake_server.py (real pyright and typescript-language-server are not installed where the suite runs): a
definition, references, a type error after an edit, a rename across two files with one checkpoint and the permission
check, the outline from document symbols and its fallback, and when the tools are offered."""

import asyncio
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from app.agent.gate import call_tool
from app.agent.policy import ToolPolicy
from app.agent.tools import own_tools
from app.checkpoints.service import CheckpointStore
from app.core import reply_run
from app.lsp.pool import LanguageServerPool
from app.lsp.servers import SERVERS
from app.lsp.service import CodeIntel, code_intel_for
from app.providers.fake_provider import ScriptedResponse
from tests.test_chat import send
from tests.test_checkpoints import checkpoints, folder_of
from tests.test_delegation import project_conversation
from tests.test_language_servers import fake_spec
from tests.test_tool_guard import start_run

CODE_TOOL_NAMES = {"find_definition", "find_references", "diagnostics", "rename_symbol", "document_symbols"}
UTIL = '''def add(a: int, b: int) -> int:
    return a + b


class Calculator:
    def total(self, values: list[int]) -> int:
        return sum(values)
'''
APP = "from util import add\n\nprint(add(1, 2))\n"
STORE = """export interface Item {
  name: string;
}

export function count(items: Item[]): number {
  return items.length;
}
"""
MAIN = 'import { count } from "./store";\n\nconsole.log(count([]));\n'


def python_project(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "util.py").write_text(UTIL, encoding="utf-8")
    (folder / "app.py").write_text(APP, encoding="utf-8")
    return folder


def typescript_project(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "store.ts").write_text(STORE, encoding="utf-8")
    (folder / "main.ts").write_text(MAIN, encoding="utf-8")
    return folder


def intel_for(folder: Path, pool: LanguageServerPool, language: str = "python") -> CodeIntel:
    intel = code_intel_for(folder, None, frozenset(), (fake_spec(language),), pool)
    assert intel is not None
    return intel


def play(registry, run, steps, pool: LanguageServerPool):  # noqa: ANN001, ANN201 - test helpers
    """The tool calls in one event loop, so the language server lives through all of them; stopped afterwards."""

    async def scenario():
        run.services = replace(run.services, events=asyncio.Queue())
        results = []
        for name, arguments in steps:
            output, is_error = await call_tool(registry, run, name, arguments)
            results.append((str(output), is_error))
        await pool.stop_all()
        return results

    return asyncio.run(asyncio.wait_for(scenario(), 90))


def test_definition_and_references_in_a_python_project(tmp_path: Path):
    folder = python_project(tmp_path / "project")
    pool = LanguageServerPool()
    registry, run = start_run(folder, code_intel=intel_for(folder, pool))
    results = play(
        registry,
        run,
        [
            ("find_definition", {"path": "app.py", "line": 3, "symbol": "add"}),
            ("find_references", {"path": "util.py", "line": 1, "symbol": "add"}),
            ("find_definition", {"path": "app.py", "line": 2, "symbol": "add"}),
            ("find_definition", {"path": "app.py", "line": 3, "symbol": "nothing"}),
        ],
        pool,
    )
    definition, references, near, missing = results
    assert definition == (
        "Definition of 'add' at app.py:3 (fake-python):\nutil.py:1:5: def add(a: int, b: int) -> int:",
        False,
    )
    assert references[0].splitlines() == [
        "3 references to 'add' at util.py:1 (fake-python):",
        "app.py:1:18: from util import add",
        "app.py:3:7: print(add(1, 2))",
        "util.py:1:5: def add(a: int, b: int) -> int:",
    ]
    # A line number one off still finds the symbol, and says where.
    assert "at app.py:1 (found on line 1, not 2)" in near[0] and not near[1]
    assert missing[1] and "'nothing' is not on line 3" in missing[0]


def test_definition_and_references_in_a_typescript_project(tmp_path: Path):
    folder = typescript_project(tmp_path / "web")
    pool = LanguageServerPool()
    registry, run = start_run(folder, code_intel=intel_for(folder, pool, "typescript"))
    definition, references, symbols = play(
        registry,
        run,
        [
            ("find_definition", {"path": "main.ts", "line": 3, "symbol": "count"}),
            ("find_references", {"path": "store.ts", "line": 5, "symbol": "Item"}),
            ("document_symbols", {"path": "store.ts"}),
        ],
        pool,
    )
    assert definition[0].splitlines()[1] == "store.ts:5:17: export function count(items: Item[]): number {"
    assert [row.split(":")[0:2] for row in references[0].splitlines()[1:]] == [["store.ts", "1"], ["store.ts", "5"]]
    assert "interface Item" in symbols[0] and "function count" in symbols[0]


def test_a_type_error_after_an_edit_appears_in_the_edit_result(tmp_path: Path):
    folder = python_project(tmp_path / "project")
    pool = LanguageServerPool()
    registry, run = start_run(folder, code_intel=intel_for(folder, pool))
    broken = {"path": "app.py", "old_string": "print(add(1, 2))", "new_string": 'total: int = "three"\nprint(total)'}
    results = play(
        registry,
        run,
        [
            # No server runs yet: the edit result stays as it was, without waiting for one.
            ("edit_file", {"path": "util.py", "old_string": "a + b", "new_string": "b + a"}),
            ("diagnostics", {"path": "app.py"}),
            ("edit_file", broken),
            ("write_file", {"path": "app.py", "content": APP}),
            ("diagnostics", {"path": "app.py"}),
        ],
        pool,
    )
    untouched, clean, edited, rewritten, again = results
    assert untouched == ("Edited util.py (1 replacement).", False)
    assert clean == ("fake-python: no problems in app.py.", False)
    assert edited[0].splitlines() == [
        "Edited app.py (1 replacement).",
        "",
        "[fake-python reports 1 problem in app.py now:]",
        '3:1 error: Type "str" is not assignable to declared type "int" (fake)',
    ]
    assert rewritten[0].endswith("[fake-python: no problems in this file.]")
    assert again[0] == "fake-python: no problems in app.py."


def test_a_rename_changes_two_files_after_one_checkpoint(client, fake):
    project, conversation_id = project_conversation(client)
    folder = python_project(folder_of(client, project))
    pool = LanguageServerPool()
    store = CheckpointStore(client.app.state.session_factory, Path(client.app.state.data_dir))
    registry, run = start_run(folder, checkpoints=store, code_intel=intel_for(folder, pool))
    run.config = replace(run.config, project_id=project["id"], conversation_id=conversation_id)
    ((output, is_error),) = play(
        registry, run, [("rename_symbol", {"path": "app.py", "line": 3, "symbol": "add", "new_name": "plus"})], pool
    )
    assert not is_error, output
    assert output.splitlines() == [
        "Renamed 'add' to 'plus' with fake-python: 3 changes in 2 files.",
        "  app.py (2 changes)",
        "  util.py (1 change)",
    ]
    assert (folder / "app.py").read_text(encoding="utf-8") == "from util import plus\n\nprint(plus(1, 2))\n"
    assert (folder / "util.py").read_text(encoding="utf-8").startswith("def plus(a: int, b: int) -> int:")
    taken = checkpoints(client, project)
    assert [item["tool"] for item in taken] == ["rename_symbol"]
    # The one checkpoint holds both files as they were before the rename.
    restored = client.post(f"/api/v1/projects/{project['id']}/checkpoints/{taken[0]['id']}/restore")
    assert restored.status_code == 200, restored.text
    assert (folder / "app.py").read_text(encoding="utf-8") == APP
    assert (folder / "util.py").read_text(encoding="utf-8") == UTIL


@pytest.mark.parametrize(
    ("policy", "unattended", "refused"),
    [
        (ToolPolicy(), True, False),
        (ToolPolicy(permissions={"edit_file": "ask"}), True, True),
        (ToolPolicy(permissions={"edit_file": "ask"}, access="full"), True, False),
        (ToolPolicy(access="read_only"), False, True),
    ],
)
def test_a_rename_follows_the_permission_of_edit_file(
    tmp_path: Path, policy: ToolPolicy, unattended: bool, refused: bool
):
    folder = python_project(tmp_path / "project")
    pool = LanguageServerPool()
    registry, run = start_run(folder, policy=policy, unattended=unattended, code_intel=intel_for(folder, pool))
    ((output, is_error),) = play(
        registry, run, [("rename_symbol", {"path": "util.py", "line": 1, "symbol": "add", "new_name": "plus"})], pool
    )
    assert is_error is refused, output
    assert ("plus" in (folder / "app.py").read_text(encoding="utf-8")) is not refused


def test_a_rename_refuses_a_file_changed_since_it_was_read(tmp_path: Path):
    folder = python_project(tmp_path / "project")
    pool = LanguageServerPool()
    registry, run = start_run(folder, code_intel=intel_for(folder, pool))

    async def scenario():
        await call_tool(registry, run, "read_file", {"path": "util.py"})
        (folder / "util.py").write_text(UTIL + "\n# changed elsewhere\n", encoding="utf-8")
        await call_tool(registry, run, "read_file", {"path": "app.py"})
        # util.py was read before it changed: the rename leaves every file alone.
        run.state.file_hashes["util.py"] = "outdated"
        rename = {"path": "app.py", "line": 3, "symbol": "add", "new_name": "plus"}
        result = await call_tool(registry, run, "rename_symbol", rename)
        await pool.stop_all()
        return result

    output, is_error = asyncio.run(asyncio.wait_for(scenario(), 60))
    assert is_error and "changed after you read it" in str(output)
    assert (folder / "app.py").read_text(encoding="utf-8") == APP


def test_the_outline_uses_document_symbols_where_a_server_runs_and_the_parser_otherwise(tmp_path: Path):
    folder = python_project(tmp_path / "project")
    pool = LanguageServerPool()
    registry, run = start_run(folder, code_intel=intel_for(folder, pool))
    parsed, symbols, served = play(
        registry,
        run,
        [
            ("read_file", {"path": "util.py", "outline": True}),
            ("document_symbols", {"path": "util.py"}),
            ("read_file", {"path": "util.py", "outline": True}),
        ],
        pool,
    )
    # The parser gives signatures; the fake server's symbols have none, which tells the two apart.
    assert "function add(a: int, b: int) -> int" in parsed[0]
    assert symbols[0] == served[0]
    assert served[0].splitlines()[1:] == [
        "        1-2  function add",
        "        5-7  class Calculator",
        "        6-7    method total",
    ]


def test_the_code_tools_are_offered_only_where_a_server_can_run(tmp_path: Path):
    folder = python_project(tmp_path / "project")
    pool = LanguageServerPool()
    _, without = start_run(folder)
    _, with_servers = start_run(folder, code_intel=intel_for(folder, pool))
    assert not CODE_TOOL_NAMES & {tool.name for tool in own_tools(without)}
    assert CODE_TOOL_NAMES <= {tool.name for tool in own_tools(with_servers)}
    # A sub-run has its parent's servers.
    assert with_servers.services.for_child().code_intel is with_servers.code_intel


def test_a_reply_in_a_project_offers_the_code_tools_when_a_server_can_run(client, fake, monkeypatch):
    project, conversation_id = project_conversation(client)
    python_project(folder_of(client, project))
    spec = fake_spec()
    monkeypatch.setattr(
        reply_run, "code_intel_for_run", lambda folder, runtime: code_intel_for(folder, runtime, frozenset(), (spec,))
    )
    fake.responses.append(ScriptedResponse(text="Looked."))
    send(client, conversation_id, "Where is add defined?")
    offered = {tool.name for tool in fake.requests[0].tool_box.definitions}
    assert CODE_TOOL_NAMES <= offered


@pytest.mark.skipif(shutil.which("pyright-langserver") is None, reason="pyright is not installed here")
def test_real_pyright_finds_a_definition_and_a_type_error(tmp_path: Path):
    folder = python_project(tmp_path / "project")
    pool = LanguageServerPool()
    intel = code_intel_for(folder, None, frozenset(), SERVERS, pool)
    assert intel is not None
    registry, run = start_run(folder, code_intel=intel)
    definition, _, edited = play(
        registry,
        run,
        [
            ("find_definition", {"path": "app.py", "line": 3, "symbol": "add"}),
            ("diagnostics", {"path": "app.py"}),
            ("edit_file", {"path": "app.py", "old_string": "print(add(1, 2))", "new_string": 'total: int = "x"'}),
        ],
        pool,
    )
    assert "util.py:1:5" in definition[0]
    assert "error" in edited[0]
