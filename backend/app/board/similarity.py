"""Whether a new card says the same as a card on the board: a word overlap of titles and text, pure.

The threshold is tuned on a small table of pairs (duplicates in other words, and different work in the same words):
the titles are compared, and the titles with their descriptions; the higher overlap counts. Two cards about the same
file but different work stay under the threshold, a reworded title of the same work reaches it.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

# A score at or over this is "similar": the agent is shown the card before it adds another.
SIMILAR_THRESHOLD = 0.55
# The text read of a description; enough for its gist, cheap to compare.
MAX_COMPARED_CHARS = 2000
STOP_WORDS = frozenset(
    "a an and are as at be by do does for from has have in into is it its not of on or so that the this to when "
    "with without should must can cannot will would we our you your there then than also only all any after before "
    "add adds added make makes new use uses using".split()
)
WORD = re.compile(r"[a-z0-9]+")
ENDINGS = ("ing", "ed", "es", "s")


@dataclass(frozen=True)
class CardText:
    title: str
    description: str = ""


def stem(word: str) -> str:
    """A crude stem: a plural or verb ending off a longer word, so "fails", "failed" and "failing" meet."""
    for ending in ENDINGS:
        if len(word) > len(ending) + 3 and word.endswith(ending):
            return word[: -len(ending)]
    return word


def words(text: str) -> frozenset[str]:
    return frozenset(stem(word) for word in WORD.findall(text.lower()) if len(word) > 1 and word not in STOP_WORDS)


def dice(first: frozenset[str], second: frozenset[str]) -> float:
    if not first or not second:
        return 0.0
    return 2 * len(first & second) / (len(first) + len(second))


def similarity(new: CardText, existing: CardText) -> float:
    """0 to 1: the overlap of the titles, or of title and description together when that is higher (a reworded title
    whose descriptions tell the same)."""
    title = dice(words(new.title), words(existing.title))
    whole = dice(
        words(f"{new.title} {new.description[:MAX_COMPARED_CHARS]}"),
        words(f"{existing.title} {existing.description[:MAX_COMPARED_CHARS]}"),
    )
    return max(title, whole)


def is_similar(score: float) -> bool:
    return score >= SIMILAR_THRESHOLD


def title_score(new: CardText, title: str) -> float:
    """The title overlap alone, to pick which cards' descriptions are worth reading."""
    return dice(words(new.title), words(title))


def most_similar(
    new: CardText, candidates: Iterable[tuple[int, CardText]], limit: int
) -> Sequence[tuple[int, float]]:
    """The ids of the similar candidates with their score, the most similar first, at most `limit`."""
    scored = [(card_id, similarity(new, text)) for card_id, text in candidates]
    similar = [(card_id, score) for card_id, score in scored if is_similar(score)]
    return sorted(similar, key=lambda item: (-item[1], item[0]))[:limit]
