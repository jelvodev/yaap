"""The git panel asks for the runner's runtime per request; building one reads the SSH key from the vault, which is
logged as a use. The runtime is kept for a few minutes while the runner stays the same."""

from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.settings import SETTING_DEFINITIONS, store_setting
from app.runner import service as runner_service
from app.runner.service import GIT_RUNTIME_MAX_AGE, Runners
from app.storage.models import Target


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def runners(client):
    clock = FakeClock()
    bound = Runners(enabled=True, clock=clock)
    bound.bind(client.app.state.session_factory, client.app.state.cipher, Path(client.app.state.workspace_dir))
    bound.enabled = True
    bound.fake_clock = clock
    return bound


@pytest.fixture
def builds(monkeypatch):
    """How often the key was read from the vault (every read is a logged use)."""
    reads: list[str] = []
    original = runner_service.settings_for

    def counting(*args, **kwargs):
        reads.append(args[3])
        return original(*args, **kwargs)

    monkeypatch.setattr(runner_service, "settings_for", counting)
    return reads


def test_two_calls_share_one_runtime_and_one_vault_use(client, runners, builds):
    first = runners.git_runtime(1)
    second = runners.git_runtime(1)
    assert second is first
    assert builds == ["git"]


@pytest.mark.parametrize("change", ["network", "host_key", "forget", "forget_all", "age"])
def test_the_runtime_is_built_again_when_the_runner_changes_or_is_forgotten(client, runners, builds, change):
    first = runners.git_runtime(1)
    if change == "network":
        with client.app.state.session_factory() as session:
            store_setting(session, SETTING_DEFINITIONS["runner_network"], 1, "off")
    elif change == "host_key":
        with client.app.state.session_factory() as session:
            found = session.scalars(select(Target).where(Target.kind == "runner")).one()
            found.host_key = "ssh-ed25519 AAAA-other"
            session.commit()
    elif change == "forget":
        runners.forget(1)
    elif change == "forget_all":
        runners.forget()
    else:
        runners.fake_clock.now += GIT_RUNTIME_MAX_AGE + 1
    second = runners.git_runtime(1)
    assert second is not first
    assert len(builds) == 2


def test_the_runtime_is_kept_until_it_is_five_minutes_old(client, runners, builds):
    first = runners.git_runtime(1)
    runners.fake_clock.now += GIT_RUNTIME_MAX_AGE - 1
    assert runners.git_runtime(1) is first
    assert builds == ["git"]
