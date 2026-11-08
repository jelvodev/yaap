"""Scheduled tasks."""

import asyncio
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.agent.runs import RunConfig, RunRegistry
from app.agent.gate import call_tool
from app.agent.tools import tools_for
from app.providers.fake_provider import ScriptedResponse
from app.scheduling.cron import CronError, describe, next_time, parse
from app.scheduling.service import recover_runs
from app.scheduling.service import tick as tick_now
from app.storage.models import Conversation, Goal, ScheduledRun, ScheduledTask
from tests.test_chat import add_key

FRIDAY_NOON = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


def tick(app):
    """One round of the loop in a worker thread of the app's event loop, as the loop runs it."""
    import anyio

    return app.state.portal.call(anyio.to_thread.run_sync, tick_now, app)


def test_cron_in_a_time_zone():
    weekdays = parse("0 7 * * mon-fri")
    assert next_time(weekdays, "Europe/Amsterdam", FRIDAY_NOON) == datetime(2026, 11, 9, 6, 0, tzinfo=UTC)
    assert describe(weekdays, "Europe/Amsterdam") == "Weekdays at 07:00 (Europe/Amsterdam)"
    # Summer time: 07:00 in Amsterdam is 05:00 UTC.
    assert next_time(parse("0 7 * * *"), "Europe/Amsterdam", datetime(2026, 7, 1, 12, tzinfo=UTC)) == datetime(2026, 7, 2, 5, 0, tzinfo=UTC)
    assert next_time(parse("*/15 * * * *"), "UTC", FRIDAY_NOON) == FRIDAY_NOON + timedelta(minutes=15)
    assert next_time(parse("0 9 1 * sun"), "UTC", FRIDAY_NOON) == datetime(2026, 11, 8, 9, 0, tzinfo=UTC)  # Day or weekday.
    for bad in ("* * *", "61 * * * *", "0 7 * * funday", "*/0 * * * *"):
        try:
            parse(bad)
            raise AssertionError(bad)
        except CronError:
            pass


def project(client) -> dict:
    add_key(client)
    return client.post("/api/v1/environments/1/projects", json={"name": "Nightly"}).json()


def task_body(**extra) -> dict:
    return {
        "name": "Check deps",
        "prompt": "Check the dependencies.",
        "model": "claude-opus-5-5",
        "effort": "medium",
        "schedule_kind": "cron",
        "cron": "0 3 * * *",
        "timezone": "Europe/Amsterdam",
        **extra,
    }


def wait_for_runs(client, done: int = 1) -> None:
    for _ in range(200):
        tick(client.app)
        with client.app.state.session_factory() as session:
            if len(session.scalars(select(ScheduledRun).where(ScheduledRun.status != "running")).all()) >= done:
                return
        time.sleep(0.05)
    raise AssertionError("the run did not finish")


def test_tasks_are_saved_with_their_schedule(client):
    created = client.post(f"/api/v1/projects/{project(client)['id']}/tasks", json=task_body())
    assert created.status_code == 201, created.text
    task = created.json()
    assert task["schedule_text"] == "Every day at 03:00 (Europe/Amsterdam)" and len(task["next_runs"]) == 3
    bad = client.post(f"/api/v1/projects/{task['project_id']}/tasks", json=task_body(cron="0 3 * *"))
    assert bad.status_code == 422 and "five fields" in bad.json()["detail"]
    moments = client.post(
        f"/api/v1/projects/{task['project_id']}/tasks",
        json=task_body(schedule_kind="moments", moments=["2030-01-01T09:00+01:00", "2031-01-01T09:00+01:00"]),
    ).json()
    assert moments["next_runs"][0].startswith("2030-01-01T08:00")
    assert client.post(f"/api/v1/projects/{task['project_id']}/tasks", json=task_body(schedule_kind="moments", moments=["2030-01-01T09:00"])).status_code == 422
    manual = client.post(f"/api/v1/projects/{task['project_id']}/tasks", json=task_body(schedule_kind="manual")).json()
    assert manual["schedule_text"] == "On demand" and manual["next_runs"] == []
    paused = client.put(f"/api/v1/projects/{task['project_id']}/tasks/{task['id']}", json=task_body(enabled=False)).json()
    assert paused["next_runs"] == []


def test_a_due_run_happens_once_unattended_and_ends_with_its_goal(client, fake):
    proj = project(client)
    task = client.post(f"/api/v1/projects/{proj['id']}/tasks", json=task_body(max_turns=3)).json()
    with client.app.state.session_factory() as session:
        stored = session.get_one(ScheduledTask, task["id"])
        stored.next_run_at = datetime.now(UTC) - timedelta(hours=5)  # Missed while YAAP was down.
        session.commit()
    fake.responses.extend(
        [
            # The agent tries a command that would ask, then finishes the goal.
            ScriptedResponse(text="Trying.", tool_calls=(("run_command", {"command": "npm outdated"}),)),
            ScriptedResponse(text="Done.", tool_calls=(("finish_goal", {"status": "done", "summary": "All current."}),)),
        ]
    )
    started = tick(client.app)
    assert len(started) == 1
    assert tick(client.app) == []  # No overlap, and the missed runs run once.
    wait_for_runs(client)
    with client.app.state.session_factory() as session:
        run = session.scalars(select(ScheduledRun)).one()
        conversation = session.get_one(Conversation, run.conversation_id)
        task_row = session.get_one(ScheduledTask, task["id"])
        assert run.trigger == "missed" and run.status == "done" and run.summary == "All current."
        assert conversation.scheduled_task_id == task["id"] and conversation.unread
        assert not task_row.running and task_row.next_run_at > datetime.now(UTC)
    messages = client.get(f"/api/v1/conversations/{run.conversation_id}").json()["messages"]
    tool_call = next(block for message in messages for block in message["content"] if block["type"] == "tool_call" and block["name"] == "run_command")
    assert tool_call["output"].startswith("Refused: this is a scheduled run without the user")
    listed = client.get("/api/v1/environments/1/conversations").json()
    item = next(entry for entry in listed if entry["id"] == run.conversation_id)
    assert item["scheduled"] and not item["unread"]  # Opening it marked it seen.
    recent = client.get("/api/v1/environments/1/task-runs").json()
    assert recent[0]["status"] == "done" and recent[0]["task_name"] == "Check deps"


def test_allowed_commands_run_and_one_conversation_is_reused(client, fake):
    proj = project(client)
    body = task_body(schedule_kind="manual", conversation_mode="one", allowed_commands=["echo hello"])
    task = client.post(f"/api/v1/projects/{proj['id']}/tasks", json=body).json()
    fake.responses.extend(
        [
            ScriptedResponse(text="Ran.", tool_calls=(("run_command", {"command": "echo hello"}),)),
            ScriptedResponse(text="Done.", tool_calls=(("finish_goal", {"status": "done", "summary": "Said hello."}),)),
        ]
    )
    first = client.post(f"/api/v1/projects/{proj['id']}/tasks/{task['id']}/run")
    assert first.status_code == 202, first.text
    assert client.post(f"/api/v1/projects/{proj['id']}/tasks/{task['id']}/run").status_code == 409  # Still running.
    wait_for_runs(client)
    messages = client.get(f"/api/v1/conversations/{first.json()['conversation_id']}").json()["messages"]
    output = next(block["output"] for message in messages for block in message["content"] if block.get("name") == "run_command")
    assert output.startswith("Exit code 0") and "hello" in output
    fake.responses.append(ScriptedResponse(text="Again.", tool_calls=(("finish_goal", {"status": "done", "summary": "Again."}),)))
    second = client.post(f"/api/v1/projects/{proj['id']}/tasks/{task['id']}/run").json()
    wait_for_runs(client, 2)
    assert second["conversation_id"] == first.json()["conversation_id"]
    runs = client.get(f"/api/v1/projects/{proj['id']}/tasks/{task['id']}/runs").json()
    assert [run["status"] for run in runs] == ["done", "done"]


def test_an_unattended_run_refuses_what_would_ask(client):
    registry = RunRegistry()
    run = registry.start(
        RunConfig(
            conversation_id=1,
            environment_id=1,
            blueprint_layers=[],
            tool_names=tools_for(False, False),
            unattended=True,
        )
    )
    output, is_error = asyncio.run(call_tool(registry, run, "web_fetch", {"url": "https://example.com"}))
    assert is_error and output.startswith("Refused: this is a scheduled run") and run.events.empty()


def test_a_restart_ends_runs_that_were_going_on(client):
    proj = project(client)
    task = client.post(f"/api/v1/projects/{proj['id']}/tasks", json=task_body(schedule_kind="manual")).json()
    with client.app.state.session_factory() as session:
        conversation = Conversation(environment_id=1, project_id=proj["id"], title="x", provider="anthropic", model="claude-opus-5-5", effort="medium", created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
        session.add(conversation)
        session.flush()
        goal = Goal(conversation_id=conversation.id, objective="x", status="active", max_turns=3, max_cost=1, turns=1, created_at=datetime.now(UTC))
        session.add(goal)
        session.flush()
        session.add(ScheduledRun(task_id=task["id"], conversation_id=conversation.id, goal_id=goal.id, trigger="manual", status="running", started_at=datetime.now(UTC)))
        session.get_one(ScheduledTask, task["id"]).running = True
        session.commit()
    assert recover_runs(client.app) == 1
    with client.app.state.session_factory() as session:
        assert session.scalars(select(ScheduledRun)).one().status == "stopped"
        assert not session.get_one(ScheduledTask, task["id"]).running
        assert session.scalars(select(Goal)).one().status == "stopped"
