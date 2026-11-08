"""The Codex connection: OpenAI models through the `codex` CLI signed in with a ChatGPT subscription."""

import asyncio
import base64
import json
import sys

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
    ToolCallFinished,
    ToolCallStarted,
    ToolServer,
)
from app.providers import codex_provider
from app.providers.codex_provider import DISABLED_FEATURES, CodexProvider, plan_turn, read_codex_status
from app.providers.registry import default_provider_registry
from tests.conftest import pass_to_cli
from tests.test_tracing import traced_events

# Stands in for the `codex` binary: records its arguments, stdin and instructions, then prints the scripted JSONL.
FAKE_CODEX = """
import json, os, sys
arguments = sys.argv[1:]
if arguments == ["--version"]:
    print("codex-cli 0.166.0")
    sys.exit(0)
if arguments == ["login", "status"]:
    print(os.environ.get("FAKE_CODEX_LOGIN", "Not logged in"))
    sys.exit(0 if os.environ.get("FAKE_CODEX_LOGIN") else 1)
log = {"argv": arguments, "stdin": sys.stdin.read(), "token": os.environ.get("YAAP_TOOL_TOKEN"),
       "hidden": [key for key in ("YAAP_MASTER_KEY", "OPENAI_API_KEY") if key in os.environ]}
for index, argument in enumerate(arguments):
    if argument == "-c" and arguments[index + 1].startswith("model_instructions_file="):
        path = json.loads(arguments[index + 1].split("=", 1)[1])
        log["instructions"] = open(path, encoding="utf-8").read()
    if argument == "--image":
        log.setdefault("images", []).append(open(arguments[index + 1], "rb").read().decode())
with open(os.environ["FAKE_CODEX_LOG"], "a", encoding="utf-8") as file:
    file.write(json.dumps(log) + "\\n")
with open(os.environ["FAKE_CODEX_OUTPUT"], encoding="utf-8") as file:
    sys.stdout.write(file.read())
sys.exit(int(os.environ.get("FAKE_CODEX_EXIT", "0")))
"""


def user(text: str) -> HistoryMessage:
    return HistoryMessage(role="user", content=[{"type": "text", "text": text}])


def reply(text: str, thread_id: str | None) -> HistoryMessage:
    raw = [{"type": "codex_session", "thread_id": thread_id}] if thread_id else [{"type": "text", "text": text}]
    return HistoryMessage(role="assistant", content=[{"type": "text", "text": text}], provider_raw=raw)


def item(kind: str, **fields) -> dict:
    return {"type": kind, "item": fields}


SUCCESS_LINES = [
    {"type": "thread.started", "thread_id": "thread-1"},
    {"type": "turn.started"},
    item("item.completed", id="r1", type="reasoning", text="The user greets."),
    item("item.started", id="t1", type="mcp_tool_call", server="yaap", tool="read_file", arguments={"path": "a"}, status="in_progress"),
    item(
        "item.completed",
        id="t1",
        type="mcp_tool_call",
        server="yaap",
        tool="read_file",
        arguments={"path": "a"},
        result={"content": [{"type": "text", "text": "file a"}]},
        error=None,
        status="completed",
    ),
    item("item.completed", id="m1", type="agent_message", text="Hallo"),
    item("item.completed", id="m2", type="agent_message", text="Alex"),
    {"type": "turn.completed", "usage": {"input_tokens": 1000, "cached_input_tokens": 800, "output_tokens": 20}},
]


@pytest.fixture
def fake_codex(tmp_path, monkeypatch):
    script = tmp_path / "fake_codex.py"
    script.write_text(FAKE_CODEX, encoding="utf-8")
    log = tmp_path / "log.json"
    output = tmp_path / "output.jsonl"
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log))
    monkeypatch.setenv("FAKE_CODEX_OUTPUT", str(output))
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    pass_to_cli(monkeypatch, codex_provider, "FAKE_CODEX_LOG", "FAKE_CODEX_OUTPUT", "FAKE_CODEX_EXIT", "FAKE_CODEX_LOGIN")

    class FakeCodex:
        command = [sys.executable, str(script)]

        def set_output(self, lines: list[dict], exit_code: int = 0) -> None:
            output.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
            monkeypatch.setenv("FAKE_CODEX_EXIT", str(exit_code))

        def log(self) -> dict:
            return json.loads(log.read_text(encoding="utf-8").splitlines()[-1])

    return FakeCodex()


def run(provider: CodexProvider, messages: list[HistoryMessage], tool_server: ToolServer | None = None) -> list:
    request = AgentRequest(
        model="gpt-6.1-sol",
        effort="high",
        system=[SystemPart("Base rules.")],
        messages=messages,
        tool_server=tool_server,
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


def settings_of(argv: list[str]) -> list[str]:
    return [argv[index + 1] for index, argument in enumerate(argv) if argument == "-c"]


def test_first_turn_starts_a_new_session_and_later_turns_fork():
    assert plan_turn([user("Hallo")]).fork_session_id is None
    turn = plan_turn([user("Hallo"), reply("Hoi", "thread-1"), user("Hoe gaat het?")])
    assert turn.fork_session_id == "thread-1"
    assert turn.text == "Hoe gaat het?"


def test_replies_without_a_session_are_passed_as_a_transcript():
    turn = plan_turn([user("Hallo"), reply("Hoi", None), user("Verder")])
    assert turn.fork_session_id is None
    assert "<assistant>\nHoi\n</assistant>" in turn.text
    assert turn.text.endswith("The user's new message:\n\nVerder")


def test_stream_maps_items_usage_and_session(fake_codex):
    fake_codex.set_output(SUCCESS_LINES)
    events = run(CodexProvider(fake_codex.command), [user("Hallo")])

    assert events[0] == ReasoningDelta("The user greets.\n\n")
    assert events[1] == ToolCallStarted("t1", "read_file", {"path": "a"})
    assert events[2] == ToolCallFinished("t1", "file a", False)
    assert [event.text for event in events if isinstance(event, TextDelta)] == ["Hallo", "\n\nAlex"]
    completed = events[-1]
    assert isinstance(completed, Completed)
    assert completed.billing == "subscription"
    assert completed.provider_raw == [{"type": "codex_session", "thread_id": "thread-1"}]
    assert completed.usage == TokenUsage(input_tokens=200, output_tokens=20, cache_read_tokens=800)

    log = fake_codex.log()
    assert log["argv"][:2] == ["exec", "--json"]
    assert log["argv"][-1] == "-"
    assert log["stdin"] == "Hallo"
    assert log["instructions"] == "Base rules."
    assert log["hidden"] == []
    settings = settings_of(log["argv"])
    assert 'sandbox_mode="read-only"' in settings and 'approval_policy="never"' in settings
    assert 'model_reasoning_effort="high"' in settings
    assert "include_environment_context=false" in settings and "skills.include_instructions=false" in settings
    assert not any(setting.startswith("mcp_servers") for setting in settings)
    disabled = [log["argv"][index + 1] for index, argument in enumerate(log["argv"]) if argument == "--disable"]
    assert disabled == list(DISABLED_FEATURES)


def test_a_later_turn_forks_the_previous_session(fake_codex):
    fake_codex.set_output(SUCCESS_LINES)
    run(CodexProvider(fake_codex.command), [user("Hallo"), reply("Hoi", "thread-0"), user("Verder")])
    assert fake_codex.log()["argv"][:3] == ["exec", "fork", "thread-0"]


def test_tools_come_from_yaap_with_the_token_only_in_the_environment(fake_codex):
    fake_codex.set_output(SUCCESS_LINES)
    run(CodexProvider(fake_codex.command), [user("Hallo")], ToolServer(url="http://127.0.0.1:8080/mcp", token="secret-token"))
    log = fake_codex.log()
    assert log["token"] == "secret-token"
    assert "secret-token" not in json.dumps(log["argv"])
    settings = settings_of(log["argv"])
    assert 'mcp_servers.yaap.url="http://127.0.0.1:8080/mcp"' in settings
    assert 'mcp_servers.yaap.bearer_token_env_var="YAAP_TOOL_TOKEN"' in settings
    assert "mcp_servers.yaap.required=true" in settings


def test_images_are_passed_as_files(fake_codex):
    fake_codex.set_output(SUCCESS_LINES)
    image = {"type": "image", "media_type": "image/png", "data": base64.b64encode(b"png bytes").decode()}
    message = HistoryMessage(role="user", content=[{"type": "text", "text": "Kijk"}, image])
    provider = CodexProvider(fake_codex.command)
    run(provider, [message])
    assert fake_codex.log()["images"] == ["png bytes"]
    assert provider.files == []  # removed again after the run


def test_a_failed_tool_call_is_an_error_result(fake_codex):
    failed = item("item.completed", id="t1", type="mcp_tool_call", tool="write_file", result=None, error={"message": "Refused."}, status="failed")
    fake_codex.set_output([SUCCESS_LINES[0], failed, {"type": "turn.completed", "usage": {}}])
    events = run(CodexProvider(fake_codex.command), [user("Hallo")])
    assert ToolCallFinished("t1", "Refused.", True) in events


def test_signed_out_codex_gives_the_sign_in_command(fake_codex):
    unauthorized = {"type": "error", "message": "Reconnecting... 1/5 (unexpected status 401 Unauthorized: Missing bearer)"}
    fake_codex.set_output([SUCCESS_LINES[0], unauthorized, unauthorized])
    with pytest.raises(ProviderError, match="not signed in.*codex login --device-auth"):
        run(CodexProvider(fake_codex.command), [user("Hallo")])


def test_a_failed_turn_and_a_missing_binary_are_errors(fake_codex, tmp_path):
    fake_codex.set_output([SUCCESS_LINES[0], {"type": "turn.failed", "error": {"message": "model not supported"}}], 1)
    with pytest.raises(ProviderError, match="model not supported"):
        run(CodexProvider(fake_codex.command), [user("Hallo")])
    with pytest.raises(ProviderError, match="not installed"):
        run(CodexProvider([str(tmp_path / "no-such-codex")]), [user("Hallo")])
    status = asyncio.run(read_codex_status([str(tmp_path / "no-such-codex")]))
    assert status.installed is False and status.logged_in is False


def test_status_reads_the_sign_in(fake_codex, monkeypatch):
    signed_out = asyncio.run(read_codex_status(fake_codex.command))
    assert signed_out.installed and not signed_out.logged_in and signed_out.version == "codex-cli 0.166.0"
    monkeypatch.setenv("FAKE_CODEX_LOGIN", "Logged in using ChatGPT")
    signed_in = asyncio.run(read_codex_status(fake_codex.command))
    assert signed_in.logged_in and signed_in.detail == "Logged in using ChatGPT"


def test_codex_is_an_unmetered_openai_connection(client):
    spec = default_provider_registry().find_provider("openai", "codex")
    assert spec is not None and spec.credential_kind is None and not spec.metered and spec.runs_agents
    setting = next(item for item in client.get("/api/v1/settings").json() if item["key"] == "openai_connection")
    assert setting["value"] == "api_key"
    assert [option["value"] for option in setting["options"]] == ["api_key", "codex"]


def test_a_codex_reply_records_what_the_cli_reports(fake_codex):
    fake_codex.set_output(SUCCESS_LINES)
    request = AgentRequest(
        model="gpt-6.1-sol", effort="high", system=[SystemPart("Base rules.")], messages=[user("Hallo")]
    )

    events, recorder = asyncio.run(traced_events(CodexProvider(fake_codex.command), request))

    assert isinstance(events[-1], Completed)
    ((run_id, step),) = recorder.steps
    # Codex gives the turn's totals, not how many model calls it made.
    assert run_id == 7 and step.model == "gpt-6.1-sol" and step.model_calls is None
    assert step.usage == events[-1].usage == TokenUsage(input_tokens=200, output_tokens=20, cache_read_tokens=800)
