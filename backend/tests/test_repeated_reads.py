import asyncio
from pathlib import Path

import pytest

from app.agent.gate import call_tool
from app.agent.repeated_reads import repeat_note, text_digest
from app.agent.runs import RunConfig, RunRegistry
from app.agent.tools import tools_for


@pytest.mark.parametrize(
    ("previous", "digest", "expected"),
    [
        (None, "a", None),
        (("a", 2), "b", None),
        (("a", 2), "a", "Unchanged since read 2 of this reply (src/x.py, lines 1–5); the text above is current."),
    ],
)
def test_repeat_note_only_for_the_same_text(previous, digest, expected):
    assert repeat_note(previous, "src/x.py", 1, 5, digest) == expected


def test_text_digest_differs_for_different_text():
    assert text_digest("a") == text_digest("a") != text_digest("b")


def start(folder: Path, savings: bool):
    registry = RunRegistry()
    run = registry.start(RunConfig(
        conversation_id=1,
        environment_id=1,
        project_id=1,
        project_folder=folder,
        blueprint_layers=[],
        tool_names=tools_for(has_project=True, has_attachments=False),
        git_author=None,
        github_token=None,
    ))
    run.state.context_savings = savings
    return registry, run


def read(registry, run, **arguments):
    return asyncio.run(call_tool(registry, run, "read_file", {"path": "a.py", **arguments}))[0]


@pytest.fixture
def folder(tmp_path):
    (tmp_path / "a.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    return tmp_path


def test_second_identical_read_returns_the_note(folder):
    registry, run = start(folder, True)
    first = read(registry, run)
    assert "     2\ttwo" in first
    assert read(registry, run) == "Unchanged since read 1 of this reply (a.py, lines 1–3); the text above is current."
    assert "     2\ttwo" in read(registry, run, offset=2)


def test_changed_file_is_read_in_full_again(folder):
    registry, run = start(folder, True)
    read(registry, run)
    (folder / "a.py").write_text("one\nTWO\nthree\n", encoding="utf-8")
    assert "     2\tTWO" in read(registry, run)
    assert read(registry, run).startswith("Unchanged since read 2 ")


def test_setting_off_always_returns_the_text(folder):
    registry, run = start(folder, False)
    first = read(registry, run)
    assert read(registry, run) == first
    assert run.state.read_ranges == {}
