"""Reply resources are released even when saving reply data fails."""

import logging

import pytest
from sqlalchemy.exc import OperationalError

from app.agent.runs import RunRegistry
from app.core import chat
from app.providers.fake_provider import FakeProvider, ScriptedResponse
from tests.test_chat import add_key, start_conversation


@pytest.mark.parametrize("failure", ["reply", "usage", "run_record"])
def test_reply_cleanup_survives_recording_failures(client, fake, monkeypatch, caplog, failure: str) -> None:
    add_key(client)
    conversation_id = start_conversation(client)
    fake.responses.append(ScriptedResponse(text="Partial", error="Provider stopped."))
    closed: list[bool] = []
    tokens: list[str] = []
    original_finish = RunRegistry.finish

    async def close_provider(self: FakeProvider) -> None:
        closed.append(True)

    def finish_run(registry: RunRegistry, run) -> None:
        tokens.append(run.token)
        original_finish(registry, run)

    def fail(*_args, **_kwargs):
        raise OperationalError("injected storage failure", {}, RuntimeError("database unavailable"))

    monkeypatch.setattr(FakeProvider, "close", close_provider)
    monkeypatch.setattr(RunRegistry, "finish", finish_run)
    target = {
        "reply": "store_assistant_reply",
        "usage": "store_unfinished_usage",
        "run_record": "finish_run_record",
    }[failure]
    monkeypatch.setattr(chat, target, fail)
    caplog.set_level(logging.WARNING, logger="app.core.chat")

    response = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Hello"})

    assert response.status_code == 200
    assert closed == [True]
    assert len(tokens) == 1
    assert client.app.state.runs.find(tokens[0]) is None
    assert client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        headers={"Authorization": f"Bearer {tokens[0]}"},
    ).status_code == 401
    assert "injected storage failure" in caplog.text
