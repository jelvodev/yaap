"""Exploring by a worker: delegate(mode="explore") reads and searches only, without approvals, and
its report goes back capped."""

from pathlib import Path

from sqlalchemy import select

from app.agent.exploring import MAX_EXPLORE_REPORT, capped_report, explore_only, explore_tools
from app.agent.runs import AgentRun, RunConfig, RunServices
from app.agent.toolkit import Permission, Tool, schema
from app.agent.tools import TOOLS_BY_NAME, tools_for
from app.providers.fake_provider import ScriptedResponse
from app.storage.models import AgentRunRecord
from tests.conftest import parse_events
from tests.test_delegation import project_conversation


async def no_handler(_run: AgentRun, _arguments: dict) -> str:
    return ""


def extra(name: str, permission: Permission, read_only: bool) -> Tool:
    return Tool(name, name, schema({}, []), permission, no_handler, scope="always", read_only=read_only)


def test_the_explore_tools_follow_the_flags_of_the_tools():
    names = explore_tools()
    assert {"read_file", "list_files", "search", "git_status", "git_diff", "search_project"} <= names
    assert not names & {"write_file", "edit_file", "run_command", "git_commit", "delegate", "web_fetch", "open_document"}
    assert all(TOOLS_BY_NAME[name].read_only and TOOLS_BY_NAME[name].permission == "auto" for name in names)


def test_explore_only_narrows_the_tools_extra_tools_and_access():
    run = AgentRun(
        "token",
        RunConfig(conversation_id=1, environment_id=1, blueprint_layers=[], tool_names=tools_for(True, False)),
        RunServices(
            extra_tools={
                "docs_lookup": extra("docs_lookup", "auto", True),
                "docs_write": extra("docs_write", "auto", False),
                "docs_asks": extra("docs_asks", "ask", True),
            }
        ),
    )
    explore_only(run)
    assert run.tool_names <= explore_tools() and "read_file" in run.tool_names
    assert set(run.extra_tools) == {"docs_lookup"}
    assert run.policy.access == "read_only"


def test_a_long_report_is_capped_with_a_note():
    assert capped_report("short") == "short"
    capped = capped_report("x" * 5000)
    assert capped.startswith("x" * MAX_EXPLORE_REPORT)
    assert capped.endswith("[Report cut at 2,000 characters; 3,000 more were left out.]")
    assert len(capped) < MAX_EXPLORE_REPORT + 80


def test_an_exploring_sub_run_reads_without_approvals_and_cannot_change_anything(client, fake, tmp_path):
    project, conversation_id = project_conversation(client)
    folder = Path(tmp_path) / "workspace" / project["folder"]
    (folder / "app.py").write_text("def main():\n    return 1\n")
    fake.responses.extend(
        [
            ScriptedResponse(
                text="Found it.", tool_calls=(("delegate", {"task": "Where is main?", "mode": "explore"}),)
            ),
            ScriptedResponse(
                text="main is in app.py:1. " + "detail " * 600,
                tool_calls=(
                    ("read_file", {"path": "app.py"}),
                    ("list_files", {}),
                    ("write_file", {"path": "new.txt", "content": "no\n"}),
                    ("run_command", {"command": "rm app.py"}),
                ),
            ),
        ]
    )

    response = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Find main"})
    events = parse_events(response.text)

    main, explorer = fake.requests
    offered = {tool.name for tool in explorer.tool_box.definitions}
    assert offered <= explore_tools() and "read_file" in offered
    assert not offered & {"write_file", "edit_file", "run_command", "git_commit", "delegate"}
    assert "# Exploring task" in explorer.system[-1].text
    assert explorer.model == "claude-sonnet-5-5"

    assert not any(name == "approval_request" for name, _ in events)
    assert not (folder / "new.txt").exists() and (folder / "app.py").exists()

    result = next(data for name, data in events if name == "tool_result")
    assert result["output"].startswith("Exploration by worker, exploring on claude-sonnet-5-5: complete.")
    assert "main is in app.py:1." in result["output"]
    assert "[Report cut at 2,000 characters;" in result["output"]
    assert "Changed files" not in result["output"]

    with client.app.state.session_factory() as session:
        child = list(session.scalars(select(AgentRunRecord).order_by(AgentRunRecord.id)))[1]
    calls = [step for step in child.transcript if step["type"] == "tool_call"]
    assert [(step["name"], step["is_error"]) for step in calls] == [
        ("read_file", False),
        ("list_files", False),
        ("write_file", True),
        ("run_command", True),
    ]
    assert child.approvals == []
    # The record keeps the whole report.
    assert len(child.transcript[-1]["text"]) > MAX_EXPLORE_REPORT


def test_an_unknown_mode_is_refused(client, fake):
    _, conversation_id = project_conversation(client)
    fake.responses.extend(
        [ScriptedResponse(text="Ok.", tool_calls=(("delegate", {"task": "Look", "mode": "wander"}),))]
    )
    events = parse_events(client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Go"}).text)
    result = next(data for name, data in events if name == "tool_result")
    assert result["is_error"] and "mode must be work or explore" in result["output"]
