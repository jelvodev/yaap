"""The safety net that rewrites the Current state block when the agent did not."""

import asyncio
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.projects import state_refresh
from app.projects.current_state import COMMIT_PREFIX, read_state
from app.projects.memory import read_memory
from app.projects.state_refresh import (
    RefreshSource,
    StateRefreshContext,
    clean_answer,
    plan_event_listener,
    refresh_due,
    refresh_state,
    refresh_unless_written,
    save_state,
    state_updated_at,
)
from app.providers.fake_provider import ScriptedResponse
from tests.test_chat import add_key
from tests.test_conversation_continuation import utility_context

OLD = "**Where it stands:** Login works.\n\n**Open:** Reset.\n\n**Next step:** Mail."
NEW = "**Where it stands:** Reset works.\n\n**Open:** Nothing.\n\n**Next step:** Deploy."
USER_TEXT = "**Next step:** The user's own words."


@pytest.mark.parametrize(
    ("ended", "messages", "quiet", "agent_wrote", "expected"),
    [
        (True, 0, 0, False, True),
        (True, 50, 60, True, False),
        (False, 8, 10, False, True),
        (False, 7, 10, False, False),
        (False, 8, 9.9, False, False),
        (False, 30, 600, False, True),
        (False, 30, 600, True, False),
        (False, 0, 0, False, False),
    ],
    ids=[
        "after_a_goal_or_build", "agent_wrote_after_a_goal", "eight_messages_ten_minutes", "seven_messages",
        "not_quiet_long_enough", "long_and_quiet", "agent_wrote_when_quiet", "nothing_happened",
    ],
)
def test_refresh_is_due_only_when_the_agent_did_not_write(ended, messages, quiet, agent_wrote, expected):
    assert refresh_due(ended, messages, quiet, agent_wrote) is expected


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (NEW, NEW),
        (f"```markdown\n{NEW}\n```", NEW),
        (f"## Current state\n\n{NEW}", NEW),
        (f"```\n## Current state\n{NEW}\n```", NEW),
        (f"  {NEW}\n\n", NEW),
    ],
    ids=["plain", "fenced", "with_heading", "fenced_with_heading", "padding"],
)
def test_clean_answer_removes_a_fence_and_a_repeated_heading(answer, expected):
    assert clean_answer(answer) == expected


def project_with_state(client) -> tuple[dict, Path]:
    add_key(client)
    project = client.post("/api/v1/environments/1/projects", json={"name": "Shop"}).json()
    folder = Path(client.app.state.workspace_dir) / project["folder"]
    save_state(folder, 1, OLD, "written by the agent")
    return project, folder


def context_of(client) -> StateRefreshContext:
    return StateRefreshContext(utility_context(client), Path(client.app.state.workspace_dir))


def refresh(client, project: dict) -> bool:
    return asyncio.run(refresh_state(context_of(client), RefreshSource(project["id"], "A goal ended.")))


def last_commit(folder: Path) -> tuple[str, list[str]]:
    out = subprocess.run(
        ["git", "log", "-1", "--name-only", "--format=%s"], cwd=folder, capture_output=True, text=True, check=True
    ).stdout.split("\n")
    return out[0], [line for line in out[1:] if line]


def test_a_refresh_is_one_commit_that_touches_only_the_memory_file(client, fake):
    project, folder = project_with_state(client)
    (folder / "draft.txt").write_text("not mine to commit\n", encoding="utf-8")
    fake.responses.append(ScriptedResponse(text=NEW))

    assert refresh(client, project) is True

    subject, files = last_commit(folder)
    assert subject == f"{COMMIT_PREFIX}refreshed" and files == [".blueprint/memory.md"]
    assert read_state(read_memory(folder)) == NEW
    history = subprocess.run(["git", "log", "--name-only", "--format="], cwd=folder, capture_output=True, text=True)
    assert "draft.txt" not in history.stdout


def test_the_refresh_prompt_holds_the_old_block_and_what_happened(client, fake):
    project, _ = project_with_state(client)
    fake.responses.append(ScriptedResponse(text=NEW))

    refresh(client, project)

    prompt = str(fake.requests[-1].messages[-1])
    assert "<current_state>" in prompt and "Login works." in prompt and "A goal ended." in prompt


@pytest.mark.parametrize(
    "answer",
    [" ", "x" * 2000, "**Open:** a\n## Decisions\nb"],
    ids=["empty", "over_budget", "heading_line"],
)
def test_an_invalid_answer_keeps_the_old_block(client, fake, answer):
    project, folder = project_with_state(client)
    before = read_memory(folder)
    fake.responses.append(ScriptedResponse(text=answer))

    assert refresh(client, project) is False

    assert read_memory(folder) == before
    assert last_commit(folder)[0] == f"{COMMIT_PREFIX}written by the agent"


def test_an_edit_made_while_the_model_wrote_wins_over_the_refresh(client, monkeypatch):
    project, folder = project_with_state(client)

    async def edited_meanwhile(*args, **kwargs):
        save_state(folder, 1, USER_TEXT, "edited by you")
        return NEW

    monkeypatch.setattr(state_refresh, "run_utility", edited_meanwhile)

    assert refresh(client, project) is False

    assert read_state(read_memory(folder)) == USER_TEXT
    assert last_commit(folder)[0] == f"{COMMIT_PREFIX}edited by you"


def test_a_project_without_a_repository_is_not_refreshed(client, fake, monkeypatch):
    project, _ = project_with_state(client)
    fake.responses.append(ScriptedResponse(text=NEW))
    monkeypatch.setattr(state_refresh, "has_repository", lambda _folder: False)

    assert refresh(client, project) is False
    assert fake.requests == []


def test_the_commit_time_of_the_block_is_found_in_the_history(client):
    _, folder = project_with_state(client)

    stamp = state_updated_at(folder, 1)

    assert stamp is not None and abs(datetime.now(UTC) - stamp) < timedelta(minutes=5)
    other = client.post("/api/v1/environments/1/projects", json={"name": "Empty"}).json()
    assert state_updated_at(Path(client.app.state.workspace_dir) / other["folder"], 1) is None


@pytest.mark.parametrize(
    ("event", "has_source", "started"),
    [
        ("all_steps_done", True, True),
        ("step_failed", True, True),
        ("step_started", True, False),
        ("all_steps_done", False, False),
    ],
    ids=["build_done", "build_failed", "other_event", "no_source"],
)
def test_the_build_listener_starts_a_refresh_only_when_a_build_ends(client, event, has_source, started):
    project, _ = project_with_state(client)
    source = RefreshSource(project["id"], "A build ended.")
    starts: list[object] = []

    def start(coroutine):
        starts.append(coroutine)
        coroutine.close()

    listener = plan_event_listener(context_of(client), lambda _plan: source if has_source else None, start)
    listener(1, event)

    assert bool(starts) is started


def test_a_build_refresh_is_skipped_when_the_agent_wrote_since_the_build_started(client, fake):
    project, _ = project_with_state(client)
    fake.responses.append(ScriptedResponse(text=NEW))
    earlier_ = datetime.now(UTC) - timedelta(hours=1)
    later_ = datetime.now(UTC) + timedelta(hours=1)
    context = context_of(client)

    skipped = asyncio.run(
        refresh_unless_written(context, RefreshSource(project["id"], "A build ended.", None, earlier_))
    )
    ran = asyncio.run(
        refresh_unless_written(context, RefreshSource(project["id"], "A build ended.", None, later_))
    )

    assert (skipped, ran) == (False, True)
