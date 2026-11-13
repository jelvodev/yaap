"""Bundling board changes made close together into one commit, and the commit messages ("Board: 3 cards moved,
1 added").

Only a commit that is YAAP's own, young, and nowhere outside this repository may be replaced: rewriting a commit a
remote has (or that YAAP pushed) would make the branch diverge from it."""

import re
from collections.abc import Mapping
from datetime import timedelta
from typing import Literal

BOARD_AUTHOR_NAME = "YAAP board"
BOARD_AUTHOR_EMAIL = "board@yaap.invalid"
BOARD_AUTHOR = f"{BOARD_AUTHOR_NAME} <{BOARD_AUTHOR_EMAIL}>"
SQUASH_WINDOW = timedelta(minutes=2)

Action = Literal["added", "moved", "changed", "dropped", "versions"]
ACTIONS: tuple[Action, ...] = ("added", "moved", "changed", "dropped", "versions")
Tally = Mapping[Action, int]
CARD_ACTIONS: dict[str, Action] = {"added": "added", "moved": "moved", "changed": "changed", "dropped": "dropped"}

PREFIX = "Board: "
PART = re.compile(r"(\d+) (?:cards? )?(added|moved|changed|dropped)")
VERSION_PART = re.compile(r"(\d+) versions? changed")


def should_squash(head_author: str, head_age: timedelta, on_any_remote: bool, pushed_by_yaap: bool) -> bool:
    """Whether a new board change may replace the head commit instead of following it."""
    return (
        head_author == BOARD_AUTHOR
        and timedelta(0) <= head_age < SQUASH_WINDOW
        and not on_any_remote
        and not pushed_by_yaap
    )


def board_message(tally: Tally) -> str:
    """A board commit's message from its counts, for example "Board: 3 cards moved, 1 added, 1 version changed"."""
    parts: list[str] = []
    for action in ACTIONS:
        count = tally.get(action, 0)
        if count <= 0:
            continue
        if action == "versions":
            parts.append(f"{count} {'version' if count == 1 else 'versions'} changed")
        elif not parts:
            parts.append(f"{count} {'card' if count == 1 else 'cards'} {action}")
        else:
            parts.append(f"{count} {action}")
    return PREFIX + (", ".join(parts) if parts else "no changes")


def parse_message(message: str) -> dict[Action, int] | None:
    """The counts of a message `board_message` wrote; None for any other message."""
    first_line = message.strip().split("\n", 1)[0]
    if not first_line.startswith(PREFIX):
        return None
    tally: dict[Action, int] = {}
    for part in first_line.removeprefix(PREFIX).split(", "):
        version = VERSION_PART.fullmatch(part)
        card = PART.fullmatch(part)
        if version is not None:
            tally["versions"] = tally.get("versions", 0) + int(version.group(1))
        elif card is not None:
            action = CARD_ACTIONS[card.group(2)]
            tally[action] = tally.get(action, 0) + int(card.group(1))
        elif part != "no changes":
            return None
    return tally


def squash_message(old: str, new: str) -> str:
    """The message of a commit that replaces one with message `old` by adding the change with message `new`: the
    counts added up. A message YAAP did not write is kept as it is, followed by the new one."""
    old_tally, new_tally = parse_message(old), parse_message(new)
    if old_tally is None or new_tally is None:
        return f"{old.strip()}\n\n{new.strip()}"
    combined = {action: old_tally.get(action, 0) + new_tally.get(action, 0) for action in ACTIONS}
    return board_message(combined)
