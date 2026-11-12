import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from app.providers.base import (
    AgentRequest,
    Completed,
    HistoryMessage,
    ProviderError,
    ReasoningDelta,
    SystemPart,
    TextDelta,
    TokenUsage,
)
from app.providers import claude_code_provider
from app.providers.claude_code_provider import ClaudeCodeProvider, plan_turn, read_claude_code_status
from app.providers.fake_provider import ScriptedResponse
from tests.conftest import parse_events, pass_to_cli
from tests.test_tracing import traced_events

# Stands in for the `claude` binary: records its arguments and stdin, then prints the scripted stream-json lines.
FAKE_CLAUDE = """
import json, os, sys
log = {"argv": sys.argv[1:], "stdin": sys.stdin.read(), "cwd": os.getcwd()}
system_file = sys.argv[sys.argv.index("--system-prompt-file") + 1] if "--system-prompt-file" in sys.argv else None
if system_file:
    log["system"] = open(system_file, encoding="utf-8").read()
with open(os.environ["FAKE_CLAUDE_LOG"], "a", encoding="utf-8") as file:
    file.write(json.dumps(log) + "\\n")
if os.environ.get("FAKE_CLAUDE_REFUSE") and os.environ["FAKE_CLAUDE_REFUSE"] in sys.argv:
    sys.stderr.write("error: unknown option '" + os.environ["FAKE_CLAUDE_REFUSE"] + "'")
    sys.exit(1)
with open(os.environ["FAKE_CLAUDE_OUTPUT"], encoding="utf-8") as file:
    sys.stdout.write(file.read())
sys.exit(int(os.environ.get("FAKE_CLAUDE_EXIT", "0")))
"""


def user(text: str) -> HistoryMessage:
    return HistoryMessage(role="user", content=[{"type": "text", "text": text}])


def reply(text: str, session_id: str | None) -> HistoryMessage:
    raw = [{"type": "claude_code_session", "session_id": session_id}] if session_id else [{"type": "text", "text": text}]
    return HistoryMessage(role="assistant", content=[{"type": "text", "text": text}], provider_raw=raw)


def stream_event(delta: dict) -> dict:
    return {"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": delta}, "parent_tool_use_id": None}


SUCCESS_LINES = [
    {"type": "system", "subtype": "init", "session_id": "new-session", "model": "claude-opus-5-5"},
    stream_event({"type": "thinking_delta", "thinking": "The user greets."}),
    stream_event({"type": "text_delta", "text": "Hallo "}),
    stream_event({"type": "text_delta", "text": "Alex"}),
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "Hallo Alex"}]}, "parent_tool_use_id": None},
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "Hallo Alex",
        "session_id": "new-session",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 12, "output_tokens": 7, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 50},
        "modelUsage": {"claude-opus-5-5": {}},
    },
]


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE_CLAUDE, encoding="utf-8")
    log = tmp_path / "log.json"
    output = tmp_path / "output.jsonl"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("FAKE_CLAUDE_OUTPUT", str(output))
    pass_to_cli(monkeypatch, claude_code_provider, "FAKE_CLAUDE_LOG", "FAKE_CLAUDE_OUTPUT", "FAKE_CLAUDE_EXIT", "FAKE_CLAUDE_REFUSE")

    class FakeClaude:
        command = [sys.executable, str(script)]

        def set_output(self, lines: list[dict], exit_code: int = 0) -> None:
            output.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
            monkeypatch.setenv("FAKE_CLAUDE_EXIT", str(exit_code))

        def logs(self) -> list[dict]:
            """One entry per time the binary was started."""
            return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

        def log(self) -> dict:
            return self.logs()[-1]

    monkeypatch.setattr(ClaudeCodeProvider, "thinking_display_supported", True)
    return FakeClaude()


def run(provider: ClaudeCodeProvider, messages: list[HistoryMessage]) -> list:
    request = AgentRequest(
        model="claude-opus-5-5", effort="high", system=[SystemPart("Base rules.")], messages=messages
    )

    async def collect() -> list:
        events = []
        try:
            async for event in provider.stream(request):
                events.append(event)
        finally:
            await provider.close()
        return events

    return asyncio.run(collect())


def test_first_turn_starts_a_new_session():
    turn = plan_turn([user("Hallo")])
    assert turn.resume_session_id is None
    assert turn.prompt == "Hallo"


def test_next_turn_resumes_the_session_of_the_latest_reply():
    turn = plan_turn([user("Hallo"), reply("Hoi", "session-1"), user("Hoe gaat het?")])
    assert turn.resume_session_id == "session-1"
    assert turn.prompt == "Hoe gaat het?"


def test_replies_without_a_session_are_passed_as_a_transcript():
    turn = plan_turn([user("Hallo"), reply("Hoi via API", None), user("En nu?")])
    assert turn.resume_session_id is None
    assert "<assistant>\nHoi via API\n</assistant>" in turn.prompt
    assert turn.prompt.endswith("En nu?")


def test_stream_maps_deltas_usage_and_session(fake_claude):
    fake_claude.set_output(SUCCESS_LINES)
    provider = ClaudeCodeProvider(fake_claude.command)

    events = run(provider, [user("Hallo"), reply("Hoi", "session-1"), user("Hoe heet ik?")])

    assert [event.text for event in events if isinstance(event, TextDelta)] == ["Hallo ", "Alex"]
    assert [event.text for event in events if isinstance(event, ReasoningDelta)] == ["The user greets."]
    completed = events[-1]
    assert isinstance(completed, Completed)
    assert completed.billing == "subscription"
    assert completed.provider_raw == [{"type": "claude_code_session", "session_id": "new-session"}]
    assert completed.usage == TokenUsage(input_tokens=12, output_tokens=7, cache_read_tokens=100, cache_write_tokens=50)

    log = fake_claude.log()
    arguments = log["argv"]
    assert json.loads(log["stdin"])["message"]["content"] == [{"type": "text", "text": "Hoe heet ik?"}]
    assert log["system"] == "Base rules."
    assert arguments[arguments.index("--model") + 1] == "claude-opus-5-5"
    assert arguments[arguments.index("--effort") + 1] == "high"
    assert arguments[arguments.index("--tools") + 1] == ""
    assert arguments[arguments.index("--resume") + 1] == "session-1"
    assert "--fork-session" in arguments and "--disable-slash-commands" in arguments
    assert Path(log["cwd"]).name == "yaap-workspace"
    # The temporary system prompt file is removed after the run.
    assert not Path(arguments[arguments.index("--system-prompt-file") + 1]).exists()


def test_thinking_streams_as_summaries(fake_claude):
    fake_claude.set_output(SUCCESS_LINES)
    run(ClaudeCodeProvider(fake_claude.command), [user("Hallo")])
    arguments = fake_claude.log()["argv"]
    assert arguments[arguments.index("--thinking-display") + 1] == "summarized"


def test_a_version_without_thinking_summaries_is_retried_without_them(fake_claude, monkeypatch):
    fake_claude.set_output(SUCCESS_LINES)
    monkeypatch.setenv("FAKE_CLAUDE_REFUSE", "--thinking-display")

    events = run(ClaudeCodeProvider(fake_claude.command), [user("Hallo")])

    assert isinstance(events[-1], Completed)
    first, second = fake_claude.logs()
    assert "--thinking-display" in first["argv"] and "--thinking-display" not in second["argv"]
    # Later runs no longer pass the flag.
    run(ClaudeCodeProvider(fake_claude.command), [user("Nog een")])
    assert "--thinking-display" not in fake_claude.log()["argv"]


def test_signed_out_claude_code_gives_the_sign_in_command(fake_claude):
    fake_claude.set_output(
        [
            {"type": "assistant", "message": {"content": []}, "error": "authentication_failed", "parent_tool_use_id": None},
            {"type": "result", "subtype": "success", "is_error": True, "result": "Not logged in · Please run /login"},
        ],
        exit_code=1,
    )
    with pytest.raises(ProviderError, match="not signed in.*claude auth login"):
        run(ClaudeCodeProvider(fake_claude.command), [user("Hallo")])


def test_exit_without_result_is_an_error(fake_claude):
    fake_claude.set_output([], exit_code=3)
    with pytest.raises(ProviderError, match="exit code 3"):
        run(ClaudeCodeProvider(fake_claude.command), [user("Hallo")])


def test_missing_binary_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    with pytest.raises(ProviderError, match="not installed"):
        run(ClaudeCodeProvider([str(tmp_path / "no-such-claude")]), [user("Hallo")])
    status = asyncio.run(read_claude_code_status([str(tmp_path / "no-such-claude")]))
    assert status.installed is False and status.logged_in is False


def test_claude_code_connection_needs_no_key_and_is_not_priced(client, fake):
    client.put("/api/v1/settings/anthropic_connection", json={"value": "claude_code"})
    conversation_id = client.post(
        "/api/v1/environments/1/conversations", json={"model": "claude-opus-5-5", "effort": "medium"}
    ).json()["id"]
    fake.responses.append(ScriptedResponse(text="Hoi", usage=TokenUsage(input_tokens=1000, output_tokens=100)))

    response = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Hallo"})

    assert response.status_code == 200
    assert parse_events(response.text)[-1][1]["message"]["status"] == "complete"
    assert fake.keys_used == ["<claude code>"]
    usage = client.get("/api/v1/environments/1/usage").json()
    assert usage["cost"] == 0
    assert usage["subscription_records"] == 1
    assert usage["unpriced_records"] == 0


def test_environment_can_override_the_connection(client, fake):
    client.put("/api/v1/settings/anthropic_connection", json={"value": "claude_code"})
    override = client.put("/api/v1/environments/2/settings/anthropic_connection", json={"value": "api_key"})
    assert override.json()["source"] == "environment"

    conversation_id = client.post(
        "/api/v1/environments/2/conversations", json={"model": "claude-opus-5-5", "effort": "medium"}
    ).json()["id"]
    response = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Hallo"})
    assert response.status_code == 409
    assert "Add one in Settings" in response.json()["detail"]


def test_connection_accepts_only_its_options(client):
    rejected = client.put("/api/v1/settings/anthropic_connection", json={"value": "carrier-pigeon"})
    assert rejected.status_code == 422
    assert "Claude Code (subscription)" in rejected.json()["detail"]
    setting = next(item for item in client.get("/api/v1/settings").json() if item["key"] == "anthropic_connection")
    assert [option["value"] for option in setting["options"]] == ["claude_code", "api_key"]


def test_a_claude_code_reply_records_what_the_cli_reports(fake_claude):
    result = {**SUCCESS_LINES[-1], "num_turns": 3}
    fake_claude.set_output([*SUCCESS_LINES[:-1], result])
    request = AgentRequest(
        model="claude-opus-5-5", effort="high", system=[SystemPart("Base rules.")], messages=[user("Hallo")]
    )

    events, recorder = asyncio.run(traced_events(ClaudeCodeProvider(fake_claude.command), request))

    assert isinstance(events[-1], Completed)
    ((run_id, step),) = recorder.steps
    assert run_id == 7 and step.position == 1
    assert step.model == "claude-opus-5-5" and step.model_calls == 3
    reported = TokenUsage(input_tokens=12, output_tokens=7, cache_read_tokens=100, cache_write_tokens=50)
    assert step.usage == events[-1].usage == reported


def test_claude_code_in_a_project_folder_finds_no_repository(tmp_path):
    folder = tmp_path / "site"
    folder.mkdir()
    environment = claude_code_provider.claude_code_environment(None, folder)
    assert environment["GIT_DIR"] == os.devnull
    assert environment["GIT_CEILING_DIRECTORIES"] == str(folder.resolve().parent)
    assert "GIT_DIR" not in claude_code_provider.claude_code_environment()
