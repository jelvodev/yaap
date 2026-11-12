"""Background tasks the user switched off: they are decided by a pure function, validated as a setting, and skipped
before any model is asked."""

import asyncio

import pytest
from sqlalchemy import func, select

from app.core.utility import SWITCHED_OFF_REASON, UtilityContext, UtilitySkipped, run_utility_or_skip
from app.core.utility_tasks import SWITCHABLE_TASKS, check_tasks_off, task_enabled
from app.providers.fake_provider import ScriptedResponse
from app.storage.models import AuditRecord
from tests.test_chat import add_key


@pytest.mark.parametrize(
    ("task", "off", "expected"),
    [
        (None, ["title"], True),
        ("title", [], True),
        ("title", ["title"], False),
        ("title", ["route"], True),
        ("memory_suggestion", ["memory_suggestion", "title"], False),
        ("injection_check", ["injection_check"], True),
        ("commit_message", ["commit_message"], True),
        ("pull_request", ["pull_request"], True),
        ("rule", ["rule"], True),
        ("summary", ["summary"], True),
        ("eval_rubric", ["eval_rubric"], True),
        ("title_and_memory", [], True),
        ("title_and_memory", ["title"], False),
        ("title_and_memory", ["memory_suggestion"], False),
        ("title_and_memory", ["route"], True),
    ],
)
def test_task_enabled_follows_the_switch_only_for_switchable_tasks(task, off, expected):
    assert task_enabled(task, off) is expected


def test_every_switchable_task_can_be_switched_off():
    assert all(not task_enabled(task, [task]) for task in SWITCHABLE_TASKS)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ([], None),
        (["title", "route"], None),
        (["injection_check"], "The check of outside content cannot be switched off."),
        (["commit_message"], "Unknown or not switchable background task: commit_message."),
        (["nonsense"], "Unknown or not switchable background task: nonsense."),
        ("title", "Give a list of background tasks."),
        ([1], "Give a list of background tasks."),
    ],
)
def test_check_tasks_off_accepts_only_switchable_tasks(value, message):
    assert check_tasks_off(value, None) == message  # type: ignore[arg-type] # the check does not use providers


def test_switched_off_task_is_skipped_without_a_model_call_or_audit_row(client, fake):
    add_key(client)
    response = client.put("/api/v1/environments/1/settings/utility_tasks_off", json={"value": ["title"]})
    assert response.status_code == 200
    fake.responses.append(ScriptedResponse(text="A title"))
    state = client.app.state
    context = UtilityContext(state.session_factory, state.cipher, state.credential_types, state.providers)

    skipped = asyncio.run(run_utility_or_skip(context, 1, "Write a title", task="title"))
    answered = asyncio.run(run_utility_or_skip(context, 1, "Write a title", task="rule"))

    assert skipped == UtilitySkipped("title", SWITCHED_OFF_REASON)
    assert answered == "A title"
    assert len(fake.requests) == 1
    with state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AuditRecord)) == 1


def test_setting_rejects_the_outside_content_check(client):
    response = client.put("/api/v1/environments/1/settings/utility_tasks_off", json={"value": ["injection_check"]})
    assert response.status_code == 422
