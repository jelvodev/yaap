from datetime import UTC, datetime, timedelta

import pytest

from app.agent.outputs import OutputStore
from app.core.token_usage import (
    ReplySpan,
    TokenRow,
    ToolResultTokens,
    UsageFacts,
    file_reads,
    hit_rate,
    merged_main,
    read_path,
    reply_gaps,
    session_tokens,
    top_results,
    total_row,
)
from app.providers.base import TokenUsage
from app.storage.models import AgentRunRecord, Message, RunStep, UsageRecord
from app.tracing.steps import StepView
from tests.test_delegation import project_conversation

START = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)


def row(read: int = 0, write: int = 0, fresh: int = 0, out: int = 0, cost: float | None = None) -> TokenRow:
    return TokenRow("reply", "Reply", None, None, None, read, write, fresh, out, cost)


def usage_facts(message_id: int | None, fresh: int, out: int = 0, cost: float | None = None) -> UsageFacts:
    return UsageFacts(message_id, None, fresh, out, 0, 0, cost, START)


def message(conversation_id: int) -> Message:
    return Message(
        conversation_id=conversation_id, parent_id=None, role="assistant", content=[{"type": "text", "text": "x"}],
        status="complete",
    )  # fmt: skip


def run_record(conversation_id: int, message_id: int | None, parent: int | None, minutes: int) -> AgentRunRecord:
    return AgentRunRecord(
        environment_id=1,
        conversation_id=conversation_id,
        model="m",
        status="complete",
        message_id=message_id,
        parent_run_id=parent,
        task="Create hello.txt" if parent is not None else None,
        started_at=START + timedelta(minutes=minutes),
        finished_at=START + timedelta(minutes=minutes, seconds=30),
    )


def usage_record(
    conversation_id: int, purpose: str, message_id: int | None = None, task: str | None = None, **tokens: float
) -> UsageRecord:
    return UsageRecord(
        environment_id=1, conversation_id=conversation_id, message_id=message_id, provider="p", model="m",
        purpose=purpose, task=task, created_at=START, **tokens,
    )  # fmt: skip


def result(step_id: int, tokens: int) -> ToolResultTokens:
    return ToolResultTokens(step_id, 1, 1, "run_command", None, tokens)


def tool_step(step_id: int, tool: str, summary: str | None) -> StepView:
    return StepView(
        step_id, 1, step_id, "tool", START, 0, None, TokenUsage(), None, None, None, tool, summary, None, False, None
    )


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([], None),
        ([row(out=500)], None),
        ([row(read=0, write=100, fresh=100)], 0.0),
        ([row(read=300, write=100, fresh=100)], 0.6),
        ([row(read=100), row(read=100, write=200)], 0.5),
    ],
)
def test_hit_rate_is_cache_reads_over_all_prompt_tokens_and_none_without_any(rows, expected):
    rate = hit_rate(rows)
    assert rate is None if expected is None else rate == pytest.approx(expected)


def test_top_results_are_the_ten_largest_largest_first_with_ties_in_step_order():
    sizes = [5, 90, 7, 90, 1, 2, 3, 4, 6, 8, 9, 10, 11]
    results = [result(step, tokens) for step, tokens in enumerate(sizes, start=1)]
    top = top_results(results)
    assert [(item.step_id, item.tokens) for item in top[:3]] == [(2, 90), (4, 90), (13, 11)]
    assert len(top) == 10 and min(item.tokens for item in top) == 4


@pytest.mark.parametrize(
    ("costs", "expected"),
    [
        ([None, None], None),
        ([None, 0.5], 0.5),
        ([0.25, 0.5], 0.75),
        ([], None),
    ],
)
def test_totals_cost_stays_none_when_no_row_has_one_and_sums_the_rest_otherwise(costs, expected):
    total = total_row(row(fresh=10, cost=cost) for cost in costs)
    assert total.cost == expected and total.label == "Session" and total.input_tokens == 10 * len(costs)
    assert total.kind == "session"


@pytest.mark.parametrize(
    ("summary", "path"),
    [
        ('{"path":"src/api.py"}', "src/api.py"),
        ('{"path":"src/api.py","offset":40,"limit":80}', "src/api.py"),
        ('{"offset":1,"path":"a/b.py","outline":tr…', "a/b.py"),
        ("read the file", None),
        (None, None),
    ],
)
def test_the_path_of_a_read_comes_from_the_steps_summary_even_when_it_was_cut(summary, path):
    assert read_path(summary) == path


def test_file_reads_count_only_read_file_steps_per_path():
    steps = [
        tool_step(1, "read_file", '{"path":"a.py"}'),
        tool_step(2, "read_file", '{"path":"a.py","offset":10}'),
        tool_step(3, "search", '{"path":"a.py"}'),
        tool_step(4, "read_file", '{"path":"b.py"}'),
    ]
    assert file_reads(steps) == {"a.py": 2, "b.py": 1}


def test_gaps_are_the_pause_after_the_previous_reply_and_skip_replies_without_times():
    end = START + timedelta(minutes=1)
    spans = [
        ReplySpan(START, end, 10, 5),
        ReplySpan(end + timedelta(minutes=10), end + timedelta(minutes=11), 0, 900),
        ReplySpan(None, None, 1, 1),
        ReplySpan(end + timedelta(minutes=12), None, 7, 7),
    ]
    gaps = reply_gaps(spans)
    assert [(gap.pause_seconds, gap.cache_read_tokens, gap.cache_write_tokens) for gap in gaps] == [(600.0, 0, 900)]


def test_a_reply_with_several_main_records_adds_them_up():
    records = [usage_facts(1, 100, 5, 0.25), usage_facts(1, 40, 7, 0.5), usage_facts(2, 9), usage_facts(None, 3)]
    merged = merged_main(records)

    assert set(merged) == {1, 2}
    assert (merged[1].input_tokens, merged[1].output_tokens, merged[1].cost) == (140, 12, 0.75)
    assert merged[2].cost is None


def test_a_worker_and_a_utility_call_are_rows_of_their_own_under_the_right_reply(client, tmp_path):
    _, conversation_id = project_conversation(client)
    with client.app.state.session_factory() as session:
        first, second = (message(conversation_id) for _ in range(2))
        session.add_all([first, second])
        session.flush()
        top = run_record(conversation_id, first.id, None, 0)
        later = run_record(conversation_id, second.id, None, 20)
        session.add_all([top, later])
        session.flush()
        worker = run_record(conversation_id, None, top.id, 1)
        session.add(worker)
        session.flush()
        store = OutputStore(tmp_path, conversation_id)
        number = store.save("x" * 4000)
        read = {"kind": "tool", "tool": "read_file", "summary": '{"path":"a.py"}', "started_at": START}
        session.add_all(
            [
                RunStep(run_id=worker.id, position=1, kind="model", started_at=START, input_tokens=300,
                        output_tokens=40, cost=0.1),
                RunStep(run_id=top.id, position=1, output_ref=number, **read),
                RunStep(run_id=later.id, position=1, **read),
                usage_record(conversation_id, "main", first.id, input_tokens=30, output_tokens=5, cache_read_tokens=100,
                             cost=0.15),
                usage_record(conversation_id, "main", first.id, input_tokens=20, cost=0.05),
                usage_record(conversation_id, "main", second.id, cache_write_tokens=900),
                usage_record(conversation_id, "worker", input_tokens=300, output_tokens=40),
                usage_record(conversation_id, "utility", first.id, "title_and_memory", input_tokens=20,
                             output_tokens=2, cost=0.01),
                usage_record(conversation_id, "utility", task="summary", input_tokens=7),
            ]
        )  # fmt: skip
        session.commit()
        tokens = session_tokens(session, conversation_id, store)
        first_id, second_id, worker_id = first.id, second.id, worker.id

    assert [item.reply.message_id for item in tokens.replies] == [first_id, second_id]
    one, two = tokens.replies
    assert (one.reply.input_tokens, one.reply.cache_read_tokens) == (50, 100)
    workers = [(w.label, w.input_tokens, w.output_tokens, w.cost) for w in one.workers]
    assert workers == [("Create hello.txt", 300, 40, 0.1)]
    assert one.workers[0].run_id == worker_id and one.workers[0].message_id == first_id
    assert [(u.label, u.input_tokens) for u in one.utility] == [("Conversation titles and memory suggestions", 20)]
    assert two.workers == [] and two.utility == []
    assert [(u.label, u.message_id) for u in tokens.other_utility] == [("Summaries", None)]
    # The worker's usage record is not read: 50 + 300 + 20 + 7 uncached input, counted once each.
    assert tokens.totals.input_tokens == 377
    assert tokens.totals.cost == pytest.approx(0.31)
    top_results = [(item.tool, item.tokens, item.message_id) for item in tokens.top_tool_results]
    assert top_results == [("read_file", 1000, first_id)]
    assert tokens.file_reads == {"a.py": 2}
    assert [(gap.pause_seconds, gap.cache_write_tokens) for gap in tokens.gaps] == [(1170.0, 900)]
    assert tokens.cache_hit_rate == pytest.approx(100 / (50 + 100 + 300 + 900))
