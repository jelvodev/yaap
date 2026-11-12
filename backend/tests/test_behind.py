"""The labels behind a reply: pure rows of facts to labels, and the API that serves them."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.core.why import Why
from app.providers.base import TokenUsage
from app.storage.models import AgentRunRecord, Message, RunStep
from app.tracing.behind import ReplyFacts, behind_labels
from app.tracing.notes import ApprovalOutcome, CallWhy, Note, NoteKind, NoteView, note_row
from app.tracing.steps import RunNode, StepView
from tests.test_delegation import project_conversation

START = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)
WHY = Why("Because.", "permission", "access", "project")
ROUTE: dict[str, Any] = {
    "model_name": "Sonnet",
    "kind": "question",
    "role": "main",
    "reason": "A short question.",
    "why": WHY.stored(),
}


def node(run_id: int, children: list[RunNode] | None = None, task: str | None = None) -> RunNode:
    return RunNode(run_id, None, "agent", "m", "complete", task, None, START, START, children or [], "coder")


def model_step(
    step_id: int, run_id: int, cost: float | None = None, usage: TokenUsage | None = None
) -> StepView:
    return StepView(step_id, run_id, step_id, "model", START, 1, "m", usage or TokenUsage(), cost, None, None, None,
                    None, None, False, None)  # fmt: skip


def tool_step(step_id: int, run_id: int, tool: str, approval: ApprovalOutcome | None) -> StepView:
    return StepView(step_id, run_id, step_id, "tool", START, 1, None, TokenUsage(), None, None, None, tool, None,
                    None, False, approval)  # fmt: skip


def note(kind: NoteKind, text: str = "x", run_id: int = 1, step_id: int = 90, **extra: Any) -> NoteView:
    return NoteView(step_id, run_id, step_id, START, Note(kind, text, **extra))


def facts(
    steps: list[StepView] | None = None,
    notes: list[NoteView] | None = None,
    route: dict[str, Any] | None = None,
    root: RunNode | None = None,
    calls: list[CallWhy] | None = None,
) -> ReplyFacts:
    tree = root or node(1)
    by_run: dict[int, list[StepView]] = {}
    for step in steps or []:
        by_run.setdefault(step.run_id, []).append(step)
    return ReplyFacts(route, tree, by_run, notes or [], calls or [])


def kinds(rows: ReplyFacts) -> list[str]:
    return [label.kind for label in behind_labels(rows)]


def only(rows: ReplyFacts):
    (label,) = behind_labels(rows)
    return label


def test_a_reply_without_notes_shows_no_labels_even_with_a_route_and_helpers():
    rows = facts([model_step(1, 1, 0.5)], [], ROUTE, node(1, [node(2)]))

    assert behind_labels(rows) == []


def test_the_route_label_has_the_model_kind_reason_why_and_the_cost_of_the_root_runs_own_steps():
    rows = facts(
        [model_step(1, 1, 0.5, TokenUsage(100, 20)), model_step(2, 1, 0.25, TokenUsage(50, 5)), model_step(3, 2, 9.0)],
        [note("trim")],
        ROUTE,
        node(1, [node(2)]),
    )

    label = behind_labels(rows)[0]

    assert (label.kind, label.text, label.detail, label.why) == ("route", "Sonnet · question", "A short question.", WHY)
    assert label.cost == pytest.approx(0.75) and label.tokens == 175 and label.run_id == 1


def test_there_is_no_route_label_without_a_stored_route():
    assert kinds(facts(notes=[note("trim")])) == ["trim"]


def test_the_memory_label_counts_entries_and_keeps_the_latest_note():
    rows = facts(notes=[note("memory", "first", count=1), note("memory", "second", count=2, items=("a", "b"))])

    label = only(rows)

    assert (label.text, label.items, label.detail) == ("2 memory entries", ("a", "b"), "second")


def test_the_workers_label_adds_up_the_cost_of_each_helpers_whole_subtree():
    grandchild = node(4)
    rows = facts(
        [model_step(1, 2, 0.5), model_step(2, 4, 0.25), model_step(3, 3, None), model_step(4, 1, 7.0)],
        [note("trim")],
        None,
        node(1, [node(2, [grandchild], "Write tests"), node(3, task=None)]),
    )

    label = behind_labels(rows)[0]

    assert label.kind == "workers" and label.text == "2 helpers" and label.run_id == 2
    assert label.cost == pytest.approx(0.75)
    assert label.items == ("coder: Write tests", "coder")


@pytest.mark.parametrize(
    ("approvals", "text"),
    [
        (["approved"], "1 approval"),
        (["approved", "approved"], "2 approvals"),
        (["refused"], "1 refused"),
        (["asked", "asked"], "2 asked"),
        (["declined", "approved", "refused"], "3 approvals"),
    ],
)
def test_the_approvals_label_names_the_outcome_when_all_calls_share_one(approvals, text):
    steps = [tool_step(10 + index, 1, f"tool{index}", outcome) for index, outcome in enumerate(approvals)]
    steps.append(tool_step(99, 1, "free", None))

    labels = {item.kind: item for item in behind_labels(facts(steps, [note("trim")]))}

    assert labels["approvals"].text == text
    assert labels["approvals"].items[0] == f"tool0: {approvals[0]}" and labels["approvals"].step_id == 10


def test_a_call_let_through_by_an_eval_case_approval_asks_nobody_and_adds_no_approvals_label():
    steps = [tool_step(10, 1, "edit_file", "case_approved"), tool_step(99, 1, "free", None)]

    labels = {item.kind: item for item in behind_labels(facts(steps, [note("trim")]))}

    assert "approvals" not in labels


def test_the_approvals_label_takes_its_reason_from_the_first_matching_call():
    calls = [
        CallWhy(50, 1, "other", "k0", "approved", Why("Not this.", "permission")),
        CallWhy(11, 1, "run_command", "k1", "refused", WHY),
    ]
    steps = [tool_step(10, 1, "read_file", None), tool_step(11, 1, "run_command", "refused")]

    labels = {item.kind: item for item in behind_labels(facts(steps, [note("trim")], calls=calls))}

    assert labels["approvals"].why == WHY and labels["approvals"].text == "1 refused"


def test_the_secrets_label_adds_counts_and_lists_each_name_once_never_a_value():
    rows = facts(
        notes=[
            note("secrets_masked", "masked", items=("API_KEY", "TOKEN"), count=2),
            note("secrets_masked", "masked", step_id=91, items=("API_KEY",), count=1),
        ]
    )

    label = only(rows)

    assert (label.text, label.items) == ("3 secrets replaced", ("API_KEY", "TOKEN"))


@pytest.mark.parametrize(
    ("guard_notes", "text"),
    [
        ([note("guard", "Checked.")], "guard: clean"),
        ([note("guard", "Checked."), note("guard", "Suspicious text found.", count=1)], "guard: suspicious"),
        ([note("guard", "Looks suspicious."), note("guard", "Checked.")], "guard: suspicious"),
    ],
)
def test_the_guard_label_says_suspicious_when_any_check_was(guard_notes, text):
    assert only(facts(notes=guard_notes)).text == text


@pytest.mark.parametrize(
    ("kind", "label_kind", "text"),
    [
        ("outgoing_blocked", "outgoing", "output blocked"),
        ("trim", "trim", "older output shortened"),
        ("summary", "summary", "summarised"),
    ],
)
def test_single_note_labels_use_a_fixed_chip_text_and_the_notes_sentence(kind, label_kind, text):
    label = only(facts(notes=[note(kind, "Detail sentence.", why=WHY, cost=0.1)]))

    assert (label.kind, label.text, label.detail, label.why, label.cost) == (label_kind, text, "Detail sentence.", WHY, 0.1)
    assert (label.run_id, label.step_id) == (1, 90)


@pytest.mark.parametrize(
    ("usages", "text"),
    [
        ([TokenUsage(100, 5, 0, 0)], "cache 0%"),
        ([TokenUsage(20, 5, 70, 10), TokenUsage(10, 5, 90, 0)], "cache 80%"),
        ([], "cache"),
        ([TokenUsage(0, 5, 0, 0)], "cache"),
    ],
)
def test_the_cache_label_shows_the_hit_rate_over_all_model_steps(usages, text):
    steps = [model_step(index + 1, 1 + index % 2, None, usage) for index, usage in enumerate(usages)]
    rows = facts(steps, [note("cache", "One hour.", why=WHY)], None, node(1, [node(2)]))

    label = behind_labels(rows)[-1]

    assert (label.kind, label.text, label.why) == ("cache", text, WHY)


def test_labels_come_in_display_order():
    rows = facts(
        [model_step(1, 1, 0.1, TokenUsage(10, 1, 10, 0)), tool_step(2, 1, "t", "approved")],
        [note(kind) for kind in ("cache", "summary", "trim", "outgoing_blocked", "secrets_masked", "guard", "memory")],
        ROUTE,
        node(1, [node(2)]),
    )

    assert kinds(rows) == [
        "route", "memory", "workers", "approvals", "guard", "secrets", "outgoing", "trim", "summary", "cache"
    ]  # fmt: skip


def add_reply(client, conversation_id: int) -> tuple[int, int]:
    """An assistant message with a run that has a model step, a refused tool step and notes."""
    with client.app.state.session_factory() as session:
        message = Message(conversation_id=conversation_id, role="assistant", content=[], route=ROUTE)
        session.add(message)
        session.flush()
        run = AgentRunRecord(
            environment_id=1,
            conversation_id=conversation_id,
            message_id=message.id,
            model="m",
            status="complete",
            started_at=START,
            finished_at=START + timedelta(minutes=1),
        )
        session.add(run)
        session.flush()
        session.add(RunStep(run_id=run.id, position=1, kind="model", started_at=START, duration_ms=1, model="m",
                            input_tokens=10, output_tokens=2, cache_read_tokens=30, cache_write_tokens=0,
                            cost=0.5))  # fmt: skip
        session.add(RunStep(run_id=run.id, position=2, kind="tool", started_at=START, duration_ms=1, tool="run_command",
                            approval="refused", input_tokens=0, output_tokens=0, cache_read_tokens=0,
                            cache_write_tokens=0, detail={"why": WHY.stored(), "call_key": "abc"}))  # fmt: skip
        session.add(note_row(run.id, 3, START, Note("cache", "Kept for an hour.", WHY)))
        session.commit()
        return message.id, run.id


def test_the_behind_api_serves_the_labels_and_the_call_reasons_of_a_reply(client):
    _, conversation_id = project_conversation(client)
    message_id, run_id = add_reply(client, conversation_id)

    response = client.get(f"/api/v1/conversations/{conversation_id}/messages/{message_id}/behind")

    assert response.status_code == 200
    body = response.json()
    assert [label["kind"] for label in body["labels"]] == ["route", "approvals", "cache"]
    route, approvals, cache = body["labels"]
    assert route["text"] == "Sonnet · question" and route["why"]["source"] == "permission"
    assert route["cost"] == 0.5 and route["run_id"] == run_id
    assert approvals["text"] == "1 refused" and approvals["why"]["layer"] == "project"
    assert cache["text"] == "cache 75%"
    assert body["calls"] == [
        {"call_key": "abc", "tool": "run_command", "approval": "refused", "why": WHY.stored()}
    ]


def test_the_behind_api_is_empty_for_a_reply_without_a_run_and_refuses_other_messages(client):
    _, conversation_id = project_conversation(client)
    _, other_id = project_conversation(client)
    with client.app.state.session_factory() as session:
        bare = Message(conversation_id=conversation_id, role="assistant", content=[])
        user = Message(conversation_id=conversation_id, role="user", content=[])
        session.add_all([bare, user])
        session.commit()
        bare_id, user_id = bare.id, user.id
    base = f"/api/v1/conversations/{conversation_id}/messages"

    assert client.get(f"{base}/{bare_id}/behind").json() == {"labels": [], "calls": []}
    assert client.get(f"{base}/{user_id}/behind").status_code == 404
    assert client.get(f"{base}/9999/behind").status_code == 404
    assert client.get(f"/api/v1/conversations/{other_id}/messages/{bare_id}/behind").status_code == 404
