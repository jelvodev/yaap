"""Utility tasks the model policy skips: the caller that asked hears the policy's reason, the reply stream and the
conversation say which task was skipped, the project panel shows its background tasks, and the audit log filters."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable

import pytest

from app.core.utility import UtilityContext, UtilitySkipped, propose_pull_request
from app.providers.fake_provider import ScriptedResponse
from app.storage.models import AuditRecord
from tests.test_chat import add_key, send, start_conversation
from tests.test_delegation import project_conversation
from tests.test_eval_from_conversation import worked_conversation
from tests.test_memory_consolidation import memory_project
from tests.test_summaries import chat_turns

# The reply's model stays allowed; the utility and worker models are not.
ONLY_THE_REPLY_MODEL = {"value": {"models": ["claude-opus-5-5"]}}
POLICY_REASON = "Refused by the model policy of the environment"


def forbid_utility(client) -> None:
    assert client.put("/api/v1/environments/1/settings/model_policy", json=ONLY_THE_REPLY_MODEL).status_code == 200


def project_with_change(client) -> dict:
    project = client.post("/api/v1/environments/1/projects", json={"name": "Site"}).json()
    folder = Path(client.app.state.workspace_dir) / project["folder"]
    (folder / "index.html").write_text("<h1>Hi</h1>\n", encoding="utf-8")
    return project


def ask_commit_message(client, fake) -> str:
    add_key(client)
    project = project_with_change(client)
    forbid_utility(client)
    response = client.post(f"/api/v1/environments/1/git/repositories/project-{project['id']}/commit-message")
    assert response.status_code == 409
    return response.json()["detail"]


def ask_pull_request(client, fake) -> str:
    add_key(client)
    project = project_with_change(client)
    forbid_utility(client)
    state = client.app.state
    context = UtilityContext(state.session_factory, state.cipher, state.credential_types, state.providers)
    proposal = asyncio.run(
        propose_pull_request(context, 1, "feature", "main", "Add the page", "index.html", project_id=project["id"])
    )
    assert isinstance(proposal, UtilitySkipped)
    return proposal.message


def ask_rule(client, fake) -> str:
    add_key(client)
    conversation_id = start_conversation(client)
    fake.responses.append(ScriptedResponse(text="Ok."))
    send(client, conversation_id, "Always answer briefly.")
    forbid_utility(client)
    message = client.get(f"/api/v1/conversations/{conversation_id}").json()["messages"][0]
    response = client.post(f"/api/v1/conversations/{conversation_id}/messages/{message['id']}/rule-proposal")
    assert response.status_code == 200
    proposal = response.json()
    assert proposal["from_model"] is False and proposal["rule"] == "Always answer briefly."
    return proposal["skipped"]


def ask_summary(client, fake) -> str:
    add_key(client)
    conversation_id = start_conversation(client)
    chat_turns(client, fake, conversation_id, 7)
    forbid_utility(client)
    response = client.post(f"/api/v1/conversations/{conversation_id}/summary")
    assert response.status_code == 409
    return response.json()["detail"]


def ask_eval_case(client, fake) -> str:
    _project, conversation_id, _start = worked_conversation(client, fake)
    forbid_utility(client)
    response = client.post(f"/api/v1/conversations/{conversation_id}/eval-case-proposal")
    assert response.status_code == 200, response.text
    assert response.json()["checks_from"] == "fallback"
    return " ".join(response.json()["notes"])


def ask_contradictions(client, fake) -> str:
    project, _conversation_id = project_conversation(client)
    assert client.post("/api/v1/blueprint/rule", json={"rule": "Indent with tabs.", "apply": True}).status_code == 200
    standard = Path(client.app.state.workspace_dir) / project["folder"] / ".blueprint" / "standards" / "style.md"
    standard.parent.mkdir(parents=True, exist_ok=True)
    standard.write_text("---\nrules:\n- Indent with four spaces.\n---\n\nStyle.\n", encoding="utf-8")
    forbid_utility(client)
    report = client.post(f"/api/v1/projects/{project['id']}/learning/contradictions").json()
    assert report["available"] is False and report["contradictions"] == []
    return report["skipped"]


def ask_memory_tidy(client, fake) -> str:
    project = memory_project(client)
    forbid_utility(client)
    response = client.post(f"/api/v1/projects/{project['id']}/memory/tidy")
    assert response.status_code == 409
    # The Memory tab shows the same skip until a tidy-up runs.
    assert [skip["task"] for skip in client.get(f"/api/v1/projects/{project['id']}/utility-skips").json()] == [
        "memory_tidy"
    ]
    return response.json()["detail"]


@pytest.mark.parametrize(
    ("ask", "label"),
    [
        (ask_commit_message, "commit messages"),
        (ask_pull_request, "pull request texts"),
        (ask_rule, "rules from a message"),
        (ask_summary, "summaries"),
        (ask_eval_case, "eval case proposals"),
        (ask_contradictions, "learning proposals"),
        (ask_memory_tidy, "memory tidying"),
    ],
)
def test_a_task_the_user_asked_for_gives_the_policys_reason_when_skipped(
    client, fake, ask: Callable[..., str], label: str
):
    text = ask(client, fake)

    assert f"No model was asked for {label}." in text
    assert POLICY_REASON in text and "Settings › Models › Policy" in text
    assert "Check the" not in text


def test_the_reply_stream_says_which_utility_task_the_policy_skipped(client, fake):
    add_key(client)
    client.app.state.utility_titles = True
    conversation_id = start_conversation(client)
    forbid_utility(client)
    fake.responses.append(ScriptedResponse(text="Hello there."))

    events = send(client, conversation_id, "Hello")

    names = [name for name, _ in events]
    assert names.index("done") < names.index("utility_skipped")
    (skip,) = [data for name, data in events if name == "utility_skipped"]
    assert (skip["task"], skip["label"]) == ("title", "Conversation titles")
    assert POLICY_REASON in skip["reason"]
    # Opening the conversation shows the same skip, from its audit row.
    assert client.get(f"/api/v1/conversations/{conversation_id}/utility-skips").json() == [skip]

    # The next exchange starts without it: no title is proposed after the first reply.
    fake.responses.append(ScriptedResponse(text="Sure."))
    assert "utility_skipped" not in [name for name, _ in send(client, conversation_id, "Again")]
    assert client.get(f"/api/v1/conversations/{conversation_id}/utility-skips").json() == []


def audit_row(client, project_id: int, task: str, outcome: str, age_minutes: int) -> None:
    with client.app.state.session_factory() as session:
        session.add(
            AuditRecord(
                created_at=datetime.now(UTC) - timedelta(minutes=age_minutes),
                environment_id=1,
                project_id=project_id,
                purpose="utility",
                task=task,
                provider="anthropic",
                connection="api",
                model="claude-haiku-5-5",
                outcome=outcome,
                reason=f"{POLICY_REASON}: no." if outcome == "refused" else None,
            )
        )
        session.commit()


@pytest.mark.parametrize(
    ("attempts", "shown"),
    [
        ([("memory_tidy", "refused")], ["memory_tidy"]),
        ([("memory_tidy", "refused"), ("memory_tidy", "sent")], []),
        ([("memory_tidy", "sent"), ("memory_tidy", "refused")], ["memory_tidy"]),
        ([("learning", "refused"), ("memory_tidy", "refused")], ["memory_tidy", "learning"]),
        ([("commit_message", "refused")], []),
    ],
)
def test_the_project_shows_background_tasks_whose_latest_attempt_was_skipped(
    client, attempts: list[tuple[str, str]], shown: list[str]
):
    project = client.post("/api/v1/environments/1/projects", json={"name": "Site"}).json()
    for number, (task, outcome) in enumerate(attempts):
        audit_row(client, project["id"], task, outcome, age_minutes=len(attempts) - number)

    skips = client.get(f"/api/v1/projects/{project['id']}/utility-skips").json()

    assert [skip["task"] for skip in skips] == shown


@pytest.mark.parametrize(
    ("outcome", "purposes"), [(None, ["utility", "reply"]), ("refused", ["utility"]), ("sent", ["reply"])]
)
def test_the_audit_log_filters_by_outcome(client, fake, outcome: str | None, purposes: list[str]):
    add_key(client)
    conversation_id = start_conversation(client)
    fake.responses.append(ScriptedResponse(text="Hello there."))
    send(client, conversation_id, "Hello")
    ask_commit_message(client, fake)

    params = {"outcome": outcome} if outcome else {}
    entries = client.get("/api/v1/environments/1/audit", params=params).json()["entries"]

    assert [entry["purpose"] for entry in entries] == purposes
    exported = json.loads(client.get("/api/v1/environments/1/audit/export", params={**params, "format": "json"}).text)
    assert len(exported) == len(purposes)
