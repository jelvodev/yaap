"""A second opinion on a reply: the reply's scope, the names an answer holds, the report, the choice of reviewer, the
estimate, and the API from the access check to the stored report."""

import time
from pathlib import Path

import pytest
from sqlalchemy import select

from app.checkpoints.service import CheckpointStore
from app.core.model_choice import ForUtility
from app.providers.catalog import ANTHROPIC_MODELS, OPENAI_MODELS, ModelPricing, PriceTier
from app.providers.fake_provider import ScriptedResponse
from app.reviews.answer import FileMaterial, answer_prompt, named_paths, parse_answer_report
from app.reviews.report import ReportError
from app.reviews.reply_scope import reply_scope
from app.reviews.reviewer import estimate_cost, same_vendor_order, second_opinion_reviewer
from app.storage.models import AgentRunRecord, Conversation, Message, Review, UsageRecord
from tests.test_delegation import project_conversation
from tests.test_reviews import (
    CHEAPEST_OPENAI,
    Backend,
    add_keys,
    set_setting,
    two_vendor_registry,
    write_in_conversation,
)

ANSWER_REPORT = (
    '{"agrees": false, "summary": "The answer misreads the file.", "points": [{"severity": "high", '
    '"explanation": "hello.py has no greet function.", "path": "hello.py", "line": 1}, '
    '{"severity": "low", "explanation": "Too long.", "path": null}], "alternative": "Say that hello.py only prints."}'
)


# --- Names in an answer, pure ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("See `app/main.py` and `README.md`.", ["app/main.py", "README.md"]),
        ("Look in src/utils/helpers.ts:42 for it.", ["src/utils/helpers.ts"]),
        # Bare words need a folder or a known extension: "e.g." and "Node.js" are no files.
        ("Use it, e.g. in Node.js, or in setup.py.", ["setup.py"]),
        # Never outside the project: absolute, escaping, home-relative, URLs and Windows paths.
        ("`/etc/passwd` `../secret.txt` `~/notes.md` `C:\\x\\y.py` https://example.com/a/b.html `a/../../b.py`", []),
        ("`./pkg/mod.py` twice: `pkg/mod.py`", ["pkg/mod.py"]),
        ("`makefile` and `Dockerfile`", []),
        ("`weird.xyz` is named in backticks", ["weird.xyz"]),
        ("Nothing named here.", []),
        (" ".join(f"`f{index}.py`" for index in range(12)), [f"f{index}.py" for index in range(8)]),
    ],
)
def test_the_files_an_answer_names_are_relative_paths_inside_the_project(answer, expected):
    assert named_paths(answer) == expected


@pytest.mark.parametrize(
    ("text", "agrees", "summary", "points", "alternative"),
    [
        (
            ANSWER_REPORT,
            False,
            "The answer misreads the file.",
            [("hello.py", 1, "high"), ("", None, "low")],
            "Say that hello.py only prints.",
        ),
        ('```json\n{"agrees": true, "summary": "Sound.", "points": []}\n```', True, "Sound.", [], None),
        (
            '{"agrees": "Yes", "points": [{"explanation": "x", "severity": "weird", "line": "7"}]}',
            True,
            "",
            [("", 7, "medium")],
            None,
        ),
        ('{"agrees": "maybe", "points": [{"severity": "high"}], "alternative": "  "}', None, "", [], None),
    ],
)
def test_the_reviewer_answer_is_read_as_an_answer_check(text, agrees, summary, points, alternative):
    report = parse_answer_report(text)
    assert (report.agrees, report.summary, report.alternative) == (agrees, summary, alternative)
    assert [(point.path, point.line, point.severity) for point in report.points] == points


@pytest.mark.parametrize("text", ["Looks right.", '{"points": "none"}', '{"agrees": true,'])
def test_an_answer_check_without_a_report_is_an_error(text):
    with pytest.raises(ReportError):
        parse_answer_report(text)


def test_the_prompt_holds_the_question_the_answer_and_the_files_within_the_limit():
    files = [FileMaterial("a.py", "a" * 30_000), FileMaterial("b.py", "print('b')\n")]
    prompt, notes = answer_prompt("Why " * 3_000, "Because " * 3_000, files)

    assert len(prompt) <= 41_000
    assert '<file path="a.py">' in prompt and '<file path="b.py">' in prompt
    joined = " ".join(notes)
    assert "question was shortened" in joined and "answer was shortened" in joined and "a.py was shortened" in joined
    assert "print('b')" in prompt


def test_a_prompt_without_files_says_so():
    prompt, notes = answer_prompt(None, "Yes.", [])
    assert "(not given)" in prompt and "could be read" in prompt and notes == []


# --- Choosing the reviewer and the estimate -------------------------------------------------------------------------

STRONGEST_ANTHROPIC = same_vendor_order(ANTHROPIC_MODELS, {"anthropic"}, set())[0]
SECOND_ANTHROPIC = same_vendor_order(ANTHROPIC_MODELS, {"anthropic"}, {STRONGEST_ANTHROPIC})[0]


@pytest.mark.parametrize(
    ("written_by", "expected_first"),
    [
        (set(), STRONGEST_ANTHROPIC),
        ({STRONGEST_ANTHROPIC}, SECOND_ANTHROPIC),
        ({model.id for model in ANTHROPIC_MODELS}, None),
    ],
)
def test_the_same_vendor_fallback_is_the_strongest_other_model(written_by, expected_first):
    ordered = same_vendor_order([*ANTHROPIC_MODELS, *OPENAI_MODELS], {"anthropic"}, written_by)
    assert (ordered[0] if ordered else None) == expected_first
    assert not written_by & set(ordered)
    assert all(model.provider == "anthropic" for model in ANTHROPIC_MODELS if model.id in ordered)
    prices = [
        next(model for model in ANTHROPIC_MODELS if model.id == identifier).pricing.standard for identifier in ordered
    ]
    totals = [price.input + price.output for price in prices]
    assert totals == sorted(totals, reverse=True)


def plan_reviewer(client, registry, vendors, written_by):
    with client.app.state.session_factory() as session:
        return second_opinion_reviewer(session, registry, 1, ForUtility(task="review"), vendors, written_by)


@pytest.mark.parametrize(
    ("keys", "policy", "written_by", "model", "same_vendor", "reason"),
    [
        # Another vendor is allowed: its cheapest model, not marked.
        (("anthropic_api_key", "openai_api_key"), None, ("claude-opus-5-5",), CHEAPEST_OPENAI, False, None),
        # Only the writer's vendor is allowed: its strongest other model, with why.
        (
            ("anthropic_api_key", "openai_api_key"),
            {"connections": ["anthropic:api_key"]},
            (),
            STRONGEST_ANTHROPIC,
            True,
            "model policy",
        ),
        (("anthropic_api_key",), None, (), STRONGEST_ANTHROPIC, True, "No OpenAI API key"),
        # The model that wrote the reply is not asked to judge itself.
        (("anthropic_api_key",), None, (STRONGEST_ANTHROPIC,), SECOND_ANTHROPIC, True, "No OpenAI API key"),
        # Nothing is allowed: no reviewer, and the reasons.
        ((), None, (), None, False, "No OpenAI API key"),
    ],
)
def test_the_reviewer_is_another_vendor_else_the_strongest_other_model_of_the_same_vendor(
    client, keys, policy, written_by, model, same_vendor, reason
):
    add_keys(client, *keys)
    if policy:
        set_setting(client, "model_policy", policy)

    plan = plan_reviewer(client, two_vendor_registry(Backend(), Backend()), {"anthropic"}, written_by)

    assert (plan.choice.model if plan.choice else None) == model and plan.same_vendor is same_vendor
    if reason:
        assert reason in (plan.reason or "")
    else:
        assert plan.reason is None
    if model is None:
        assert "Anthropic" in plan.reason and "same vendor" in plan.reason


PRICING = ModelPricing(standard=PriceTier(input=10.0, output=40.0, cache_read=1.0, cache_write=12.0))


@pytest.mark.parametrize(
    ("chars", "requests", "pricing", "metered", "tokens", "usd"),
    [
        (4000, 1, PRICING, True, (1000, 4000), 0.17),
        (4001, 2, PRICING, True, (1001, 8000), 0.33001),
        (4000, 1, PRICING, False, (1000, 4000), None),
        (4000, 1, None, True, (1000, 4000), None),
        (0, 1, PRICING, True, (0, 4000), 0.16),
    ],
)
def test_the_estimate_is_the_prompt_and_the_longest_reply_at_the_standard_price(
    chars, requests, pricing, metered, tokens, usd
):
    estimate = estimate_cost(chars, requests, 4000, pricing, metered)
    assert (estimate.input_tokens, estimate.output_tokens) == tokens and estimate.usd == usd


# --- The scope of a reply -------------------------------------------------------------------------------------------


def replies_of(client, conversation_id: int) -> list[int]:
    with client.app.state.session_factory() as session:
        return list(
            session.scalars(
                select(AgentRunRecord.message_id)
                .where(AgentRunRecord.conversation_id == conversation_id, AgentRunRecord.message_id.is_not(None))
                .order_by(AgentRunRecord.id)
            )
        )


def scope_of(client, conversation_id: int, message_id: int):
    state = client.app.state
    with state.session_factory() as session:
        store = CheckpointStore(state.session_factory, state.data_dir)
        conversation = session.get_one(Conversation, conversation_id)
        return reply_scope(session, store, state.workspace_dir, conversation, session.get_one(Message, message_id))


def test_a_reply_scope_holds_only_the_changes_of_that_reply(client, fake):
    project, conversation_id = project_conversation(client)
    write_in_conversation(client, fake, conversation_id, "def hello():\n    return 1\n")
    fake.responses.append(
        ScriptedResponse(text="Done.", tool_calls=(("write_file", {"path": "other.py", "content": "y = 2\n"}),))
    )
    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Write another file"})
    fake.responses.append(ScriptedResponse(text="Nothing more to change."))
    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Anything else?"})
    first, second = replies_of(client, conversation_id)[:2]

    only_second = scope_of(client, conversation_id, second)
    only_first = scope_of(client, conversation_id, first)

    assert only_second is not None and only_second.source == "reply" and only_second.message_id == second
    assert "other.py" in only_second.diff and "hello.py" not in only_second.diff
    assert only_second.objective == "Write another file" and only_second.writer_vendors == ("anthropic",)
    assert only_second.project_id == project["id"] and only_second.writer_models
    assert only_first is not None and "hello.py" in only_first.diff and "other.py" not in only_first.diff


def test_a_reply_without_checkpoints_has_no_changes_to_review(client, fake):
    project_conversation(client)
    conversation_id = client.post(
        "/api/v1/environments/1/conversations", json={"model": "claude-opus-5-5", "effort": "medium"}
    ).json()["id"]
    fake.responses.append(ScriptedResponse(text="Just words."))
    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Hello"})
    with client.app.state.session_factory() as session:
        reply = session.scalars(
            select(Message).where(Message.conversation_id == conversation_id, Message.role == "assistant")
        ).one()
    assert scope_of(client, conversation_id, reply.id) is None


# --- Through the app ------------------------------------------------------------------------------------------------


@pytest.fixture
def reviewer(client, fake) -> Backend:
    """A second vendor whose fake backend reviews; the conversation still answers from `fake`."""
    backend = Backend()
    client.app.state.providers = two_vendor_registry(fake, backend)
    return backend


def opinion_url(conversation_id: int, message_id: int) -> str:
    return f"/api/v1/conversations/{conversation_id}/messages/{message_id}/second-opinion"


def last_reply(client, conversation_id: int) -> int:
    with client.app.state.session_factory() as session:
        return session.scalars(
            select(Message.id)
            .where(Message.conversation_id == conversation_id, Message.role == "assistant")
            .order_by(Message.id.desc())
        ).first()


def wait_for_opinion(client, conversation_id: int, seconds: float = 8.0) -> dict:
    deadline = time.monotonic() + seconds
    while True:
        listed = client.get(f"/api/v1/conversations/{conversation_id}/second-opinions").json()
        if listed and listed[0]["status"] != "running":
            return listed[0]
        assert time.monotonic() < deadline, "the second opinion did not end"
        time.sleep(0.05)


def test_the_changes_of_a_reply_get_a_second_opinion_by_another_vendor(client, fake, reviewer):
    add_keys(client, "openai_api_key")
    project, conversation_id = project_conversation(client)
    write_in_conversation(client, fake, conversation_id, "def hello(name):\n    return name\n")
    reply = last_reply(client, conversation_id)
    reviewer.responses.append(ScriptedResponse(text='{"summary": "Fine.", "findings": []}'))

    preview = client.get(opinion_url(conversation_id, reply))
    assert preview.status_code == 200, preview.text
    shown = preview.json()
    assert (shown["kind"], shown["available"], shown["same_vendor"], shown["reason"]) == ("changes", True, False, None)
    assert (shown["reviewer_provider"], shown["reviewer_model"]) == ("openai", CHEAPEST_OPENAI)
    assert shown["estimate"]["input_tokens"] > 0 and shown["estimate"]["output_tokens"] > 0
    assert shown["estimate"]["usd"] is not None and shown["estimate"]["usd"] > 0

    started = client.post(opinion_url(conversation_id, reply), json={})
    assert started.status_code == 201, started.text
    assert (started.json()["source"], started.json()["message_id"]) == ("reply", reply)
    review = wait_for_opinion(client, conversation_id)

    assert review["status"] == "done" and review["project_id"] == project["id"] and review["same_vendor"] is False
    assert review["summary"] == "Fine." and review["agrees"] is None and review["alternative"] is None
    assert "+def hello(name):" in review["diff"]
    assert "+def hello(name):" in reviewer.requests[0].messages[0].content[0]["text"]
    # Not part of the project's own list of reviews, and not added to the conversation.
    assert client.get(f"/api/v1/projects/{project['id']}/reviews").json() == []
    with client.app.state.session_factory() as session:
        assert session.scalars(select(UsageRecord).where(UsageRecord.task == "review")).one().provider == "openai"


def test_an_answer_is_checked_against_the_files_it_names_with_secrets_masked(client, fake, reviewer):
    from app.secrets.service import save_secret

    add_keys(client, "openai_api_key")
    project, conversation_id = project_conversation(client)
    folder = Path(client.app.state.workspace_dir) / project["folder"]
    (folder / "hello.py").write_text("print('hi')  # tok-very-secret-1234\n", encoding="utf-8")
    with client.app.state.session_factory() as session:
        save_secret(session, client.app.state.cipher, 1, project["id"], "API_TOKEN", "tok-very-secret-1234")
    fake.responses.append(ScriptedResponse(text="`hello.py` defines a greet function; see `missing.py` too."))
    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "What does hello.py do?"})
    reply = last_reply(client, conversation_id)
    reviewer.responses.append(ScriptedResponse(text=ANSWER_REPORT))

    assert client.get(opinion_url(conversation_id, reply)).json()["kind"] == "answer"
    started = client.post(opinion_url(conversation_id, reply), json={})
    assert started.status_code == 201, started.text
    assert started.json()["source"] == "answer"
    review = wait_for_opinion(client, conversation_id)

    assert review["status"] == "done" and review["agrees"] is False
    assert review["alternative"] == "Say that hello.py only prints." and review["diff"] == ""
    assert [(item["path"], item["line"], item["severity"]) for item in review["findings"]] == [
        ("hello.py", 1, "high"),
        ("", None, "low"),
    ]
    assert review["high_count"] == 1 and review["objective"] == "What does hello.py do?"
    prompt = reviewer.requests[0].messages[0].content[0]["text"]
    assert "What does hello.py do?" in prompt and "print('hi')" in prompt and "greet function" in prompt
    assert "tok-very-secret-1234" not in prompt and "[secret:API_TOKEN]" in prompt


def test_an_answer_in_a_conversation_without_a_project_is_checked_without_files(client, fake, reviewer):
    from tests.test_chat import add_key

    add_key(client)
    add_keys(client, "openai_api_key")
    conversation_id = client.post(
        "/api/v1/environments/1/conversations", json={"model": "claude-opus-5-5", "effort": "medium"}
    ).json()["id"]
    fake.responses.append(ScriptedResponse(text="Paris is in `france.txt`."))
    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Capital of France?"})
    reply = last_reply(client, conversation_id)
    reviewer.responses.append(ScriptedResponse(text='{"agrees": true, "summary": "Right.", "points": []}'))

    started = client.post(opinion_url(conversation_id, reply), json={})
    assert started.status_code == 201, started.text
    review = wait_for_opinion(client, conversation_id)

    assert review["project_id"] is None and review["agrees"] is True and review["alternative"] is None
    with client.app.state.session_factory() as session:
        stored = session.scalars(select(Review)).one()
    assert (stored.source, stored.message_id, stored.project_id) == ("answer", reply, None)


def test_without_another_vendor_the_same_vendor_needs_the_persons_yes(client, fake):
    project, conversation_id = project_conversation(client)
    client.app.state.providers = two_vendor_registry(fake, Backend())
    fake.responses.append(ScriptedResponse(text="Plain answer."))
    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Question"})
    reply = last_reply(client, conversation_id)
    with client.app.state.session_factory() as session:
        models = session.scalars(select(UsageRecord.model).where(UsageRecord.message_id == reply))
        written_by = set(models)
    expected = same_vendor_order(ANTHROPIC_MODELS, {"anthropic"}, written_by)[0]

    shown = client.get(opinion_url(conversation_id, reply)).json()
    assert shown["available"] is True and shown["same_vendor"] is True and shown["reviewer_model"] == expected
    assert "No OpenAI API key" in shown["reason"]

    refused = client.post(opinion_url(conversation_id, reply), json={})
    assert refused.status_code == 409 and "same vendor" in refused.json()["detail"]
    fake.responses.append(ScriptedResponse(text='{"agrees": true, "summary": "Right.", "points": []}'))
    allowed = client.post(opinion_url(conversation_id, reply), json={"allow_same_vendor": True})
    assert allowed.status_code == 201, allowed.text
    review = wait_for_opinion(client, conversation_id)
    assert review["same_vendor"] is True and review["reviewer_model"] == expected and review["status"] == "done"


def test_when_no_model_can_be_used_the_preview_says_why_and_starting_is_refused(client, fake):
    project, conversation_id = project_conversation(client)
    client.app.state.providers = two_vendor_registry(fake, Backend())
    fake.responses.append(ScriptedResponse(text="Plain answer."))
    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Question"})
    reply = last_reply(client, conversation_id)
    set_setting(client, "model_policy", {"connections": ["openai:api_key"]})

    shown = client.get(opinion_url(conversation_id, reply)).json()
    assert shown["available"] is False and shown["reviewer_model"] is None and shown["estimate"] is None
    assert "No OpenAI API key" in shown["reason"] and "model policy" in shown["reason"]
    refused = client.post(opinion_url(conversation_id, reply), json={"allow_same_vendor": True})
    assert refused.status_code == 409 and "model policy" in refused.json()["detail"]
    assert client.get(f"/api/v1/conversations/{conversation_id}/second-opinions").json() == []


def test_a_second_opinion_is_only_for_an_assistant_reply_of_the_same_conversation(client, fake):
    _, conversation_id = project_conversation(client)
    other_id = client.post(
        "/api/v1/environments/1/conversations", json={"model": "claude-opus-5-5", "effort": "medium"}
    ).json()["id"]
    fake.responses.append(ScriptedResponse(text="Plain answer."))
    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Question"})
    reply = last_reply(client, conversation_id)
    with client.app.state.session_factory() as session:
        question = session.scalars(
            select(Message.id).where(Message.conversation_id == conversation_id, Message.role == "user")
        ).first()

    for conversation, message in ((other_id, reply), (conversation_id, question), (conversation_id, 99_999)):
        assert client.get(opinion_url(conversation, message)).status_code == 404
        assert client.post(opinion_url(conversation, message), json={}).status_code == 404
    assert client.get("/api/v1/conversations/99999/second-opinions").status_code == 404
    assert client.get(f"/api/v1/conversations/{other_id}/second-opinions").json() == []
