"""Bundling board changes: when a head commit may be replaced, and the commit messages."""

from datetime import timedelta

import pytest

from app.board.squash import BOARD_AUTHOR, board_message, parse_message, should_squash, squash_message


@pytest.mark.parametrize(
    ("author", "age", "on_remote", "pushed", "expected"),
    [
        (BOARD_AUTHOR, timedelta(seconds=10), False, False, True),
        (BOARD_AUTHOR, timedelta(0), False, False, True),
        (BOARD_AUTHOR, timedelta(minutes=1, seconds=59), False, False, True),
        (BOARD_AUTHOR, timedelta(minutes=2), False, False, False),
        (BOARD_AUTHOR, timedelta(hours=1), False, False, False),
        (BOARD_AUTHOR, timedelta(seconds=-5), False, False, False),
        (BOARD_AUTHOR, timedelta(seconds=10), True, False, False),
        (BOARD_AUTHOR, timedelta(seconds=10), False, True, False),
        ("Someone <someone@example.com>", timedelta(seconds=10), False, False, False),
        ("YAAP board <other@example.com>", timedelta(seconds=10), False, False, False),
    ],
)
def test_only_a_young_own_unpublished_commit_may_be_replaced(author, age, on_remote, pushed, expected) -> None:
    assert should_squash(author, age, on_remote, pushed) is expected


@pytest.mark.parametrize(
    ("tally", "message"),
    [
        ({}, "Board: no changes"),
        ({"moved": 3, "added": 1}, "Board: 1 card added, 3 moved"),
        ({"moved": 1}, "Board: 1 card moved"),
        ({"added": 2}, "Board: 2 cards added"),
        ({"dropped": 1, "changed": 4}, "Board: 4 cards changed, 1 dropped"),
        ({"versions": 1}, "Board: 1 version changed"),
        ({"versions": 2, "added": 1}, "Board: 1 card added, 2 versions changed"),
        ({"added": 0, "moved": 0}, "Board: no changes"),
    ],
)
def test_a_message_counts_what_changed(tally, message) -> None:
    assert board_message(tally) == message


@pytest.mark.parametrize(
    "tally",
    [{"added": 1}, {"moved": 3, "added": 1}, {"versions": 2}, {"changed": 2, "dropped": 1, "versions": 1}],
)
def test_a_message_can_be_read_back(tally) -> None:
    assert parse_message(board_message(tally)) == tally


@pytest.mark.parametrize("message", ["", "Fix a bug", "Board: stuff happened", "board: 1 added", "Board: 1 flew"])
def test_a_message_yaap_did_not_write_is_not_read(message) -> None:
    assert parse_message(message) is None


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        ("Board: 3 cards moved, 1 added", "Board: 1 card moved", "Board: 1 card added, 4 moved"),
        ("Board: 1 card added", "Board: 1 version changed", "Board: 1 card added, 1 version changed"),
        ("Board: 2 cards added", "Board: 1 card dropped", "Board: 2 cards added, 1 dropped"),
        ("Hand written", "Board: 1 card moved", "Hand written\n\nBoard: 1 card moved"),
        ("Board: 1 card moved", "Hand written\n", "Board: 1 card moved\n\nHand written"),
    ],
)
def test_squashing_adds_the_counts_up_or_keeps_both_messages(old, new, expected) -> None:
    assert squash_message(old, new) == expected
