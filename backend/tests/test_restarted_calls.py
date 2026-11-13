"""A model call that broke off and started again leaves only the new attempt's text: in a utility answer and in a
delegated task's report."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.agent.delegation import Delegator, StreamOutcome
from app.core.model_choice import ForUtility, resolve_provider
from app.core.outgoing_checks import outgoing_rules
from app.core.utility import UtilityCall, UtilityContext, ask_utility_model
from app.providers.base import AgentEvent, AgentRequest, CallRestarted, Completed, TextDelta, TokenUsage
from tests.test_chat import add_key

TITLE = "Add demo page"


class RestartingProvider:
    """Streams part of an answer, breaks off, then streams the whole answer again."""

    async def stream(self, request: AgentRequest) -> AsyncIterator[AgentEvent]:
        yield TextDelta("Add dem")
        yield CallRestarted(attempt=2, reason="The connection dropped.")
        yield TextDelta(TITLE)
        yield Completed(stop_reason="end_turn", served_by_model=request.model, usage=TokenUsage(input_tokens=1))

    async def close(self) -> None:
        return None


def test_a_utility_answer_keeps_only_the_text_of_the_new_attempt(client: TestClient) -> None:
    add_key(client)
    state = client.app.state
    context = UtilityContext(state.session_factory, state.cipher, state.credential_types, state.providers)
    with state.session_factory() as session:
        choice = resolve_provider(session, state.providers, 1, ForUtility(task="title"))
        outgoing = outgoing_rules(session, state.cipher, 1, None)
    choice = replace(choice, spec=replace(choice.spec, create=lambda _key: RestartingProvider()))

    answer = asyncio.run(
        ask_utility_model(context, UtilityCall(choice, None, outgoing, task="title"), "Write a title", 60)
    )

    assert answer is not None
    assert answer.text == TITLE


def delegator_stub() -> SimpleNamespace:
    runs = SimpleNamespace(find=lambda _token: object(), finish=lambda _child: None)
    return SimpleNamespace(runs=runs)


def test_a_delegated_report_keeps_only_the_text_of_the_new_attempt() -> None:
    parent = SimpleNamespace(token="parent", events=asyncio.Queue())
    child = SimpleNamespace()
    request = AgentRequest("model", "low", [], [])

    outcome: StreamOutcome = asyncio.run(
        Delegator.stream_sub_run(delegator_stub(), parent, child, "worker", 1, RestartingProvider(), request)
    )

    assert outcome.status == "complete"
    assert "".join(outcome.texts) == TITLE
