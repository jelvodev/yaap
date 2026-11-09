"""The first-start wizard: its status from the facts and the stored progress, and its endpoints."""

import asyncio

import pytest

from app.core.first_run import (
    STEPS,
    FirstRunFacts,
    FirstRunStored,
    SignInCache,
    first_run_status,
    parse_stored,
)
from app.providers.fake_provider import FakeProvider
from app.providers.registry import ProviderRegistry, ProviderSpec

from tests.conftest import VALID_KEY

NOTHING = FirstRunFacts(connection=False, project=False, backups=False, starter=False)


@pytest.mark.parametrize(
    ("facts", "stored", "steps", "show"),
    [
        # A fresh installation is sent to the wizard.
        (NOTHING, FirstRunStored(), ("todo", "todo", "todo", "todo"), True),
        # One that already has a connection or a project is not, unless the wizard was started.
        (
            FirstRunFacts(connection=True, project=False, backups=False, starter=False),
            FirstRunStored(),
            ("done", "todo", "todo", "todo"),
            False,
        ),
        (
            FirstRunFacts(connection=False, project=True, backups=False, starter=False),
            FirstRunStored(),
            ("todo", "done", "todo", "todo"),
            False,
        ),
        # Backups and the starter alone do not make an installation set up.
        (
            FirstRunFacts(connection=False, project=False, backups=True, starter=True),
            FirstRunStored(),
            ("todo", "todo", "done", "done"),
            True,
        ),
        # Started and left halfway: it comes back.
        (
            FirstRunFacts(connection=True, project=True, backups=False, starter=False),
            FirstRunStored(started=True),
            ("done", "done", "todo", "todo"),
            True,
        ),
        # Skipped steps show as skipped; a step done later is done, skipped or not.
        (
            FirstRunFacts(connection=True, project=False, backups=False, starter=True),
            FirstRunStored(started=True, skipped=frozenset({"connection", "backups", "starter"})),
            ("done", "todo", "skipped", "done"),
            True,
        ),
        # Finished: never shown again, also on a fresh installation.
        (NOTHING, FirstRunStored(started=True, finished=True), ("todo", "todo", "todo", "todo"), False),
        (NOTHING, FirstRunStored(finished=True), ("todo", "todo", "todo", "todo"), False),
    ],
)
def test_status_follows_the_facts_and_the_stored_progress(
    facts: FirstRunFacts, stored: FirstRunStored, steps: tuple[str, ...], show: bool
) -> None:
    status = first_run_status(facts, stored)
    assert tuple(status.steps[step] for step in STEPS) == steps
    assert status.show is show
    assert (status.started, status.finished) == (stored.started, stored.finished)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, FirstRunStored()),
        ("yes", FirstRunStored()),
        (
            {"started": True, "finished": False, "skipped": ["backups"]},
            FirstRunStored(True, False, frozenset({"backups"})),
        ),
        # Unknown steps and values that are not booleans are ignored.
        (
            {"started": "true", "finished": 1, "skipped": ["backups", "other"]},
            FirstRunStored(skipped=frozenset({"backups"})),
        ),
        ({"skipped": "backups"}, FirstRunStored()),
    ],
)
def test_the_stored_progress_ignores_what_does_not_fit(value: object, expected: FirstRunStored) -> None:
    assert parse_stored(value) == expected


def subscription_registry(answers: list[bool]) -> tuple[ProviderRegistry, list[int]]:
    calls: list[int] = []

    async def signed_in() -> bool:
        calls.append(1)
        return answers[min(len(calls), len(answers)) - 1]

    spec = ProviderSpec(
        "anthropic", "Claude Code", None, lambda _key: FakeProvider("m", [], []), "claude_code", signed_in=signed_in
    )
    return ProviderRegistry([spec], []), calls


def test_a_subscription_sign_in_is_checked_now_and_then_and_remembered_once_found() -> None:
    providers, calls = subscription_registry([False, True])
    cache = SignInCache()
    assert asyncio.run(cache.read(providers, now=100.0)) is False
    # Within the recheck time the CLI is not started again.
    assert asyncio.run(cache.read(providers, now=110.0)) is False
    assert len(calls) == 1
    assert asyncio.run(cache.read(providers, now=200.0)) is True
    assert asyncio.run(cache.read(providers, now=1000.0)) is True
    assert len(calls) == 2


def steps(body: dict) -> tuple[str, ...]:
    return tuple(body["steps"][step] for step in STEPS)


def test_start_skip_finish_and_restart(client) -> None:
    status = client.get("/api/v1/first-run")
    assert status.status_code == 200, status.text
    assert status.json() == {
        "show": True,
        "started": False,
        "finished": False,
        "steps": {"connection": "todo", "project": "todo", "backups": "todo", "starter": "todo"},
    }

    started = client.post("/api/v1/first-run/start").json()
    assert started["started"] is True and started["show"] is True

    skipped = client.post("/api/v1/first-run/skip/backups").json()
    assert steps(skipped) == ("todo", "todo", "skipped", "todo")
    assert client.post("/api/v1/first-run/skip/elsewhere").status_code == 422

    finished = client.post("/api/v1/first-run/finish").json()
    assert finished["finished"] is True and finished["show"] is False
    assert steps(finished) == ("todo", "todo", "skipped", "todo")
    assert client.get("/api/v1/first-run").json()["show"] is False

    restarted = client.post("/api/v1/first-run/restart").json()
    assert restarted["started"] is True and restarted["finished"] is False and restarted["show"] is True
    assert steps(restarted) == ("todo", "todo", "todo", "todo")

    # The progress is not a setting Settings shows or sets.
    assert "first_run" not in {setting["key"] for setting in client.get("/api/v1/settings").json()}
    assert client.put("/api/v1/settings/first_run", json={"value": {"finished": True}}).status_code == 404


def test_steps_are_done_by_what_the_installation_has(client) -> None:
    saved = client.put("/api/v1/environments/1/credentials/anthropic_api_key", json={"value": VALID_KEY})
    assert saved.status_code == 200, saved.text
    body = client.get("/api/v1/first-run").json()
    # A connection means the installation is set up: it is not sent to the wizard.
    assert steps(body) == ("done", "todo", "todo", "todo") and body["show"] is False

    assert client.post("/api/v1/environments/1/projects", json={"name": "Site"}).status_code in (200, 201)
    # The schedule is on by default; only saving it counts as a choice.
    assert client.put("/api/v1/settings/backup_schedule", json={"value": {"enabled": False}}).status_code == 200
    assert steps(client.get("/api/v1/first-run").json()) == ("done", "done", "todo", "todo")
    assert client.put("/api/v1/settings/backup_schedule", json={"value": {"enabled": True}}).status_code == 200
    assert steps(client.get("/api/v1/first-run").json()) == ("done", "done", "done", "todo")

