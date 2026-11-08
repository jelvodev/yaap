"""Eval runs and variants of them, on a small fixture project with the fake provider."""

import time
from pathlib import Path

from sqlalchemy import select

from app.git.service import commit_all, run_git
from app.providers.base import TokenUsage
from app.providers.fake_provider import ScriptedResponse
from app.storage.models import Conversation, EvalResult, EvalRun, Message, ScheduledRun, ScheduledTask
from tests.conftest import parse_events
from tests.test_chat import add_key
from tests.test_scheduled_tasks import tick

CASE = """---
summary: {summary}
start:
  commit: {commit}
{extra}checks:
  - kind: file
    name: greeting written
    path: hello.txt
    contains: hello
  - kind: command
    name: still a repository at the start commit
    run: git log -1 --format=%s
---

Write hello.txt with the word hello in it.
"""


def fixture_project(client) -> tuple[dict, Path, str]:
    """A project whose folder is a git repository with one commit."""
    add_key(client)
    project = client.post("/api/v1/environments/1/projects", json={"name": "Evals"}).json()
    folder = client.app.state.workspace_dir / project["folder"]
    (folder / "README.md").write_text("A fixture project.\n", encoding="utf-8")
    commit_all(folder, "Start")
    commit = run_git(["rev-parse", "HEAD"], folder).stdout.strip()
    return project, folder, commit


def write_case(folder: Path, name: str, commit: str, extra: str = "", summary: str = "Say hello") -> None:
    evals = folder / ".blueprint" / "evals"
    evals.mkdir(parents=True, exist_ok=True)
    (evals / f"{name}.md").write_text(CASE.format(summary=summary, commit=commit, extra=extra), encoding="utf-8")


def start_run(client, project_id: int, **body) -> dict:
    response = client.post(f"/api/v1/projects/{project_id}/evals/runs", json=body)
    assert response.status_code == 202, response.text
    return response.json()


def finished_run(client, project_id: int, run_id: int) -> dict:
    for _ in range(400):
        run = client.get(f"/api/v1/projects/{project_id}/evals/runs/{run_id}").json()
        if run["status"] not in ("queued", "running"):
            return run
        time.sleep(0.05)
    raise AssertionError("the eval run did not finish")


WRITE_HELLO = ScriptedResponse(text="Written.", tool_calls=(("write_file", {"path": "hello.txt", "content": "hello"}),))


def test_a_passing_case_removes_its_workspace_and_records_its_usage(client, fake):
    project, folder, commit = fixture_project(client)
    write_case(folder, "greet", commit)
    fake.responses.append(WRITE_HELLO)

    run = finished_run(client, project["id"], start_run(client, project["id"])["id"])

    assert run["status"] == "done" and run["summary"] == "1 of 1 passed."
    (result,) = run["results"]
    assert result["status"] == "passed", result
    assert [check["status"] for check in result["checks"]] == ["passed", "passed"]
    # One model step (the fake reports only Completed) and one tool step; the run record links the trace.
    assert result["steps"] == 2 and result["tokens"] == 15 and result["asked"] == 0
    assert result["agent_run_id"] is not None and result["conversation_id"] is not None
    # The workspace is gone, and the project itself was never touched.
    assert result["workspace"] is None
    assert not (client.app.state.workspace_dir / "default" / ".evals" / str(run["id"])).exists()
    assert not (folder / "hello.txt").exists()
    # The play's conversation is archived, out of the sidebar.
    with client.app.state.session_factory() as session:
        conversation = session.get_one(Conversation, result["conversation_id"])
        assert conversation.archived_at is not None and conversation.title == f"Eval {run['id']} · greet"


def test_a_failing_case_keeps_its_workspace_for_a_day(client, fake):
    project, folder, commit = fixture_project(client)
    write_case(folder, "greet", commit)
    fake.responses.append(ScriptedResponse(text="I did nothing."))

    run = finished_run(client, project["id"], start_run(client, project["id"])["id"])

    (result,) = run["results"]
    assert result["status"] == "failed"
    assert result["checks"][0]["status"] == "failed" and "does not exist" in result["checks"][0]["detail"]
    workspace = client.app.state.workspace_dir / result["workspace"]
    # A clone at the start commit, detached and without a remote: it shares nothing with the project.
    assert (workspace / "README.md").is_file() and (workspace / ".git").is_dir()
    assert run_git(["rev-parse", "HEAD"], workspace).stdout.strip() == commit
    assert run_git(["remote"], workspace).stdout.strip() == ""
    assert result["workspace_expires_at"] is not None

    # The minute loop prunes it once its time is up.
    with client.app.state.session_factory() as session:
        row = session.get_one(EvalResult, result["id"])
        row.workspace_expires_at = row.started_at
        session.commit()
    tick(client.app)
    assert not workspace.exists()
    again = client.get(f"/api/v1/projects/{project['id']}/evals/runs/{run['id']}").json()
    assert again["results"][0]["workspace"] is None


def test_a_call_that_would_ask_is_refused_and_counted(client, fake):
    project, folder, commit = fixture_project(client)
    write_case(folder, "greet", commit)
    write_case(folder, "greet-allowed", commit, extra="allowed_tools: [run_command]\n")
    asks = ScriptedResponse(
        text="Written.",
        tool_calls=(
            ("run_command", {"command": "echo hi"}),
            ("write_file", {"path": "hello.txt", "content": "hello"}),
        ),
    )
    fake.responses.extend([asks, asks])

    run = finished_run(client, project["id"], start_run(client, project["id"])["id"])

    results = {result["case_name"]: result for result in run["results"]}
    assert results["greet"]["asked"] == 1 and results["greet"]["status"] == "passed"
    # The case's allowed_tools run without asking, as a scheduled task's allowlists do.
    assert results["greet-allowed"]["asked"] == 0
    messages = client.get(f"/api/v1/conversations/{results['greet']['conversation_id']}").json()["messages"]
    call = next(b for m in messages for b in m["content"] if b["type"] == "tool_call" and b["name"] == "run_command")
    assert call["output"].startswith("Refused:")


def test_a_budget_overrun_stops_the_case_during_the_run_and_fails_it(client, fake):
    project, folder, commit = fixture_project(client)
    write_case(folder, "greet", commit, extra="budget:\n  tokens: 100 tokens\n")
    slow = ScriptedResponse(
        text=" ".join(["word"] * 60),
        delay=0.05,
        call_usages=(TokenUsage(input_tokens=500, output_tokens=10), TokenUsage(input_tokens=1, output_tokens=1)),
    )
    fake.responses.append(slow)

    run = finished_run(client, project["id"], start_run(client, project["id"])["id"])

    (result,) = run["results"]
    assert result["status"] == "failed"
    assert result["detail"].startswith("Stopped during the run: over budget (510 tokens of 100 tokens")
    budget = next(check for check in result["checks"] if check["kind"] == "budget")
    assert budget["status"] == "failed"
    # Stopped well before the 60 slow words (3 seconds): the reply was cut off and stored as incomplete. The play's
    # seconds count from the reply's start; the test's wall time also holds the clone of the workspace and a dozen git
    # processes, each a tenth of a second or more on Windows, which say nothing about the stop.
    assert result["seconds"] < 1.5
    with client.app.state.session_factory() as session:
        reply = session.scalars(
            select(Message).where(Message.conversation_id == result["conversation_id"], Message.role == "assistant")
        ).one()
        assert reply.status == "incomplete"


def test_an_invalid_case_is_listed_with_its_problems_and_cannot_run(client):
    project, folder, commit = fixture_project(client)
    write_case(folder, "greet", commit)
    (folder / ".blueprint" / "evals" / "broken.md").write_text("---\nstart: {}\n---\n", encoding="utf-8")

    cases = {case["name"]: case for case in client.get(f"/api/v1/projects/{project['id']}/evals/cases").json()}

    assert cases["greet"]["valid"] and cases["greet"]["start"] == f"commit {commit}" and cases["greet"]["checks"] == 2
    assert not cases["broken"]["valid"] and cases["broken"]["problems"]
    refused = client.post(f"/api/v1/projects/{project['id']}/evals/runs", json={"case_name": "broken"})
    assert refused.status_code == 409 and "no valid eval case broken" in refused.json()["detail"]


def test_eval_in_the_chat_starts_a_run(client, fake):
    project, folder, commit = fixture_project(client)
    write_case(folder, "greet", commit)
    fake.responses.append(WRITE_HELLO)
    body = {"model": "claude-opus-5-5", "effort": "medium", "project_id": project["id"], "preset": "Balanced"}
    conversation = client.post("/api/v1/environments/1/conversations", json=body).json()

    response = client.post(f"/api/v1/conversations/{conversation['id']}/messages", json={"text": "/eval greet"})

    assert response.status_code == 200, response.text
    ((name, payload),) = parse_events(response.text)
    assert name == "eval_started" and payload["project_id"] == project["id"]
    run = finished_run(client, project["id"], payload["run_id"])
    assert run["trigger"] == "command" and run["passed"] == 1
    # Nothing was added to the conversation the command was typed in.
    assert client.get(f"/api/v1/conversations/{conversation['id']}").json()["messages"] == []


def test_a_scheduled_task_can_run_the_eval_cases(client, fake):
    project, folder, commit = fixture_project(client)
    write_case(folder, "greet", commit)
    fake.responses.append(WRITE_HELLO)
    body = {
        "name": "Nightly evals",
        "action": "eval",
        "model": "claude-opus-5-5",
        "effort": "medium",
        "schedule_kind": "manual",
    }
    task = client.post(f"/api/v1/projects/{project['id']}/tasks", json=body)
    assert task.status_code == 201, task.text
    assert client.post(f"/api/v1/projects/{project['id']}/tasks/{task.json()['id']}/run").status_code == 202

    runs = client.get(f"/api/v1/projects/{project['id']}/evals/runs").json()
    finished_run(client, project["id"], runs[0]["id"])
    tick(client.app)
    with client.app.state.session_factory() as session:
        record = session.scalars(select(ScheduledRun)).one()
        assert record.status == "done" and record.summary == "1 of 1 passed."
        assert not session.get_one(ScheduledTask, task.json()["id"]).running


def test_removing_a_conversation_a_run_or_the_project_cleans_up(client, fake):
    project, folder, commit = fixture_project(client)
    write_case(folder, "greet", commit)
    fake.responses.extend([WRITE_HELLO, WRITE_HELLO])
    first = finished_run(client, project["id"], start_run(client, project["id"])["id"])
    second = finished_run(client, project["id"], start_run(client, project["id"])["id"])

    # A play's conversation removed: the result keeps its verdict, without the links.
    conversation_id = first["results"][0]["conversation_id"]
    assert client.delete(f"/api/v1/conversations/{conversation_id}").status_code == 204
    kept = client.get(f"/api/v1/projects/{project['id']}/evals/runs/{first['id']}").json()["results"][0]
    assert kept["status"] == "passed" and kept["conversation_id"] is None and kept["agent_run_id"] is None

    # A run removed: its plays' conversations go with it.
    second_conversation = second["results"][0]["conversation_id"]
    assert client.delete(f"/api/v1/projects/{project['id']}/evals/runs/{second['id']}").status_code == 204
    with client.app.state.session_factory() as session:
        assert session.get(Conversation, second_conversation) is None
        assert session.get(EvalRun, second["id"]) is None

    # The project removed: its eval runs go, and their conversations do not keep it.
    assert client.delete(f"/api/v1/projects/{project['id']}").status_code == 204
    with client.app.state.session_factory() as session:
        assert session.scalars(select(EvalRun)).all() == []
        assert session.scalars(select(EvalResult)).all() == []


def test_a_blueprint_variant_uses_the_branch_rules_and_leaves_main_untouched(client, fake):
    project, folder, commit = fixture_project(client)
    instructions = folder / ".blueprint" / "instructions.md"
    instructions.parent.mkdir(parents=True, exist_ok=True)
    instructions.write_text("Main rule: be brief.\n", encoding="utf-8")
    commit_all(folder, "Main rule")
    run_git(["switch", "--quiet", "-c", "proposal"], folder)
    instructions.write_text("Proposed rule: always write tests.\n", encoding="utf-8")
    commit_all(folder, "Proposed rule")
    run_git(["switch", "--quiet", "main"], folder)
    write_case(folder, "greet", commit)
    fake.responses.append(WRITE_HELLO)

    variant = {"layer": "project", "ref": "proposal"}
    run = finished_run(client, project["id"], start_run(client, project["id"], variant=variant)["id"])

    assert run["results"][0]["status"] == "passed" and run["variant"]["label"] == "project Blueprint at proposal"
    system = "\n".join(part.text for part in fake.requests[-1].system)
    assert "Proposed rule: always write tests." in system and "Main rule" not in system
    # The layer's branch, files and history are as they were.
    assert run_git(["branch", "--show-current"], folder).stdout.strip() == "main"
    assert instructions.read_text(encoding="utf-8") == "Main rule: be brief.\n"
    assert run_git(["worktree", "list"], folder).stdout.count("\n") == 1
    # The copy of the layer at the ref is gone with the run.
    assert not (client.app.state.data_dir / "evals" / str(run["id"])).exists()


def test_a_comparison_plays_both_sides_three_times(client, fake):
    project, folder, commit = fixture_project(client)
    write_case(folder, "greet", commit)
    fake.responses.extend([WRITE_HELLO] * 6)

    response = client.post(
        f"/api/v1/projects/{project['id']}/evals/comparisons", json={"variant": {"preset": "Balanced"}}
    )
    assert response.status_code == 202, response.text
    baseline, variant = response.json()
    assert variant["baseline_run_id"] == baseline["id"] and baseline["plays"] == 3 and variant["plays"] == 3
    finished_run(client, project["id"], baseline["id"])
    finished_run(client, project["id"], variant["id"])

    compared = client.get(
        f"/api/v1/projects/{project['id']}/evals/compare", params={"a": baseline["id"], "b": variant["id"]}
    ).json()
    (case,) = compared["cases"]
    assert case["case"] == "greet" and case["a"]["plays"] == 3 and case["b"]["plays"] == 3
    assert case["a"]["pass_rate"] == case["b"]["pass_rate"] == 1.0 and case["steps_difference"] == 0
    assert compared["pass_rate_a"] == compared["pass_rate_b"] == 1.0


def test_stopping_a_run_stops_its_playing_case(client, fake):
    project, folder, commit = fixture_project(client)
    write_case(folder, "greet", commit)
    fake.responses.append(ScriptedResponse(text=" ".join(["word"] * 200), delay=0.05))
    run = start_run(client, project["id"])
    for _ in range(100):
        detail = client.get(f"/api/v1/projects/{project['id']}/evals/runs/{run['id']}").json()
        if detail["results"][0]["conversation_id"] is not None:
            break
        time.sleep(0.05)

    assert client.post(f"/api/v1/projects/{project['id']}/evals/runs/{run['id']}/stop").status_code == 204

    stopped = finished_run(client, project["id"], run["id"])
    assert stopped["status"] == "stopped" and stopped["results"][0]["status"] == "stopped"
    assert client.app.state.replies.running(stopped["results"][0]["conversation_id"]) is None
