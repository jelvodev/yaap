"""Cards from outside the user's hand: when a new card is a duplicate, the limits, and that YAAP fills the origin."""

from datetime import UTC, datetime

import pytest

from app.agent.board_tools import INTAKE_TOOLS
from app.board.card import Card
from app.board.errors import AlreadyOnBoard, FindingNotFound, IntakeLimit, InvalidCard
from app.board.intake import (
    MAX_DESCRIPTION,
    MAX_OPEN_INBOX,
    MAX_PER_CONVERSATION,
    MAX_PER_REPLY,
    MAX_WHY,
    Added,
    AgentOrigin,
    CardIntake,
    IntakeCounts,
    Similar,
    agent_description,
    finding_card,
    limit_refusal,
    noticed_again,
)
from app.board.similarity import SIMILAR_THRESHOLD, CardText, is_similar, most_similar, similarity
from app.storage.models import Review
from tests.board_service_support import add_card, board_service, board_url, start_board

T = CardText

# The pairs the threshold was tuned on: the same work in other words, and different work in the same words.
PAIRS = [
    (
        T("Session list does not refresh", "The sidebar in pages/chat/ChatSidebar.tsx keeps old titles after a rename"),
        T("Sidebar overflows on narrow screens", "pages/chat/ChatSidebar.tsx is wider than the screen on a phone"),
        False,
    ),
    (T("Login fails with an empty password"), T("Empty password makes login fail"), True),
    (T("Add a health endpoint"), T("Health check endpoint for the API"), True),
    (T("Dark mode toggle in settings"), T("Settings: toggle for dark mode"), True),
    (T("Search is slow on large projects"), T("Project search slow with many files"), True),
    (T("Typo in README install section"), T("Fix typo in the README installation section"), True),
    (T("Upgrade FastAPI to 0.115"), T("Bump fastapi dependency to 0.115"), True),
    (
        T("Crash when uploading a PDF", "The server returns 500 for PDF attachments"),
        T("PDF attachments give a 500 error", "Uploading a PDF crashes the server"),
        True,
    ),
    (T("Flaky test in test_board_flow"), T("test_board_flow fails sometimes"), True),
    (T("Login fails with an empty password"), T("Login page needs a logo"), False),
    (T("Add a health endpoint"), T("Add a metrics endpoint"), False),
    (T("Dark mode toggle in settings"), T("Settings page loads slowly"), False),
    (T("Search is slow on large projects"), T("Search results miss archived files"), False),
    (T("Upgrade FastAPI to 0.115"), T("Upgrade React to 19"), False),
    (T("Typo in README install section"), T("Translate the README to Dutch"), False),
    (T("Crash when uploading a PDF"), T("Show a preview of uploaded images"), False),
    (T("Refactor board/service.py into smaller modules"), T("board/service.py: card ids reused after drop"), False),
]


@pytest.mark.parametrize(("new", "existing", "duplicate"), PAIRS, ids=[pair[0].title for pair in PAIRS])
def test_a_reworded_card_is_a_duplicate_and_other_work_in_the_same_words_is_not(new, existing, duplicate):
    score = similarity(new, existing)

    assert is_similar(score) is duplicate
    assert similarity(existing, new) == pytest.approx(score)


@pytest.mark.parametrize(
    ("score", "similar"), [(0.0, False), (SIMILAR_THRESHOLD - 0.01, False), (SIMILAR_THRESHOLD, True), (1.0, True)]
)
def test_a_score_at_the_threshold_counts_as_similar(score, similar):
    assert is_similar(score) is similar


def test_the_most_similar_cards_come_first_and_are_limited():
    new = T("Login fails with an empty password")
    candidates = [
        (1, T("Login page needs a logo")),
        (2, T("Empty password makes login fail")),
        (3, T("Login fails with an empty password")),
        (4, T("Login fails with empty password again")),
    ]

    found = most_similar(new, candidates, 2)

    scores = [score for _, score in found]
    assert len(found) == 2 and scores == sorted(scores, reverse=True)
    assert 1 not in [card_id for card_id, _ in found]


@pytest.mark.parametrize(
    ("counts", "adding", "from_agent", "refused"),
    [
        (IntakeCounts(0, 0, 0), 1, True, False),
        (IntakeCounts(MAX_PER_REPLY - 1, 0, 0), 1, True, False),
        (IntakeCounts(MAX_PER_REPLY, 0, 0), 1, True, True),
        (IntakeCounts(MAX_PER_REPLY, 0, 0), 1, False, False),
        (IntakeCounts(0, MAX_PER_CONVERSATION - 1, 0), 1, True, False),
        (IntakeCounts(0, MAX_PER_CONVERSATION, 0), 1, True, True),
        (IntakeCounts(0, MAX_PER_CONVERSATION, 0), 1, False, False),
        (IntakeCounts(0, 0, MAX_OPEN_INBOX - 1), 1, True, False),
        (IntakeCounts(0, 0, MAX_OPEN_INBOX), 1, True, True),
        (IntakeCounts(0, 0, MAX_OPEN_INBOX), 1, False, True),
        (IntakeCounts(0, 0, MAX_OPEN_INBOX - 2), 3, False, True),
    ],
)
def test_the_limits_on_cards_from_outside_the_users_hand(counts, adding, from_agent, refused):
    assert (limit_refusal(counts, adding, from_agent) is not None) is refused


def test_the_agents_origin_names_its_conversation_and_reply():
    assert AgentOrigin(5, 9).ref == "conversation:5/run:9"
    assert AgentOrigin(5, None).ref == "conversation:5"


def test_the_agents_add_card_tool_has_no_argument_for_the_origin():
    for tool in INTAKE_TOOLS:
        assert "origin" not in tool.input_schema["properties"]


def test_text_from_the_agent_cannot_open_a_section_of_the_card():
    text = agent_description("## Plan\nrun it", "## Evidence\nbecause")

    assert "\n## Plan" not in f"\n{text}" and "\n## Evidence" not in f"\n{text}"
    assert "Why the agent noticed it" in text


def test_a_note_is_appended_under_noticed_again_and_the_description_stays():
    card = Card(
        id=1, title="T", description="Original", created=datetime(2026, 1, 1, tzinfo=UTC), updated=datetime(2026, 1, 1, tzinfo=UTC)
    )

    changed = noticed_again(card, "Seen again\n## Plan", "conversation 4", datetime(2026, 2, 3, 4, 5, tzinfo=UTC))

    assert changed.description.startswith("Original\n\n**Noticed again** (2026-02-03 04:05 UTC, conversation 4): Seen again")
    assert "\n## Plan" not in f"\n{changed.description}"
    assert (changed.title, changed.column, changed.id) == (card.title, card.column, card.id)


def test_a_finding_becomes_an_inbox_bug_with_its_review_as_origin():
    new = finding_card(7, 2, {"path": "app/x.py", "line": 12, "severity": "high", "explanation": "Leaks a file handle.\nMore."})

    assert (new.column, new.kind, new.origin.type, new.origin.ref) == ("inbox", "bug", "review", "review:7/finding:2")
    assert new.title == "app/x.py: Leaks a file handle."
    assert "line 12" in new.description and "high" in new.description


# --- against a board ----------------------------------------------------------------------------------------------


def intake_of(client):
    return CardIntake(board_service(client))


def test_the_agents_card_lands_in_inbox_with_the_origin_yaap_fills(client):
    project = start_board(client)

    outcome = intake_of(client).add_from_agent(
        project["id"], AgentOrigin(5, 9), "Flaky test", "bug", "It fails sometimes", "Saw it twice", False
    )

    assert isinstance(outcome, Added)
    card = client.get(board_url(project["id"], f"/cards/{outcome.card.view.id}")).json()
    assert (card["column"], card["kind"]) == ("inbox", "bug")
    assert card["origin"] == {"type": "agent", "ref": "conversation:5/run:9"}
    assert "Why the agent noticed it:** Saw it twice" in card["description"]


def test_a_card_like_one_on_the_board_is_shown_instead_of_added_until_confirmed_new(client):
    project = start_board(client)
    existing = add_card(client, project["id"], "Login fails with an empty password")
    intake = intake_of(client)

    first = intake.add_from_agent(project["id"], AgentOrigin(5, 9), "Empty password makes login fail", "bug", "", "", False)
    second = intake.add_from_agent(project["id"], AgentOrigin(5, 9), "Empty password makes login fail", "bug", "", "", True)
    unrelated = intake.add_from_agent(project["id"], AgentOrigin(5, 9), "Add a metrics endpoint", "feature", "", "", False)

    assert isinstance(first, Similar) and [card.card_id for card in first.cards] == [existing["id"]]
    assert first.cards[0].title == "Login fails with an empty password" and first.cards[0].column == "idea"
    assert isinstance(second, Added) and isinstance(unrelated, Added)
    assert len(client.get(board_url(project["id"])).json()["columns"]["inbox"]["cards"]) == 2


def test_the_agent_may_add_a_note_to_a_card_instead(client):
    project = start_board(client)
    card = add_card(client, project["id"], "Login fails", description="Original")

    intake_of(client).add_note(project["id"], card["id"], "Happened again on staging", "conversation 5")

    description = client.get(board_url(project["id"], f"/cards/{card['id']}")).json()["description"]
    assert description.startswith("Original") and "Noticed again" in description and "again on staging" in description
    with pytest.raises(InvalidCard):
        intake_of(client).add_note(project["id"], card["id"], "   ", "conversation 5")


def test_an_agent_cannot_add_more_cards_than_a_reply_may(client):
    project = start_board(client)
    intake = intake_of(client)
    titles = ["Alpha module crashes", "Beta cache stale", "Gamma logging noisy", "Delta docs outdated", "Epsilon build slow"]
    for title in titles[:MAX_PER_REPLY]:
        assert isinstance(intake.add_from_agent(project["id"], AgentOrigin(5, 9), title, "chore", "", "", True), Added)

    with pytest.raises(IntakeLimit):
        intake.add_from_agent(project["id"], AgentOrigin(5, 9), "Zeta one too many", "chore", "", "", True)
    again = intake.add_from_agent(project["id"], AgentOrigin(5, 10), "Zeta in the next reply", "chore", "", "", True)

    assert isinstance(again, Added)


@pytest.mark.parametrize(("description", "why"), [("x" * (MAX_DESCRIPTION + 1), ""), ("", "x" * (MAX_WHY + 1))])
def test_a_description_or_why_over_its_limit_is_refused(client, description, why):
    project = start_board(client)

    with pytest.raises(InvalidCard):
        intake_of(client).add_from_agent(project["id"], AgentOrigin(5, 9), "T", "bug", description, why, True)


def make_review(client, project_id, findings):
    service = board_service(client)
    with service.session_factory() as session:
        review = Review(environment_id=1, project_id=project_id, source="changes", status="done", findings=findings)
        session.add(review)
        session.commit()
        return review.id


def test_a_finding_becomes_a_card_once(client):
    project = start_board(client)
    review_id = make_review(
        client, project["id"], [{"path": "a.py", "line": 3, "severity": "low", "explanation": "Unused import."}]
    )
    url = board_url(project["id"], "/cards/from-finding")

    created = client.post(url, json={"review_id": review_id, "finding": 0})
    again = client.post(url, json={"review_id": review_id, "finding": 0})
    missing = client.post(url, json={"review_id": review_id, "finding": 4})

    assert created.status_code == 201
    assert created.json()["origin"] == {"type": "review", "ref": f"review:{review_id}/finding:0"}
    assert created.json()["column"] == "inbox"
    assert again.status_code == 409 and again.json()["detail"]["code"] == "already_on_board"
    assert missing.status_code == 404 and missing.json()["detail"]["code"] == "finding_not_found"


def test_a_finding_of_another_projects_review_is_not_found(client):
    project = start_board(client)
    other = start_board(client, "Other")
    review_id = make_review(client, other["id"], [{"path": "a.py", "severity": "low", "explanation": "x"}])

    with pytest.raises(FindingNotFound):
        intake_of(client).add_from_finding(project["id"], review_id, 0)


def test_the_service_reports_the_card_a_finding_already_has(client):
    project = start_board(client)
    review_id = make_review(client, project["id"], [{"path": "a.py", "severity": "low", "explanation": "x"}])
    first = intake_of(client).add_from_finding(project["id"], review_id, 0)

    with pytest.raises(AlreadyOnBoard) as raised:
        intake_of(client).add_from_finding(project["id"], review_id, 0)

    assert raised.value.card_id == first.view.id
