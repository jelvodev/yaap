"""Commands and long output: streaming, timeouts that keep the output, live output, full outputs."""

import asyncio
import os
import sys
import time
from dataclasses import replace

from app.agent.outputs import STORE_ABOVE, OutputStore, prune_outputs, read_range, search_lines, shortened
from app.agent.runs import RunRegistry
from app.agent.command_tools import run_command
from app.agent.gate import call_tool, keep_long_output
from app.agent.tools import tools_for
from app.targets.container import LocalExecutor
from tests.test_agent_tools import project, start_run  # noqa: F401

PY = f'"{sys.executable}"'


def python(code: str) -> str:
    return f'{PY} -c "{code}"'


def test_a_stopped_command_keeps_its_output(tmp_path):
    executor = LocalExecutor(tmp_path)
    chunks: list[str] = []
    code = "import time,sys; print('started', flush=True); time.sleep(30)"
    started = time.monotonic()
    result = asyncio.run(executor.run(python(code), dict(os.environ), 30, silence=1.5, on_output=chunks.append))
    assert time.monotonic() - started < 15
    assert result.timed_out and result.stopped == "silence" and "started" in result.output
    assert "started" in "".join(chunks)

    steady = python("import time; [(print(i, flush=True), time.sleep(0.3)) for i in range(100)]")
    total = asyncio.run(executor.run(steady, dict(os.environ), 2, silence=1.5))
    assert total.stopped == "total" and "0" in total.output


def test_run_command_explains_a_stop_and_spots_a_question(project):  # noqa: F811
    registry = RunRegistry()
    run = start_run(registry, project)
    code = "import sys,time; sys.stdout.write('Overwrite the file? [y/N]'); sys.stdout.flush(); time.sleep(30)"
    output = asyncio.run(run_command(run, {"command": python(code), "silence_seconds": 5}))
    assert output.startswith("Stopped after 5 seconds without new output. It probably waits for input")
    assert "start_process" in output and "Overwrite the file?" in output
    # The live output reached the chat while the command ran.
    events = []
    while not run.events.empty():
        events.append(run.events.get_nowait().model_dump())
    assert any(event["event"] == "tool_output" and "Overwrite" in event["text"] for event in events)


def test_commands_run_without_a_terminal_and_without_colours(project, monkeypatch):  # noqa: F811
    # A CI service sets CI itself, and a value that is already set wins over YAAP's default.
    monkeypatch.delenv("CI", raising=False)
    registry = RunRegistry()
    run = start_run(registry, project)
    code = "import os; print(os.environ['GIT_PAGER'], os.environ['CI'], chr(27) + '[31mred' + chr(27) + '[0m')"
    output = asyncio.run(run_command(run, {"command": python(code)}))
    assert output.replace("\r", "") == "Exit code 0\ncat 1 red"


def test_long_output_is_stored_and_read_back(project):  # noqa: F811
    registry = RunRegistry()
    run = start_run(registry, project)
    assert "read_output" in run.tool_names
    run.services = replace(run.services, outputs=OutputStore(project.parent / "data", 7))
    command = python("[print(f'line {number}') for number in range(1, 5001)]")

    async def scenario():
        async def approve():
            event = (await run.events.get()).model_dump()
            registry.decide(event["approval_id"], "approve")

        approver = asyncio.create_task(approve())
        output, _ = await call_tool(registry, run, "run_command", {"command": command})
        await approver
        return output

    short = asyncio.run(scenario()).replace("\r", "")
    assert "[Full output: output 1," in short and len(short) < STORE_ABOVE
    assert short.startswith("Exit code 0\nline 1\n") and "line 5000" in short
    part, _ = asyncio.run(call_tool(registry, run, "read_output", {"output": 1, "start_line": 2501, "end_line": 2502}))
    assert part.replace("\r", "").startswith("2501: line 2500\n2502: line 2501")
    found, _ = asyncio.run(call_tool(registry, run, "read_output", {"output": 1, "search": r"line 4999\s*$"}))
    assert found.replace("\r", "") == "5000: line 4999"
    missing, is_error = asyncio.run(call_tool(registry, run, "read_output", {"output": 9}))
    assert is_error and "no output 9" in missing


def test_output_is_stored_from_6000_characters_on(project):  # noqa: F811
    run = start_run(RunRegistry(), project)
    run.services = replace(run.services, outputs=OutputStore(project.parent / "data", 9))
    assert STORE_ABOVE == 6_000
    kept = keep_long_output(run, "run_command", "a" * 5_999)
    assert kept == "a" * 5_999 and run.outputs.read(1) is None
    stored = str(keep_long_output(run, "run_command", "b" * 6_001))
    assert run.outputs.read(1) == "b" * 6_001 and len(stored) < 6_001
    assert stored.startswith("b" * 2_000 + "\n\n[… 1,001 characters left out …]\n\n" + "b" * 3_000)
    assert stored.endswith(
        "[Full output: output 1, 1 lines, 6,001 characters. Read any part with read_output (a range of lines, or a "
        "search).]"
    )


def test_read_tools_are_never_shortened(project):  # noqa: F811
    registry = RunRegistry()
    run = start_run(registry, project)
    run.services = replace(run.services, outputs=OutputStore(project.parent / "data", 8))
    (project / "big.txt").write_text("y" * 20_000, encoding="utf-8")
    output, _ = asyncio.run(call_tool(registry, run, "read_file", {"path": "big.txt"}))
    assert "[Full output" not in output


def test_helpers_and_pruning(tmp_path):
    text = "\n".join(f"row {number}" for number in range(1, 3001))
    short = shortened(text, 3)
    assert short.startswith("row 1\n") and "row 3000" in short and "3,000 lines" in short
    assert "[Lines 1-400 of 3000.]" in read_range(text, 1, None)
    assert search_lines(text, "row 12$") == "12: row 12"
    assert search_lines(text, "(") == "No line matches '('."

    store = OutputStore(tmp_path, 5)
    assert [store.save("a"), store.save("b")] == [1, 2] and store.read(2) == "b"
    old = store.path(1)
    os.utime(old, (time.time() - 40 * 86400, time.time() - 40 * 86400))
    assert prune_outputs(tmp_path) == 1 and store.read(1) is None and store.read(2) == "b"


def test_a_deleted_conversation_takes_its_outputs_along(client):
    from tests.test_chat import start_conversation

    conversation_id = start_conversation(client)
    store = OutputStore(client.app.state.data_dir, conversation_id)
    store.save("the whole output")
    response = client.get(f"/api/v1/conversations/{conversation_id}/outputs/1")
    assert response.status_code == 200 and response.text == "the whole output"
    assert client.get(f"/api/v1/conversations/{conversation_id}/outputs/2").status_code == 404
    assert client.delete(f"/api/v1/conversations/{conversation_id}").status_code == 204
    assert not store.folder.exists()


def test_tools_for_offers_read_output():
    assert "read_output" in tools_for(has_project=False, has_attachments=False)
