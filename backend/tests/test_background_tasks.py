"""The data behind Settings › Background tasks and the Blueprint's built-in items: which task is switchable and who
starts it, what it ran on and cost this month, and who replaced a built-in item."""

from datetime import UTC, datetime

import pytest

from app.blueprint.builtin import builtin_items
from app.core.background_tasks import builtin_item_for_task, listed_tasks, recorded_tasks, started_by
from app.core.scheduled_work import describe_cron, describe_interval, idle_after
from app.storage.models import UsageRecord

BASE = "/api/v1/environments/1"


@pytest.mark.parametrize(
    ("task", "starter"),
    [
        ("title", "yaap"),
        ("memory_suggestion", "yaap"),
        ("route", "yaap"),
        ("injection_check", "yaap"),
        ("summary", "yaap"),
        ("eval_rubric", "yaap"),
        ("rule", "user"),
        ("import", "user"),
        ("eval_case", "user"),
        ("commit_message", "user"),
        ("pull_request", "user"),
    ],
)
def test_started_by_names_the_tasks_the_user_starts(task, starter):
    assert started_by(task) == starter


def test_combined_task_has_no_row_and_counts_toward_memory_suggestions():
    assert "title_and_memory" not in listed_tasks()
    assert recorded_tasks("memory_suggestion") == ["memory_suggestion", "title_and_memory"]
    assert recorded_tasks("title") == ["title"]


@pytest.mark.parametrize(("task", "item"), [("route", "router"), ("injection_check", "guard"), ("eval_rubric", None)])
def test_builtin_item_for_task_follows_the_tasks_of_the_shipped_items(task, item):
    shipped = builtin_items()
    expected = item if item in shipped else None
    assert builtin_item_for_task(task) == expected


@pytest.mark.parametrize(
    ("cron", "words"),
    [
        ("0 3 * * *", "Every day at 03:00"),
        ("30 9 * * *", "Every day at 09:30"),
        ("*/5 * * * *", "On the schedule */5 * * * *"),
    ],
)
def test_describe_cron_words_a_daily_time(cron, words):
    assert describe_cron(cron) == words


@pytest.mark.parametrize(
    ("seconds", "words"),
    [(60, "Every minute"), (900, "Every 15 minutes"), (3600, "Every hour"), (7200, "Every 2 hours")],
)
def test_describe_interval(seconds, words):
    assert describe_interval(seconds) == words


@pytest.mark.parametrize(
    ("seconds", "words"),
    [(600, "After 10 minutes without use"), (3600, "After 1 hour without use"), (7200, "After 2 hours without use")],
)
def test_idle_after(seconds, words):
    assert idle_after(seconds) == words


def add_usage(client, task, cost, summary=None):
    with client.app.state.session_factory() as session:
        session.add(
            UsageRecord(
                environment_id=1, provider="anthropic", model="claude-haiku-5-5", purpose="utility", task=task,
                summary=summary, cost=cost, created_at=datetime.now(UTC),
            )
        )
        session.commit()


def test_background_tasks_list_runs_cost_and_switch(client):
    add_usage(client, "title", 0.001, "A title")
    add_usage(client, "title_and_memory", 0.002)
    add_usage(client, "memory_suggestion", 0.003)
    assert client.put(f"{BASE}/settings/utility_tasks_off", json={"value": ["title"]}).status_code == 200

    body = client.get(f"{BASE}/background-tasks").json()
    tasks = {row["task"]: row for row in body["tasks"]}

    assert "title_and_memory" not in tasks
    assert tasks["title"]["runs"] == 1
    assert tasks["title"]["cost"] == pytest.approx(0.001)
    assert tasks["title"]["enabled"] is False
    assert tasks["title"]["switchable"] is True
    assert tasks["title"]["last_runs"][0]["summary"] == "A title"
    assert tasks["memory_suggestion"]["runs"] == 2
    assert tasks["memory_suggestion"]["cost"] == pytest.approx(0.005)
    assert tasks["memory_suggestion"]["enabled"] is True
    assert tasks["injection_check"]["switchable"] is False
    assert tasks["commit_message"]["started_by"] == "user"
    assert tasks["eval_rubric"]["model"] is None
    assert tasks["title"]["model"]
    assert {work["name"] for work in body["scheduled"]} >= {"Backups", "Update check", "Scheduled tasks"}
    backups = next(work for work in body["scheduled"] if work["name"] == "Backups")
    assert backups["setting_key"] == "backup_schedule"
    assert backups["schedule"] == "Every day at 03:00"


def test_background_tasks_of_an_unknown_environment_are_not_found(client):
    assert client.get("/api/v1/environments/999/background-tasks").status_code == 404
    assert client.get("/api/v1/environments/999/blueprint/builtin").status_code == 404


def test_builtin_items_say_what_can_be_replaced_and_what_they_cost(client):
    add_usage(client, "injection_check", 0.004)
    add_usage(client, "route", 0.002)

    items = {item["name"]: item for item in client.get(f"{BASE}/blueprint/builtin").json()["items"]}

    assert items["guard"]["can_override"] is False
    assert items["guard"]["overridden_by"] is None
    assert items["guard"]["runs"] == 1
    assert items["guard"]["cost"] == pytest.approx(0.004)
    assert items["router"]["can_override"] is True
    assert items["router"]["runs"] == 1
    assert items["worker"]["runs"] is None
    assert items["worker"]["model"] is None
    assert items["worker"]["model_note"] == "The preset's worker model"
    assert items["worker"]["instructions"]
