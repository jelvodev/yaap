"""Reviews by another vendor: the reviewer's vendor, skipping with a reason, the goal trigger, the
stored report, the bounded prompt, and a project left untouched."""

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.model_choice import ForUtility
from app.core.other_vendor import NoOtherVendor, other_vendor_choice, other_vendor_models
from app.core.settings import SETTING_DEFINITIONS, store_setting
from app.core.utility import SWITCHED_OFF_REASON
from app.credentials.store import save_credential
from app.credentials.types import CredentialTestResult
from app.providers.base import AgentRequest
from app.providers.catalog import ANTHROPIC_MODELS, OPENAI_MODELS
from app.providers.fake_provider import FakeProvider, ScriptedResponse
from app.providers.registry import ProviderRegistry, ProviderSpec
from app.reviews.report import FileDiff, ReportError, parse_report, plan_parts, split_files
from app.secrets.service import save_secret
from app.storage.models import AuditRecord, Review, UsageRecord
from tests.test_delegation import project_conversation
from tests.test_goals import wait_for_goal

CHEAPEST_OPENAI = other_vendor_models(OPENAI_MODELS, "anthropic")[0]
REPORT = (
    '{"summary": "Adds a greeting.", "findings": [{"path": "hello.py", "line": 2, "severity": "high", '
    '"explanation": "The name is never used."}, {"path": "hello.py", "line": null, "severity": "nit", '
    '"explanation": "No docstring."}]}'
)


# --- The report, pure ---------------------------------------------------------------------------------------------


def file_diff(path: str, size: int) -> str:
    body = "+" + "x" * max(0, size - 60) + "\n"
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -0,0 +1 @@\n{body}"


def test_a_diff_is_split_per_file_by_its_new_name():
    deleted = "diff --git a/old.txt b/old.txt\n--- a/old.txt\n+++ /dev/null\n"
    diff = file_diff("a.py", 100) + file_diff("src/b.ts", 100) + deleted
    assert [file.path for file in split_files(diff)] == ["a.py", "src/b.ts", "old.txt"]


def test_a_small_diff_is_reviewed_in_one_part_without_notes():
    parts = plan_parts(split_files(file_diff("a.py", 200) + file_diff("b.py", 200)))
    assert len(parts.parts) == 1 and [file.path for file in parts.parts[0]] == ["a.py", "b.py"]
    assert parts.notes == ()


def test_a_large_diff_is_reviewed_per_file_up_to_a_limit_with_notes():
    files = [FileDiff(f"f{index}.py", file_diff(f"f{index}.py", 600)) for index in range(6)]
    files.insert(1, FileDiff("huge.py", file_diff("huge.py", 5000)))

    parts = plan_parts(files, max_chars=1000, max_parts=3)

    assert [[file.path for file in part] for part in parts.parts] == [["f0.py"], ["huge.py"], ["f1.py"]]
    assert "[The rest of this file's diff is not shown.]" in parts.parts[1][0].text
    assert len(parts.parts[1][0].text) < 1100
    notes = " ".join(parts.notes)
    assert "Shortened" in notes and "huge.py" in notes
    assert "Not reviewed" in notes and "f2.py, f3.py, f4.py, f5.py" in notes
    assert "in 3 parts" in notes


@pytest.mark.parametrize(
    ("answer", "summary", "findings"),
    [
        (REPORT, "Adds a greeting.", [("hello.py", 2, "high"), ("hello.py", None, "low")]),
        ('```json\n{"summary": "Fine.", "findings": []}\n```', "Fine.", []),
        (
            '{"findings": [{"path": "", "line": "7", "severity": "weird", "explanation": "x"}]}',
            "",
            [("(general)", 7, "medium")],
        ),
        (
            '{"findings": [{"path": "a.py", "line": 0, "severity": "Critical", "explanation": "y"}, {"path": "b"}]}',
            "",
            [("a.py", None, "high")],
        ),
    ],
)
def test_the_reviewer_answer_is_read_as_a_report(answer, summary, findings):
    found_summary, found = parse_report(answer)
    assert found_summary == summary
    assert [(item.path, item.line, item.severity) for item in found] == findings


@pytest.mark.parametrize("answer", ["Looks good.", '{"findings": "none"}', '{"summary": "x",'])
def test_an_answer_without_a_report_is_an_error(answer):
    with pytest.raises(ReportError):
        parse_report(answer)


# --- Choosing the reviewer ------------------------------------------------------------------------------------------


@dataclass
class Backend:
    responses: list[ScriptedResponse] = field(default_factory=list)
    requests: list[AgentRequest] = field(default_factory=list)


def two_vendor_registry(writer: Backend, reviewer: Backend) -> ProviderRegistry:
    """Anthropic answers from the conversation's fake backend, OpenAI from the reviewer's."""

    def anthropic(_key: str | None) -> FakeProvider:
        return FakeProvider("claude-opus-5-5", writer.responses, writer.requests)

    def openai(_key: str | None) -> FakeProvider:
        return FakeProvider(CHEAPEST_OPENAI, reviewer.responses, reviewer.requests)

    return ProviderRegistry(
        [
            ProviderSpec("anthropic", "Anthropic", "anthropic_api_key", anthropic, runs_agents=True),
            ProviderSpec("openai", "OpenAI", "openai_api_key", openai, connection="api_key", runs_agents=True),
        ],
        [*ANTHROPIC_MODELS, *OPENAI_MODELS],
    )


def add_keys(client, *kinds: str) -> None:
    ok = CredentialTestResult(True, "The key works.")
    with client.app.state.session_factory() as session:
        for kind in kinds:
            save_credential(session, client.app.state.cipher, 1, kind, "sk-test-valid-key-1234", ok)


def set_setting(client, key: str, value: object) -> None:
    with client.app.state.session_factory() as session:
        store_setting(session, SETTING_DEFINITIONS[key], 1, value)


def choose(client, registry: ProviderRegistry, vendors: set[str]) -> str:
    with client.app.state.session_factory() as session:
        return other_vendor_choice(session, registry, 1, ForUtility(task="review"), vendors).model


@pytest.mark.parametrize(
    ("vendors", "task_model", "expected"),
    [
        # The review task's own utility model when it is of another vendor than the code's.
        ({"openai"}, None, "claude-haiku-5-5"),
        # Of the same vendor: the cheapest allowed model of another vendor.
        ({"anthropic"}, None, CHEAPEST_OPENAI),
        # A model of its own for the review that is of another vendor is used as it is.
        ({"anthropic"}, "gpt-6-astra", "gpt-6-astra"),
        # ... and one of the code's vendor is passed over.
        ({"openai"}, "gpt-6-astra", "claude-haiku-5-5"),
        # Nobody's model wrote the changes: any vendor may review.
        (set(), None, "claude-haiku-5-5"),
    ],
)
def test_the_reviewer_is_of_another_vendor_than_the_code(client, vendors, task_model, expected):
    add_keys(client, "anthropic_api_key", "openai_api_key")
    if task_model:
        set_setting(client, "utility_task_models", {"review": task_model})
    assert choose(client, two_vendor_registry(Backend(), Backend()), vendors) == expected


@pytest.mark.parametrize(
    ("keys", "policy", "reason"),
    [
        (("anthropic_api_key", "openai_api_key"), {"connections": ["anthropic:api_key"]}, "model policy"),
        (("anthropic_api_key",), None, "No OpenAI API key"),
    ],
)
def test_without_an_allowed_model_of_another_vendor_there_is_no_reviewer(client, keys, policy, reason):
    add_keys(client, *keys)
    if policy:
        set_setting(client, "model_policy", policy)
    with pytest.raises(NoOtherVendor) as refused:
        choose(client, two_vendor_registry(Backend(), Backend()), {"anthropic"})
    assert "another vendor than Anthropic" in str(refused.value) and reason in str(refused.value)


# --- Reviews through the app ----------------------------------------------------------------------------------------


@pytest.fixture
def reviewer(client, fake) -> Backend:
    """The app with a second vendor whose fake backend reviews; the conversation still answers from `fake`."""
    backend = Backend()
    client.app.state.providers = two_vendor_registry(fake, backend)
    return backend


def wait_for_review(client, project_id: int, seconds: float = 8.0) -> dict:
    deadline = time.monotonic() + seconds
    while True:
        listed = client.get(f"/api/v1/projects/{project_id}/reviews").json()
        if listed and listed[0]["status"] != "running":
            return client.get(f"/api/v1/projects/{project_id}/reviews/{listed[0]['id']}").json()
        assert time.monotonic() < deadline, "the review did not end"
        time.sleep(0.05)


def write_in_conversation(client, fake, conversation_id: int, content: str) -> None:
    fake.responses.append(
        ScriptedResponse(text="Written.", tool_calls=(("write_file", {"path": "hello.py", "content": content}),))
    )
    response = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Write a greeting"})
    assert response.status_code == 200


def folder_state(folder: Path) -> tuple[dict[str, bytes], str]:
    files = {
        str(path.relative_to(folder)): path.read_bytes()
        for path in sorted(folder.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(folder).parts
    }
    status = subprocess.run(["git", "status", "--porcelain"], cwd=folder, capture_output=True, text=True).stdout
    return files, status


def test_a_conversation_review_is_by_another_vendor_stored_and_bounded(client, fake, reviewer):
    add_keys(client, "openai_api_key")
    project, conversation_id = project_conversation(client)
    with client.app.state.session_factory() as session:
        save_secret(session, client.app.state.cipher, 1, project["id"], "API_TOKEN", "tok-very-secret-1234")
    write_in_conversation(client, fake, conversation_id, "def hello(name):\n    return 'hi tok-very-secret-1234'\n")
    folder = Path(client.app.state.workspace_dir) / project["folder"]
    before = folder_state(folder)
    reviewer.responses.append(ScriptedResponse(text=REPORT))

    started = client.post(f"/api/v1/projects/{project['id']}/reviews", json={"conversation_id": conversation_id})
    assert started.status_code == 201, started.text
    assert started.json()["source"] == "conversation"
    review = wait_for_review(client, project["id"])

    assert review["status"] == "done" and review["reason"] is None
    assert review["writer_vendors"] == ["anthropic"]
    assert (review["reviewer_provider"], review["reviewer_model"]) == ("openai", CHEAPEST_OPENAI)
    assert review["summary"] == "Adds a greeting."
    assert [(item["path"], item["line"], item["severity"]) for item in review["findings"]] == [
        ("hello.py", 2, "high"),
        ("hello.py", None, "low"),
    ]
    assert review["findings_count"] == 2 and review["high_count"] == 1
    # The prompt holds the diff, the objective and the rules; secrets only as placeholders, also in what is stored.
    prompt = reviewer.requests[0].messages[0].content[0]["text"]
    assert "+def hello(name):" in prompt and "Write a greeting" in prompt and "standards" in prompt
    assert "tok-very-secret-1234" not in prompt and "[secret:API_TOKEN]" in prompt
    assert "tok-very-secret-1234" not in review["diff"] and "+def hello(name):" in review["diff"]
    # Recorded like any utility call: usage and the audit log.
    with client.app.state.session_factory() as session:
        usage = session.scalars(select(UsageRecord).where(UsageRecord.task == "review")).one()
        audit = session.scalars(select(AuditRecord).where(AuditRecord.task == "review")).one()
    assert (usage.provider, usage.model, usage.conversation_id) == ("openai", CHEAPEST_OPENAI, conversation_id)
    assert (audit.provider, audit.model) == ("openai", CHEAPEST_OPENAI)
    # Readable later, also per conversation; nothing in the project changed.
    listed = client.get(f"/api/v1/projects/{project['id']}/reviews", params={"conversation_id": conversation_id}).json()
    assert [item["id"] for item in listed] == [review["id"]]
    assert folder_state(folder) == before


def test_when_the_policy_allows_no_other_vendor_the_review_is_skipped_and_says_why(client, fake, reviewer):
    add_keys(client, "openai_api_key")
    project, conversation_id = project_conversation(client)
    write_in_conversation(client, fake, conversation_id, "print('hi')\n")
    set_setting(client, "model_policy", {"connections": ["anthropic:api_key"]})

    client.post(f"/api/v1/projects/{project['id']}/reviews", json={"conversation_id": conversation_id})
    review = wait_for_review(client, project["id"])

    assert review["status"] == "skipped" and review["reviewer_model"] is None
    assert "another vendor than Anthropic" in review["reason"] and "model policy" in review["reason"]
    assert reviewer.requests == []


def test_nothing_to_review_is_refused(client):
    project, conversation_id = project_conversation(client)
    for body in ({}, {"conversation_id": conversation_id}):
        refused = client.post(f"/api/v1/projects/{project['id']}/reviews", json=body)
        assert refused.status_code == 409, refused.text
    assert client.get(f"/api/v1/projects/{project['id']}/reviews").json() == []


def test_the_uncommitted_changes_are_reviewed_without_touching_the_project(client, fake, reviewer):
    from tests.test_chat import add_key

    add_key(client)
    project = client.post("/api/v1/environments/1/projects", json={"name": "Shop"}).json()
    folder = Path(client.app.state.workspace_dir) / project["folder"]
    (folder / "notes.md").write_text("# Notes\n", encoding="utf-8")
    before = folder_state(folder)
    fake.responses.append(ScriptedResponse(text='{"summary": "Notes.", "findings": []}'))

    started = client.post(f"/api/v1/projects/{project['id']}/reviews", json={})
    assert started.status_code == 201, started.text
    review = wait_for_review(client, project["id"])

    # Nobody's model wrote these changes, so the review's own utility model reviews them.
    assert review["status"] == "done" and review["source"] == "changes" and review["writer_vendors"] == []
    assert review["reviewer_model"] == "claude-haiku-5-5" and review["findings"] == []
    assert "+# Notes" in review["diff"] and "+# Notes" in fake.requests[0].messages[0].content[0]["text"]
    assert reviewer.requests == []
    assert folder_state(folder) == before


def goal_reviews(client) -> list[Review]:
    with client.app.state.session_factory() as session:
        return list(session.scalars(select(Review)))


def test_a_goal_that_changed_code_is_reviewed_and_one_that_changed_nothing_is_not(client, fake, reviewer):
    client.app.state.reviews_after_goals = True
    add_keys(client, "openai_api_key")
    project, quiet = project_conversation(client)
    fake.responses.append(
        ScriptedResponse(text="Nothing to do.", tool_calls=(("finish_goal", {"status": "done", "summary": "Fine."}),))
    )
    client.post(f"/api/v1/conversations/{quiet}/messages", json={"text": "/goal check the readme"})
    wait_for_goal(client, quiet, ("done",))
    time.sleep(0.3)
    assert goal_reviews(client) == []

    conversation_id = client.post(
        "/api/v1/environments/1/conversations",
        json={"model": "claude-opus-5-5", "effort": "medium", "project_id": project["id"], "preset": "Balanced"},
    ).json()["id"]
    fake.responses.extend(
        [
            ScriptedResponse(text="Wrote it.", tool_calls=(("write_file", {"path": "hello.py", "content": "x = 1\n"}),)),
            ScriptedResponse(text="Done.", tool_calls=(("finish_goal", {"status": "done", "summary": "Greeting."}),)),
        ]
    )
    reviewer.responses.append(ScriptedResponse(text=REPORT))
    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "/goal add a greeting"})
    goal = wait_for_goal(client, conversation_id, ("done",))
    review = wait_for_review(client, project["id"])

    assert review["source"] == "goal" and review["goal_id"] == goal["id"]
    assert review["conversation_id"] == conversation_id and review["status"] == "done"
    assert review["reviewer_provider"] == "openai" and review["findings_count"] == 2
    assert "add a greeting" in reviewer.requests[0].messages[0].content[0]["text"]
    assert len(goal_reviews(client)) == 1


def test_a_review_the_user_switched_off_is_skipped_without_asking_a_model(client, fake, reviewer):
    add_keys(client, "openai_api_key")
    project, conversation_id = project_conversation(client)
    write_in_conversation(client, fake, conversation_id, "print('hi')\n")
    set_setting(client, "utility_tasks_off", ["review"])

    client.post(f"/api/v1/projects/{project['id']}/reviews", json={"conversation_id": conversation_id})
    review = wait_for_review(client, project["id"])

    assert review["status"] == "skipped" and review["reason"] == SWITCHED_OFF_REASON
    assert reviewer.requests == []
